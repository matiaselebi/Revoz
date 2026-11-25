"""Proyecto de traducción de un juego: sus líneas de voz, el texto, la traducción y el estado de revisión.

Se guarda como JSON en el perfil del usuario y se puede exportar/importar en CSV
(para repartir el trabajo entre varias personas o revisar en una planilla).
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from revoz.core import audio, scan

PENDING, TRANSCRIBED, TRANSLATED, REVIEWED = "pendiente", "transcrita", "traducida", "revisada"
STATUSES = [PENDING, TRANSCRIBED, TRANSLATED, REVIEWED]

# Formatos donde cada archivo es una sola línea de voz (los bancos .bnk/.fsb traen muchas juntas).
SINGLE_LINE_FORMATS = scan.READABLE

# ponytail: promedio de habla (~15 caracteres por segundo) para estimar el texto antes de transcribir.
CHARS_PER_SECOND = 15


@dataclass
class Line:
    id: str  # ruta relativa al juego, o el id que da el plugin
    path: str = ""  # archivo para escuchar ("" si el audio está dentro de un paquete)
    duration: float | None = None
    character: str = ""
    original: str = ""
    translation: str = ""
    status: str = PENDING
    no_voice: bool = False  # Whisper no encontró voz (efecto, música, silencio)
    doubtful: bool = False  # Whisper no está seguro de lo que escuchó: revisar a mano
    error: str = ""  # último error al procesarla (se reintenta en la próxima pasada)
    selected: bool = False  # elegida a mano para traducir
    clip_start: float | None = None  # traducir solo desde este segundo (None = desde el principio)
    clip_end: float | None = None  # hasta este segundo (None = hasta el final)
    dub_path: str = ""  # audio con la voz traducida
    dub_key: str = ""  # con qué texto y voz se generó (si cambia, se vuelve a generar)
    dub_fits: bool | None = None  # False si ni acelerando entra en el tiempo del original


def parse_time(text: str) -> float | None:
    """'83.5', '1:23.5' o '0:01:23' -> segundos. Vacío -> None. Lanza ValueError si no se entiende."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    seconds = 0.0
    for part in text.split(":"):
        value = float(part)
        if value < 0:
            raise ValueError(text)
        seconds = seconds * 60 + value
    return seconds


def format_time(seconds: float | None) -> str:
    if seconds is None:
        return ""
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}:{secs:04.1f}"


def set_clip(line: "Line", start: float | None, end: float | None) -> None:
    """Cambia el tramo a traducir. Si cambia, lo ya transcrito y traducido deja de valer: la línea vuelve a empezar."""
    if start is not None and end is not None and end <= start:
        raise ValueError("'Hasta' tiene que ser mayor que 'Desde'")
    if line.duration is not None and start is not None and start >= line.duration:
        raise ValueError("'Desde' está después del final del audio")
    if (start, end) == (line.clip_start, line.clip_end):
        return
    line.clip_start, line.clip_end = start, end
    line.original = line.translation = line.error = line.dub_path = line.dub_key = ""
    line.dub_fits = None
    line.no_voice = line.doubtful = False
    line.status = PENDING


@dataclass
class Project:
    appid: str
    game: str
    language: str = ""  # nombre para mostrar, ej. "Español (Latinoamérica) (es-419)"
    provider: str = ""
    lines: list[Line] = field(default_factory=list)
    target: str = "es-419"  # código del idioma destino
    model: str = ""  # modelo de la IA para traducir
    whisper_model: str = "base"
    glossary: dict[str, str] = field(default_factory=dict)  # término -> traducción ("" = dejarlo igual)
    characters: dict[str, str] = field(default_factory=dict)  # personaje -> descripción para la IA
    context: str = ""  # de qué trata el juego, tono
    spent: float = 0.0  # US$ gastados en este proyecto (estimado con los precios cargados)
    limit: float = 0.0  # US$ máximo para este proyecto (0 = sin límite)
    export_path: str = ""  # dónde exportar el resultado al terminar ("" = no exportar)
    default_voice: str = ""  # voz para los personajes sin voz propia, ej. "Windows:Microsoft Raul"
    voices: dict[str, str] = field(default_factory=dict)  # personaje -> voz
    natural_length: bool = False  # True = no acelerar la voz aunque dure más que el original
    pronunciations: dict[str, str] = field(default_factory=dict)  # palabra -> cómo tiene que decirla la voz
    export_audio_dir: str = ""  # carpeta donde copiar los audios traducidos al terminar


# Carpetas que no dicen quién habla (para adivinar el personaje por la ruta del archivo)
_NOT_CHARACTERS = {
    "sound", "sounds", "audio", "vo", "voice", "voices", "speech", "dialog", "dialogue", "dialogues", "sfx",
    "media", "streams", "data", "assets", "resources", "localization", "loc", "lang", "languages", "game",
    "en", "english", "es", "spanish", "fr", "french", "de", "german", "it", "italian", "pt", "ru", "pl",
    "wav", "ogg", "mp3", "valve", "music", "ambient", "ambience",
}  # fmt: skip


def guess_character(line_id: str) -> str:
    """Adivina quién habla por la carpeta del archivo: "valve/sound/scientist/hola.wav" -> "scientist"."""
    for part in reversed(line_id.replace("\\", "/").split("/")[:-1]):
        name = part.lower()
        if name in _NOT_CHARACTERS or name.isdigit() or "_" in name and name.split("_")[-1] in _NOT_CHARACTERS:
            continue
        return part
    return ""


def build_lines(game_path: Path, plugin=None) -> list[Line]:
    """Lista las líneas de voz: las da el plugin si hay uno; si no, los audios sueltos del juego."""
    if plugin is not None:
        return [Line(str(i), character=guess_character(str(i))) for i in plugin.list_audio(game_path)]
    lines = []
    for dirpath, _, names in os.walk(game_path, onerror=lambda e: None):
        for n in names:
            p = Path(dirpath, n)
            hit = scan.identify(p)
            if hit and hit[0] in SINGLE_LINE_FORMATS:
                rel = p.relative_to(game_path).as_posix()
                lines.append(Line(rel, str(p), audio.duration(p), character=guess_character(rel)))
    return sorted(lines, key=lambda line: line.id.lower())


def update(line: Line, field_name: str, value: str) -> None:
    """Cambia un campo y avanza el estado solo: con texto original pasa a transcrita, con traducción a traducida."""
    setattr(line, field_name, value)
    if field_name == "original" and value.strip() and line.status == PENDING:
        line.status = TRANSCRIBED
    elif field_name == "translation" and value.strip() and line.status in (PENDING, TRANSCRIBED):
        line.status = TRANSLATED


def progress(lines: list[Line]) -> dict[str, int]:
    """Cuántas líneas llegaron al menos a cada estado."""
    rank = {s: i for i, s in enumerate(STATUSES)}
    return {s: sum(rank.get(line.status, 0) >= rank[s] for line in lines) for s in STATUSES[1:]}


@dataclass
class Estimate:
    minutes: float
    without_duration: int
    chars: int
    cost: float


def estimate(lines: list[Line], usd_per_minute: float, usd_per_million_chars: float) -> Estimate:
    seconds = sum(line.duration or 0 for line in lines)
    without = sum(line.duration is None for line in lines)
    chars = sum(len(line.original) if line.original else int((line.duration or 0) * CHARS_PER_SECOND) for line in lines)
    cost = seconds / 60 * usd_per_minute + chars / 1_000_000 * usd_per_million_chars
    return Estimate(seconds / 60, without, chars, cost)


# --- guardar / cargar ---


def load(file: Path) -> Project | None:
    try:
        d = json.loads(file.read_text("utf-8"))
        lines = [Line(**{k: v for k, v in x.items() if k in Line.__dataclass_fields__}) for x in d.pop("lines")]
        return Project(**{k: v for k, v in d.items() if k in Project.__dataclass_fields__}, lines=lines)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save(file: Path, project: Project) -> None:
    # Se escribe a un archivo temporal y después se reemplaza: si se corta la luz no se pierde el proyecto.
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(project), ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, file)


# --- CSV ---

CSV_COLUMNS = ["id", "personaje", "original", "traduccion", "estado", "duracion"]


def _safe_cell(text: str) -> str:
    # Evita que Excel/Sheets ejecute una "fórmula" si un texto empieza con = + - @.
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def export_csv(file: Path, lines: list[Line]) -> None:
    with open(file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for line in lines:
            dur = f"{line.duration:.2f}" if line.duration is not None else ""
            w.writerow([_safe_cell(x) for x in (line.id, line.character, line.original, line.translation, line.status)] + [dur])


def export_txt(file: Path, lines: list[Line]) -> None:
    """Texto fácil de leer: archivo, personaje, original y traducción de cada línea."""
    blocks = []
    for line in lines:
        head = line.id + (f"  [{line.character}]" if line.character else "")
        if line.clip_start is not None or line.clip_end is not None:
            head += f"  ({format_time(line.clip_start or 0)} - {format_time(line.clip_end) or 'fin'})"
        body = [head, "  " + (line.original or "(sin texto)")]
        if line.translation:
            body.append("  → " + line.translation)
        blocks.append("\n".join(body))
    file.write_text("\n\n".join(blocks) + "\n", "utf-8-sig")


def voice_for(project: "Project", line: Line) -> str:
    return project.voices.get(line.character) or project.default_voice


def spoken_text(project: "Project", text: str) -> str:
    """El texto que se le pasa a la voz: con las palabras reemplazadas según el diccionario de pronunciación."""
    import re

    for word in sorted(project.pronunciations, key=len, reverse=True):  # primero las más largas
        say = project.pronunciations[word]
        if word.strip() and say.strip():
            text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", lambda _m, s=say: s, text, flags=re.IGNORECASE)
    return text


def target_length(line: Line) -> float | None:
    """Cuánto tiempo tiene la voz traducida para entrar: el tramo elegido o el audio entero."""
    if line.duration is None:
        return None
    end = line.clip_end if line.clip_end is not None else line.duration
    return max(0.1, end - (line.clip_start or 0))


def export_audio(folder: Path, lines: list[Line]) -> int:
    """Copia los audios traducidos con la misma ruta que el original (cambiando la extensión a .wav)."""
    import shutil

    count = 0
    for line in lines:
        if line.dub_path and Path(line.dub_path).is_file():
            dst = folder / Path(line.id).with_suffix(".wav")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(line.dub_path, dst)
            count += 1
    return count


def export(file: Path, lines: list[Line]) -> None:
    """Exporta según la extensión: .txt texto legible; cualquier otra, CSV (se abre con Excel)."""
    file.parent.mkdir(parents=True, exist_ok=True)
    (export_txt if file.suffix.lower() == ".txt" else export_csv)(file, lines)


def import_csv(file: Path, lines: list[Line]) -> int:
    """Suma al proyecto lo que trae el CSV (por id). Las celdas vacías no borran nada. Devuelve cuántas líneas cambió."""
    by_id = {line.id: line for line in lines}
    changed = 0
    with open(file, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            line = by_id.get((row.get("id") or "").removeprefix("'"))
            if not line:
                continue
            before = asdict(line)
            for col, attr in (("personaje", "character"), ("original", "original"), ("traduccion", "translation")):
                if value := (row.get(col) or "").removeprefix("'"):
                    update(line, attr, value)
            if (row.get("estado") or "") in STATUSES:
                line.status = row["estado"]
            changed += asdict(line) != before
    return changed
