"""Proveedores de IA. Cada uno sabe validar su clave y decir a qué idiomas traduce.

Los plugins de tipo "provider" heredan de Provider para sumar más servicios.
La clave viaja solo en cabeceras HTTPS; nunca en la URL ni en mensajes de error.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

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


class ProviderError(Exception):
    pass


class Provider:
    name = ""

    def languages(self, key: str) -> set[str] | None:
        """Valida la clave y devuelve los códigos de idioma destino (minúsculas),
        o None si el servicio traduce cualquier idioma común (modelos de lenguaje)."""
        raise NotImplementedError


def supports(langs: set[str] | None, code: str) -> bool:
    if langs is None:
        return True
    primary = code.lower().split("-")[0]
    return any(lang.lower().split("-")[0] == primary for lang in langs)


def get_json(url: str, headers: dict[str, str]) -> object:
    if not url.startswith("https://"):
        raise ValueError("Solo se permiten conexiones HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": "Revoz/0.1", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read(2_000_000))
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):  # Google y xAI responden 400 a una clave inválida
            raise ProviderError("Clave inválida o sin permisos") from None
        raise ProviderError(f"El servicio respondió con error {e.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ProviderError("No se pudo conectar con el servicio") from None


class OpenAICompatible(Provider):
    """Modelos de lenguaje con API estilo OpenAI: se valida la clave listando los modelos."""

    def __init__(self, name: str, models_url: str) -> None:
        self.name = name
        self.models_url = models_url

    def languages(self, key: str) -> set[str] | None:
        get_json(self.models_url, {"Authorization": f"Bearer {key}"})
        return None


class Gemini(Provider):
    name = "Google Gemini"

    def languages(self, key: str) -> set[str] | None:
        get_json("https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": key})
        return None


class Anthropic(Provider):
    name = "Anthropic"

    def languages(self, key: str) -> set[str] | None:
        get_json("https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return None


class DeepL(Provider):
    name = "DeepL"

    def languages(self, key: str) -> set[str] | None:
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        data = get_json(f"https://{host}/v2/languages?type=target", {"Authorization": f"DeepL-Auth-Key {key}"})
        return {d["language"].lower() for d in data}


class ElevenLabs(Provider):
    name = "ElevenLabs"

    def languages(self, key: str) -> set[str] | None:
        models = get_json("https://api.elevenlabs.io/v1/models", {"xi-api-key": key})
        return {lang["language_id"].lower() for m in models for lang in m.get("languages", [])}


class Google(Provider):
    name = "Google Translate"

    def languages(self, key: str) -> set[str] | None:
        data = get_json(
            "https://translation.googleapis.com/language/translate/v2/languages", {"X-Goog-Api-Key": key}
        )
        return {d["language"].lower() for d in data["data"]["languages"]}


BUILTIN: list[Provider] = [
    # Modelos de lenguaje (traducen cualquier idioma común)
    OpenAICompatible("OpenAI", "https://api.openai.com/v1/models"),
    Anthropic(),
    Gemini(),
    OpenAICompatible("Mistral", "https://api.mistral.ai/v1/models"),
    OpenAICompatible("DeepSeek", "https://api.deepseek.com/models"),
    OpenAICompatible("xAI (Grok)", "https://api.x.ai/v1/models"),
    OpenAICompatible("Groq", "https://api.groq.com/openai/v1/models"),
    OpenAICompatible("Cohere", "https://api.cohere.com/v1/models"),
    OpenAICompatible("OpenRouter", "https://openrouter.ai/api/v1/key"),
    # Traductores
    DeepL(),
    Google(),
    # Voz y doblaje
    ElevenLabs(),
]
