"""Motor de traducción: transcribe las voces y traduce el texto, línea por línea.

No depende de juegos: trabaja sobre un Project (líneas con archivo de audio o id de plugin), así que
el mismo motor sirve para otras secciones (películas, series) en el futuro.

- Lo ya hecho no se repite: usa la caché y se saltea lo que ya tiene texto o traducción.
- Se puede pausar en cualquier momento; la próxima pasada sigue donde quedó.
- Si la IA corta (internet, límite de uso), reintenta con esperas cada vez más largas.
- Nunca supera el límite de gasto del proyecto (estimado con los precios que cargó la persona).
"""

from __future__ import annotations

import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from revoz.core import project as pj
from revoz.core.cache import Cache, file_hash, translation_key
from revoz.core.providers import Item, Provider, ProviderError, Request

RETRY_WAITS = (2, 5, 15, 30)  # segundos entre reintentos cuando la IA corta
BATCH_CHARS = 2500  # tamaño de cada pedido a la IA (las IA responden mejor con lotes chicos)
BATCH_LINES = 25
VOICE_VERSION = 2  # subirlo cuando cambie cómo se genera o ajusta la voz: las voces viejas se rehacen solas


@dataclass
class Options:
    transcribe: bool = True
    translate: bool = True
    max_lines: int = 0  # 0 = todas las pendientes; > 0 = modo prueba con esa cantidad
    skip_no_voice: bool = True
    whisper_model: str = "base"
    provider: Provider | None = None
    key: str = ""
    model: str = ""
    target: str = "es-419"
    usd_per_million_chars: float = 0.0
    limit: float = 0.0  # 0 = sin límite
    only: list[int] | None = None  # índices de las líneas a considerar, en orden (ej. las que muestra el filtro)
    voice: bool = False  # generar la voz traducida
    tts: dict = field(default_factory=dict)  # nombre -> TTSProvider
    tts_key: str = ""  # clave de ElevenLabs (si se usa)
    dub_dir: Path | None = None  # dónde guardar los audios traducidos del proyecto


@dataclass
class Summary:
    transcribed: int = 0
    translated: int = 0
    no_voice: int = 0
    doubtful: int = 0
    from_cache: int = 0
    errors: int = 0
    voiced: int = 0
    too_long: int = 0  # voces que no entran en el tiempo del original
    spent: float = 0.0
    stopped: str = ""  # "" si terminó; si no, por qué paró ("pausa", "límite", o el error)
    messages: list[str] = field(default_factory=list)


class Engine:
    def __init__(
        self,
        project: pj.Project,
        game_path: Path,
        cache: Cache,
        models_dir: Path,
        plugin=None,
        transcriber_factory=None,
        on_progress=None,  # (hechas, total, mensaje, índice de línea o -1)
        on_save=None,  # se llama después de cada lote para guardar el proyecto
        sleep=None,  # para tests: reemplaza la espera entre reintentos
    ) -> None:
        self.project = project
        self.game_path = game_path
        self.cache = cache
        self.models_dir = models_dir
        self.plugin = plugin
        self.transcriber_factory = transcriber_factory
        self.on_progress = on_progress or (lambda *a: None)
        self.on_save = on_save or (lambda: None)
        self.stop = threading.Event()
        self._sleep = sleep
        self._transcriber = None

    def _wait(self, seconds: float) -> bool:
        """Espera entre reintentos. Devuelve True si mientras tanto se pidió pausar."""
        if self._sleep is None:
            return self.stop.wait(seconds)
        self._sleep(seconds)
        return self.stop.is_set()

    # --- qué falta hacer ---

    def _needs_transcript(self, line: pj.Line) -> bool:
        return not line.original.strip() and not line.no_voice

    def _needs_translation(self, line: pj.Line, opts: Options) -> bool:
        if line.translation.strip() or not line.original.strip():
            return False
        return not (opts.skip_no_voice and line.no_voice)

    def _dub_key(self, line: pj.Line) -> str:
        import hashlib

        text = pj.spoken_text(self.project, line.translation)
        raw = f"v{VOICE_VERSION}|{pj.voice_for(self.project, line)}|{text}|{self.project.natural_length}|{line.clip_start}|{line.clip_end}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _needs_voice(self, line: pj.Line, opts: Options) -> bool:
        if not opts.voice or line.no_voice:
            return False
        will_have_text = bool(line.translation.strip()) or (opts.translate and self._needs_translation(line, opts)) or (
            opts.translate and opts.transcribe and self._needs_transcript(line))  # fmt: skip
        if not will_have_text:
            return False
        return not (line.dub_path and Path(line.dub_path).is_file() and line.dub_key == self._dub_key(line))

    def _select(self, opts: Options) -> list[int]:
        candidates = opts.only if opts.only is not None else range(len(self.project.lines))
        todo = [
            i
            for i in candidates
            if (opts.transcribe and self._needs_transcript(self.project.lines[i]))
            or (opts.translate and self._needs_translation(self.project.lines[i], opts))
            or self._needs_voice(self.project.lines[i], opts)
        ]
        return todo[: opts.max_lines] if opts.max_lines > 0 else todo

    # --- pasada completa ---

    def run(self, opts: Options) -> Summary:
        summary = Summary()
        for line in self.project.lines:  # personajes que falten, por la carpeta del archivo
            if not line.character:
                line.character = pj.guess_character(line.id)
        self._use_plugin_subtitles(summary)
        if opts.voice and not self.project.default_voice:
            self._pick_default_voice(opts, summary)
        selected = self._select(opts)
        to_transcribe = [i for i in selected if opts.transcribe and self._needs_transcript(self.project.lines[i])]
        # Pasos: transcribir cuenta 1 y traducir cuenta 1. Los audios sin voz restan su paso de traducción.
        total = len(to_transcribe) + (len(selected) if opts.translate else 0) + (len(selected) if opts.voice else 0)
        done = 0
        self.on_progress(0, total, "", -1)
        try:
            for n, i in enumerate(to_transcribe, 1):
                if self.stop.is_set():
                    summary.stopped = "pausa"
                    return summary
                line = self.project.lines[i]
                self._transcribe_line(line, opts, summary)
                done += 1
                if opts.translate and not self._needs_translation(line, opts):
                    total -= 1  # sin voz o con error: no hay nada que traducir
                self.on_progress(done, total, line.id, i)
                if n % 25 == 0:
                    self.on_save()
            self.on_save()
            if opts.translate:
                pending = [i for i in selected if self._needs_translation(self.project.lines[i], opts)]
                for batch in self._batches(pending):
                    if self.stop.is_set():
                        summary.stopped = "pausa"
                        return summary
                    if not self._translate_batch(batch, opts, summary):
                        return summary
                    done += len(batch)
                    self.on_progress(min(done, total), total, self.project.lines[batch[-1]].id, batch[-1])
                    self.on_save()
            if opts.voice:
                for i in selected:
                    if self.stop.is_set():
                        summary.stopped = "pausa"
                        return summary
                    line = self.project.lines[i]
                    if self._needs_voice(line, opts) and line.translation.strip():
                        if not self._voice_line(line, opts, summary):
                            return summary
                    done += 1
                    self.on_progress(min(done, total), total, line.id, i)
                    if done % 10 == 0:
                        self.on_save()
            self.on_progress(total, total, "", -1)
        finally:
            self.on_save()
        return summary

    # --- subtítulos del juego (los da un plugin) ---

    def _use_plugin_subtitles(self, summary: Summary) -> None:
        get = getattr(self.plugin, "subtitles", None)
        if not callable(get):
            return
        try:
            subs = get(self.game_path) or {}
        except Exception as e:  # un plugin con errores no frena la traducción
            summary.messages.append(f"El plugin no pudo leer los subtítulos: {type(e).__name__}: {e}")
            return
        for line in self.project.lines:
            text = subs.get(line.id)
            if isinstance(text, str) and text.strip() and not line.original.strip():
                pj.update(line, "original", text.strip())

    # --- transcripción ---

    def _audio_file(self, line: pj.Line, tmp: Path) -> Path | None:
        if line.path and Path(line.path).is_file():
            return Path(line.path)
        if self.plugin is not None and hasattr(self.plugin, "extract"):
            return Path(self.plugin.extract(self.game_path, line.id, tmp))
        return None

    def _transcribe_line(self, line: pj.Line, opts: Options, summary: Summary) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="revoz_") as tmp:
                path = self._audio_file(line, Path(tmp))
                if path is None:
                    raise FileNotFoundError("no se encontró el audio")
                audio_hash = file_hash(path)
                model = f"whisper-{opts.whisper_model}"
                if line.clip_start is not None or line.clip_end is not None:  # otro tramo = otra transcripción
                    model += f"@{line.clip_start or 0:.2f}-{'' if line.clip_end is None else f'{line.clip_end:.2f}'}"
                cached = self.cache.get_transcript(audio_hash, model)
                if cached:
                    summary.from_cache += 1
                    text, _lang, no_voice, doubtful = cached
                else:
                    if self._transcriber is None:
                        self.on_progress(-1, -1, "Cargando el modelo de voz (la primera vez se descarga)…", -1)
                        self._transcriber = self.transcriber_factory(opts.whisper_model, self.models_dir)
                    t = self._transcriber.transcribe(path, line.clip_start, line.clip_end)
                    text, no_voice, doubtful = t.text, t.no_voice, t.doubtful
                    self.cache.put_transcript(audio_hash, model, t.text, t.language, t.no_voice, t.doubtful)
        except Exception as e:  # un audio roto no frena el resto
            line.error = f"{type(e).__name__}: {e}"
            summary.errors += 1
            return
        line.error = ""
        line.no_voice, line.doubtful = no_voice, doubtful
        if text:
            pj.update(line, "original", text)
            summary.transcribed += 1
        summary.no_voice += no_voice
        summary.doubtful += doubtful

    # --- voz ---

    def _pick_default_voice(self, opts: Options, summary: Summary) -> None:
        """Sin voz elegida: usa una voz de Windows del idioma destino (gratis, sin descargar)."""
        from revoz.core.tts import matches_language

        provider = opts.tts.get("Windows")
        try:
            voices = provider.voices() if provider else []
        except Exception:
            voices = []
        regional = ("es-mx", "es-ar", "es-us") if self.project.target == "es-419" else ()
        found = sorted((v for v in voices if matches_language(v.language, self.project.target)), key=lambda v: v.language.lower() not in regional)
        if found:
            self.project.default_voice = found[0].key
            summary.messages.append(f"Voz elegida automáticamente: {found[0].name}. Podés cambiarla con el botón Voces.")
        else:
            summary.messages.append("No hay ninguna voz de Windows en ese idioma. Elegí una con el botón Voces.")

    def _voice_line(self, line: pj.Line, opts: Options, summary: Summary) -> bool:
        """Genera la voz traducida de una línea. Devuelve False si hay que cortar la pasada."""
        import hashlib

        from revoz.core import fit
        from revoz.core.tts import Job, split_key

        voice = pj.voice_for(self.project, line)
        if not voice:
            line.error = "Elegí una voz para este personaje (botón Voces)"
            summary.errors += 1
            return True
        provider_name, voice_id = split_key(voice)
        provider = opts.tts.get(provider_name)
        if provider is None:
            line.error = f"No está disponible la voz {voice}"
            summary.errors += 1
            return True
        text = pj.spoken_text(self.project, line.translation)
        target = pj.target_length(line)

        def speak(speed: float) -> Path:
            """Genera (o toma de la caché) la voz a esa velocidad. Misma voz, texto y velocidad: no se repite ni se paga."""
            path = opts.dub_dir / ".cache" / (hashlib.sha256(f"{voice}|{text}|{speed:.2f}".encode()).hexdigest() + ".wav")
            if path.is_file():
                summary.from_cache += 1
            else:
                provider.synthesize([Job(text, voice_id, path, speed)], opts.tts_key)
            return path

        try:
            spoken = speak(1.0)
            # Si no entra, primero se le pide a la voz que hable más rápido (suena más natural que acelerar el audio)
            if target and not self.project.natural_length:
                ratio = fit.duration(spoken) / target
                if ratio > fit.TOLERANCE:
                    spoken = speak(round(min(ratio, fit.NATIVE_MAX), 2))
            original = Path(line.path) if line.path and Path(line.path).is_file() else None
            fmt = fit.audio_format(original) if original else None
            out = opts.dub_dir / Path(line.id).with_suffix(".wav")
            result = fit.process(
                spoken, out, pj.target_length(line), *(fmt or (None, None)), natural=self.project.natural_length
            )
        except ProviderError as e:
            line.error = str(e)
            summary.errors += 1
            if not e.retry and provider.needs_key:  # clave inválida, etc.: no sigue intentando
                summary.stopped = str(e)
                return False
            return True
        except Exception as e:  # un audio que falla no frena el resto
            line.error = f"{type(e).__name__}: {e}"
            summary.errors += 1
            return True
        line.dub_path, line.dub_key, line.dub_fits, line.error = str(out), self._dub_key(line), result.fits, ""
        summary.voiced += 1
        summary.too_long += not result.fits
        return True

    # --- traducción ---

    def _batches(self, indexes: list[int]) -> list[list[int]]:
        batches, current, size = [], [], 0
        for i in indexes:
            n = len(self.project.lines[i].original)
            if current and (size + n > BATCH_CHARS or len(current) >= BATCH_LINES):
                batches.append(current)
                current, size = [], 0
            current.append(i)
            size += n
        if current:
            batches.append(current)
        return batches

    def _cache_key(self, line: pj.Line, opts: Options) -> str:
        p = self.project
        extra = [p.glossary, p.context, line.character, p.characters.get(line.character, "")]
        return translation_key(line.original, opts.target, opts.provider.name, opts.model, extra)

    def _translate_batch(self, batch: list[int], opts: Options, summary: Summary) -> bool:
        """Traduce un lote. Devuelve False si hay que cortar la pasada (límite, clave inválida, pausa)."""
        lines = self.project.lines
        to_send = []
        for i in batch:
            hit = self.cache.get_translation(self._cache_key(lines[i], opts))
            if hit:
                pj.update(lines[i], "translation", hit)
                lines[i].error = ""
                summary.from_cache += 1
                summary.translated += 1
            else:
                to_send.append(i)
        if not to_send:
            return True

        cost = sum(len(lines[i].original) for i in to_send) / 1_000_000 * opts.usd_per_million_chars
        if opts.limit > 0 and self.project.spent + cost > opts.limit:
            summary.stopped = "límite"
            return False

        request = Request(
            [Item(str(i), lines[i].original, lines[i].character) for i in to_send],
            opts.target,
            dict(self.project.glossary),
            dict(self.project.characters),
            self.project.context,
            opts.model,
        )
        result = None
        for attempt, wait in enumerate((0, *RETRY_WAITS)):
            if wait:
                self.on_progress(-1, -1, "La IA cortó; reintentando en unos segundos…", -1)
                if self._wait(wait):
                    summary.stopped = "pausa"
                    return False
            try:
                result = opts.provider.translate(opts.key, request)
                break
            except ProviderError as e:
                if not e.retry or attempt == len(RETRY_WAITS):
                    summary.stopped = str(e)
                    for i in to_send:
                        lines[i].error = str(e)
                    summary.errors += len(to_send)
                    return False
        self.project.spent += cost
        summary.spent += cost
        for i in to_send:
            text = result.get(str(i), "").strip()
            if text:
                pj.update(lines[i], "translation", text)
                lines[i].error = ""
                self.cache.put_translation(self._cache_key(lines[i], opts), text)
                summary.translated += 1
            else:
                lines[i].error = "La IA no devolvió esta línea; se reintenta en la próxima pasada"
                summary.errors += 1
        return True
