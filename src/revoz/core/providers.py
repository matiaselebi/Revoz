"""Proveedores de IA: validan la clave, dicen a qué idiomas traducen y traducen texto.

Los plugins de tipo "provider" heredan de Provider (o de LLMProvider) para sumar más servicios.
La clave viaja solo en cabeceras HTTPS; nunca en la URL ni en mensajes de error.
La única conexión sin HTTPS permitida es a la propia PC (Ollama, que corre en localhost).
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from revoz.version import USER_AGENT
from pathlib import Path

TARGET_LANGUAGES = [
    ("es-419", "Español (Latinoamérica)"),
    ("es", "Español (España)"),
    ("en", "English"),
    ("pt", "Português"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("ru", "Русский"),
    ("pl", "Polski"),
    ("tr", "Türkçe"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("zh", "中文"),
    ("ar", "العربية"),
]
# Nombre en inglés para las instrucciones a los modelos de lenguaje (entienden mejor así)
LANGUAGE_NAMES_EN = {
    "es-419": "Latin American Spanish",
    "es": "Spanish from Spain",
    "en": "English",
    "pt": "Brazilian Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ru": "Russian",
    "pl": "Polish",
    "tr": "Turkish",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Simplified Chinese",
    "ar": "Arabic",
}


class ProviderError(Exception):
    def __init__(self, message: str, retry: bool = False) -> None:
        super().__init__(message)
        self.retry = retry  # True si vale la pena reintentar más tarde (red, límite de uso, servidor caído)


@dataclass
class Item:
    """Una línea para traducir."""

    id: str
    text: str
    character: str = ""


@dataclass
class Request:
    """Todo lo que la IA necesita saber para traducir un lote de líneas."""

    items: list[Item]
    target: str  # código, ej. "es-419"
    glossary: dict[str, str]  # término -> cómo traducirlo ("" = dejarlo igual)
    characters: dict[str, str]  # personaje -> descripción
    context: str  # de qué trata el juego, tono, etc.
    model: str = ""


class Provider:
    name = ""
    needs_key = True
    can_translate = False

    def languages(self, key: str) -> set[str] | None:
        """Valida la clave y devuelve los códigos de idioma destino (minúsculas),
        o None si el servicio traduce cualquier idioma común (modelos de lenguaje)."""
        raise NotImplementedError

    def models(self, key: str) -> list[str]:
        """Modelos disponibles (solo para modelos de lenguaje)."""
        return []

    def default_model(self, models: list[str]) -> str:
        return models[0] if models else ""

    def translate(self, key: str, request: Request) -> dict[str, str]:
        """Devuelve {id: traducción}. Puede devolver menos ids si la IA se saltó alguno."""
        raise ProviderError("Este servicio no traduce texto")


def supports(langs: set[str] | None, code: str) -> bool:
    if langs is None:
        return True
    primary = code.lower().split("-")[0]
    return any(lang.lower().split("-")[0] == primary for lang in langs)


def choose_code(langs: set[str], code: str) -> str:
    """Código del servicio más parecido al pedido: es-419 -> es-419 si existe, si no es."""
    code, primary = code.lower(), code.lower().split("-")[0]
    if code in langs:
        return code
    preferred = {"pt": "pt-br", "en": "en-us", "zh": "zh-hans"}.get(primary)
    if preferred in langs:
        return preferred
    if primary in langs:
        return primary
    return next((lang for lang in sorted(langs) if lang.split("-")[0] == primary), primary)


# --- conexiones ---


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError("Solo se permiten conexiones HTTPS")


def _request(url: str, headers: dict[str, str], body: object | None, timeout: float) -> object:
    _check_url(url)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(20_000_000))
    except urllib.error.HTTPError as e:
        said = _error_message(e)
        # Google, Gemini y xAI responden 400 (no 401) a una clave inválida
        if e.code in (401, 403) or (e.code == 400 and (body is None or "key" in said.lower())):
            raise ProviderError("Clave inválida o sin permisos") from None
        if e.code == 429:
            raise ProviderError("La IA pidió esperar: se llegó al límite de uso", retry=True) from None
        detail = f": {said}" if said else ""
        if e.code >= 500:
            raise ProviderError(f"El servicio respondió con error {e.code}{detail}", retry=True) from None
        raise ProviderError(f"El servicio respondió con error {e.code}{detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise ProviderError("No se pudo conectar con el servicio", retry=True) from None
    except ValueError:
        raise ProviderError("Respuesta inesperada del servicio") from None


def _error_message(e: urllib.error.HTTPError) -> str:
    """El mensaje de error que mandó el servicio (cada uno lo pone en otro lugar), recortado."""
    try:
        data = json.loads(e.read(20_000))
    except Exception:
        return ""
    found = data
    for k in ("error", "message", "detail"):
        if isinstance(found, dict) and k in found:
            found = found[k]
    if isinstance(found, dict):
        found = found.get("message", "")
    if isinstance(found, list):  # Mistral y otros: lista de errores de validación
        found = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in found)
    return str(found or "").strip()[:200]


def get_json(url: str, headers: dict[str, str], timeout: float = 15) -> object:
    return _request(url, headers, None, timeout)


def post_json(url: str, headers: dict[str, str], body: object, timeout: float = 180) -> object:
    return _request(url, headers, body, timeout)


# --- modelos de lenguaje ---

SYSTEM_PROMPT = """You are a professional video game localizer.
Translate each voice line into {language}.
- Keep the meaning, tone and personality of each character. Use natural, spoken language.
- Keep each translation about the same length as the original: it will be dubbed and must fit the same time.
- Follow the glossary exactly. A term with no translation must stay exactly as written.
- Don't add notes, quotes or explanations. Don't merge or split lines.
Answer ONLY with JSON in this exact shape: {{"translations": [{{"id": "...", "text": "..."}}]}}"""


def build_prompt(req: Request) -> tuple[str, str]:
    system = SYSTEM_PROMPT.format(language=LANGUAGE_NAMES_EN.get(req.target, req.target))
    extra = []
    if req.context.strip():
        extra.append("About the game: " + req.context.strip())
    if req.glossary:
        terms = [f"- {src} -> {dst or src}" for src, dst in req.glossary.items()]
        extra.append("Glossary:\n" + "\n".join(terms))
    used = {i.character for i in req.items if i.character}
    descs = [f"- {name}: {req.characters[name]}" for name in sorted(used) if req.characters.get(name)]
    if descs:
        extra.append("Characters:\n" + "\n".join(descs))
    if extra:
        system += "\n\n" + "\n\n".join(extra)
    lines = [{"id": i.id, "character": i.character, "text": i.text} for i in req.items]
    return system, json.dumps({"lines": lines}, ensure_ascii=False)


def parse_translations(text: str, ids: set[str]) -> dict[str, str]:
    """Saca las traducciones del JSON que devolvió la IA (aunque venga con texto alrededor)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # modelos que razonan antes de responder
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ProviderError("La IA no devolvió las traducciones en el formato pedido", retry=True)
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        raise ProviderError("La IA no devolvió las traducciones en el formato pedido", retry=True) from None
    out = {}
    for entry in data.get("translations", []) if isinstance(data, dict) else []:
        if isinstance(entry, dict) and str(entry.get("id")) in ids and isinstance(entry.get("text"), str):
            out[str(entry["id"])] = entry["text"].strip()
    return out


class LLMProvider(Provider):
    """Base de los modelos de lenguaje: arman el pedido igual y solo cambia cómo se envía."""

    can_translate = True
    cheap_hints: tuple[str, ...] = ()  # partes del nombre de los modelos baratos (se eligen por defecto)

    def complete(self, key: str, model: str, system: str, user: str) -> str:
        raise NotImplementedError

    def translate(self, key: str, request: Request) -> dict[str, str]:
        if not request.model:
            raise ProviderError("Elegí un modelo de la IA")
        system, user = build_prompt(request)
        return parse_translations(self.complete(key, request.model, system, user), {i.id for i in request.items})

    def default_model(self, models: list[str]) -> str:
        """El primer tipo de modelo barato de la lista que exista; de ese tipo, la versión más nueva y estable."""
        usable = [m for m in models if chat_model(m)]
        for hint in self.cheap_hints:
            found = sorted(m for m in usable if hint in m.lower())
            stable = [m for m in found if not any(w in m.lower() for w in ("preview", "exp", "beta", "batch"))]
            if stable or found:
                return (stable or found)[-1]
        return usable[0] if usable else (models[0] if models else "")

    def _send(self, send, body: dict) -> object:
        """Envía con temperatura baja (traducciones parejas). Algunos modelos nuevos no la aceptan: se reintenta sin."""
        try:
            return send(body)
        except ProviderError as e:
            if "temperature" not in str(e).lower():
                raise
            return send({k: v for k, v in body.items() if k != "temperature"})


# Palabras de modelos que no son de chat (voz, imágenes, embeddings…): no sirven para traducir
NOT_CHAT = ("embed", "whisper", "tts", "transcribe", "dall-e", "image", "audio", "realtime", "moderation", "search",
            "codex", "davinci", "babbage", "sora", "computer-use", "ocr", "rerank", "guard", "live", "veo", "imagen")


def chat_model(name: str) -> bool:
    return not any(w in name.lower() for w in NOT_CHAT)


class OpenAICompatible(LLMProvider):
    """Modelos de lenguaje con API estilo OpenAI (OpenAI, Mistral, DeepSeek, xAI, Groq, OpenRouter)."""

    def __init__(self, name: str, base_url: str, check_url: str = "", cheap_hints: tuple[str, ...] = ()) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.check_url = check_url or self.base_url + "/models"
        self.cheap_hints = cheap_hints

    def languages(self, key: str) -> set[str] | None:
        get_json(self.check_url, {"Authorization": f"Bearer {key}"})
        return None

    def models(self, key: str) -> list[str]:
        data = get_json(self.base_url + "/models", {"Authorization": f"Bearer {key}"})
        return sorted(str(m["id"]) for m in data.get("data", []) if isinstance(m, dict) and "id" in m and chat_model(str(m["id"])))

    def complete(self, key: str, model: str, system: str, user: str) -> str:
        data = self._send(
            lambda body: post_json(self.base_url + "/chat/completions", {"Authorization": f"Bearer {key}"}, body),
            {"model": model, "temperature": 0.2, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Respuesta inesperada del servicio") from None


class Anthropic(LLMProvider):
    name = "Anthropic"
    cheap_hints = ("haiku",)
    _headers = staticmethod(lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"})

    def languages(self, key: str) -> set[str] | None:
        get_json("https://api.anthropic.com/v1/models", self._headers(key))
        return None

    def models(self, key: str) -> list[str]:
        data = get_json("https://api.anthropic.com/v1/models?limit=100", self._headers(key))
        return [str(m["id"]) for m in data.get("data", []) if isinstance(m, dict) and "id" in m]

    def complete(self, key: str, model: str, system: str, user: str) -> str:
        data = self._send(
            lambda body: post_json("https://api.anthropic.com/v1/messages", self._headers(key), body),
            {"model": model, "max_tokens": 8192, "temperature": 0.2, "system": system, "messages": [{"role": "user", "content": user}]},
        )
        try:
            return "".join(c.get("text", "") for c in data["content"] if c.get("type") == "text")
        except (KeyError, TypeError, AttributeError):
            raise ProviderError("Respuesta inesperada del servicio") from None


class Gemini(LLMProvider):
    name = "Google Gemini"
    cheap_hints = ("flash-lite", "flash")
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def languages(self, key: str) -> set[str] | None:
        get_json(self.BASE + "/models", {"x-goog-api-key": key})
        return None

    def models(self, key: str) -> list[str]:
        data = get_json(self.BASE + "/models?pageSize=200", {"x-goog-api-key": key})
        return sorted(
            str(m["name"]).removeprefix("models/")
            for m in data.get("models", [])
            if isinstance(m, dict) and "generateContent" in m.get("supportedGenerationMethods", []) and chat_model(str(m["name"]))
        )

    def complete(self, key: str, model: str, system: str, user: str) -> str:
        data = post_json(
            f"{self.BASE}/models/{urllib.parse.quote(model)}:generateContent",
            {"x-goog-api-key": key},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
            },
        )
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Respuesta inesperada del servicio") from None


class Ollama(LLMProvider):
    """Modelos de lenguaje que corren en la PC con Ollama (ollama.com): gratis y sin internet."""

    name = "Ollama (local, gratis)"
    needs_key = False
    cheap_hints = ("qwen", "llama", "gemma", "mistral")
    BASE = "http://localhost:11434"

    def _tags(self) -> list[str]:
        try:
            data = get_json(self.BASE + "/api/tags", {}, timeout=5)
        except ProviderError:
            if not self._start_app():
                raise ProviderError("Ollama no está abierto. Instalalo desde ollama.com y abrilo.") from None
            data = get_json(self.BASE + "/api/tags", {}, timeout=5)
        return sorted(str(m["name"]) for m in data.get("models", []) if isinstance(m, dict) and "name" in m)

    def _start_app(self, wait: float = 20) -> bool:
        """Si Ollama está instalado pero cerrado, lo abre y espera a que responda. Devuelve True si quedó andando."""
        app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
        if sys.platform != "win32" or not app.is_file():
            return False
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # sigue abierto aunque se cierre Revoz
        subprocess.Popen([str(app)], creationflags=flags, close_fds=True)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                get_json(self.BASE + "/api/tags", {}, timeout=2)
                return True
            except ProviderError:
                time.sleep(0.5)
        return False

    def languages(self, key: str) -> set[str] | None:
        if not self._tags():
            raise ProviderError("Ollama no tiene modelos. Descargá uno, por ejemplo: ollama pull qwen2.5")
        return None

    def models(self, key: str) -> list[str]:
        return self._tags()

    def complete(self, key: str, model: str, system: str, user: str) -> str:
        data = post_json(
            self.BASE + "/api/chat",
            {},
            {
                "model": model,
                "stream": False,
                "format": "json",
                "think": False,  # los modelos que razonan tardan mucho más y para traducir no hace falta
                "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=600,  # en una PC sin placa de video puede tardar
        )
        try:
            return data["message"]["content"]
        except (KeyError, TypeError):
            raise ProviderError("Respuesta inesperada del servicio") from None


# --- traductores (DeepL y Google): el glosario se respeta marcando los términos que no se traducen ---


def protect_terms(text: str, glossary: dict[str, str], wrap) -> str:
    """Reemplaza cada término del glosario por su traducción fija, marcada para que el traductor no la toque."""
    for src in sorted(glossary, key=len, reverse=True):  # primero los términos más largos
        dst = glossary[src] or src
        text = re.sub(rf"(?<!\w){re.escape(src)}(?!\w)", lambda _m, d=dst: wrap(d), text, flags=re.IGNORECASE)
    return text


class DeepL(Provider):
    name = "DeepL"
    can_translate = True

    @staticmethod
    def _host(key: str) -> str:
        return "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"

    def languages(self, key: str) -> set[str] | None:
        data = get_json(f"https://{self._host(key)}/v2/languages?type=target", {"Authorization": f"DeepL-Auth-Key {key}"})
        return {d["language"].lower() for d in data}

    def translate(self, key: str, request: Request) -> dict[str, str]:
        target = choose_code(self.languages(key) or set(), request.target).upper()
        texts = [protect_terms(html.escape(i.text, quote=False), request.glossary, lambda d: f"<x>{html.escape(d, quote=False)}</x>") for i in request.items]
        body = {"text": texts, "target_lang": target, "tag_handling": "xml", "ignore_tags": ["x"]}
        if request.context.strip():
            body["context"] = request.context.strip()
        data = post_json(f"https://{self._host(key)}/v2/translate", {"Authorization": f"DeepL-Auth-Key {key}"}, body)
        try:
            results = [t["text"] for t in data["translations"]]
        except (KeyError, TypeError):
            raise ProviderError("Respuesta inesperada del servicio") from None
        return {i.id: html.unescape(re.sub(r"</?x>", "", r)).strip() for i, r in zip(request.items, results)}


class Google(Provider):
    name = "Google Translate"
    can_translate = True
    URL = "https://translation.googleapis.com/language/translate/v2"

    def languages(self, key: str) -> set[str] | None:
        data = get_json(self.URL + "/languages", {"X-Goog-Api-Key": key})
        return {d["language"].lower() for d in data["data"]["languages"]}

    def translate(self, key: str, request: Request) -> dict[str, str]:
        target = choose_code(self.languages(key) or set(), request.target)
        wrap = lambda d: f'<span translate="no">{html.escape(d)}</span>'  # noqa: E731
        texts = [protect_terms(html.escape(i.text), request.glossary, wrap) for i in request.items]
        data = post_json(self.URL, {"X-Goog-Api-Key": key}, {"q": texts, "target": target, "format": "html"})
        try:
            results = [t["translatedText"] for t in data["data"]["translations"]]
        except (KeyError, TypeError):
            raise ProviderError("Respuesta inesperada del servicio") from None
        clean = lambda s: html.unescape(re.sub(r"</?span[^>]*>", "", s)).strip()  # noqa: E731
        return {i.id: clean(r) for i, r in zip(request.items, results)}


class ElevenLabs(Provider):
    """Voz y doblaje (Fase 3). Por ahora solo valida la clave y lista idiomas."""

    name = "ElevenLabs"

    def languages(self, key: str) -> set[str] | None:
        models = get_json("https://api.elevenlabs.io/v1/models", {"xi-api-key": key})
        return {lang["language_id"].lower() for m in models for lang in m.get("languages", [])}


class Cohere(OpenAICompatible):
    """Cohere traduce con su API compatible con OpenAI; la lista de modelos sale de su API propia."""

    def models(self, key: str) -> list[str]:
        data = get_json("https://api.cohere.com/v1/models?endpoint=chat&page_size=100", {"Authorization": f"Bearer {key}"})
        return sorted(str(m["name"]) for m in data.get("models", []) if isinstance(m, dict) and "name" in m and chat_model(str(m["name"])))


BUILTIN: list[Provider] = [
    # Modelos de lenguaje (traducen cualquier idioma común)
    OpenAICompatible("OpenAI", "https://api.openai.com/v1", cheap_hints=("gpt-4.1-mini", "gpt-4o-mini", "mini")),
    Anthropic(),
    Gemini(),
    OpenAICompatible("Mistral", "https://api.mistral.ai/v1", cheap_hints=("mistral-small", "small")),
    OpenAICompatible("DeepSeek", "https://api.deepseek.com", cheap_hints=("deepseek-chat", "chat")),
    OpenAICompatible("xAI (Grok)", "https://api.x.ai/v1", cheap_hints=("mini", "fast")),
    OpenAICompatible("Groq", "https://api.groq.com/openai/v1", cheap_hints=("llama-3.3-70b", "70b", "llama")),
    Cohere("Cohere", "https://api.cohere.ai/compatibility/v1", check_url="https://api.cohere.com/v1/models", cheap_hints=("command-r7b", "command-a", "command")),
    OpenAICompatible(
        "OpenRouter", "https://openrouter.ai/api/v1", check_url="https://openrouter.ai/api/v1/key",
        cheap_hints=("openai/gpt-4.1-mini", "google/gemini-2.5-flash", "mini", "flash"),
    ),  # fmt: skip
    Ollama(),
    # Traductores
    DeepL(),
    Google(),
    # Voz y doblaje
    ElevenLabs(),
]
