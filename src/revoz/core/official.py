"""Idiomas oficiales de un juego según la tienda de Steam.

Muchos juegos ya traen doblaje oficial y se activa desde Steam (Propiedades → Idioma).
Antes de traducir con IA conviene avisar: el doblaje oficial es gratis, legal y mejor.
Solo se consulta la ficha pública de la tienda; no se descarga ningún archivo del juego.
"""

from __future__ import annotations

import re

from revoz.core.providers import get_json

# Código de Revoz -> nombres que usa Steam (en inglés). El primero es el exacto; el resto, el mismo idioma en otra región.
STEAM_NAMES = {
    "es-419": ["Spanish - Latin America", "Spanish - Spain"],
    "es": ["Spanish - Spain", "Spanish - Latin America"],
    "en": ["English"],
    "pt": ["Portuguese - Brazil", "Portuguese - Portugal"],
    "fr": ["French"],
    "de": ["German"],
    "it": ["Italian"],
    "ru": ["Russian"],
    "pl": ["Polish"],
    "tr": ["Turkish"],
    "ja": ["Japanese"],
    "ko": ["Korean"],
    "zh": ["Simplified Chinese", "Traditional Chinese"],
    "ar": ["Arabic"],
}

AUDIO, OTHER_REGION, TEXT, NONE = "audio", "other_region", "text", "none"


def parse_languages(html: str) -> dict[str, bool]:
    """'English<strong>*</strong>, Danish<br>…' -> {"English": True, "Danish": False} (True = con voces)."""
    listed = html.split("<br", 1)[0]
    out = {}
    for part in listed.split(","):
        name = re.sub(r"<[^>]*>", "", part).replace("*", "").strip()
        if name:
            out[name] = "*" in part
    return out


def steam_languages(appid: str) -> dict[str, bool] | None:
    """Idiomas de la ficha de Steam, o None si el juego no es de Steam o no tiene ficha."""
    if not appid.isdigit():
        return None
    data = get_json(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english&filters=basic", {})
    entry = data.get(appid) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or not entry.get("success"):
        return None
    return parse_languages(str(entry.get("data", {}).get("supported_languages", "")))


def check(languages: dict[str, bool], code: str) -> tuple[str, str]:
    """Qué hay oficial para ese idioma: (estado, nombre de la otra región si el estado es OTHER_REGION)."""
    exact, *others = STEAM_NAMES.get(code, [code])
    if languages.get(exact):
        return AUDIO, ""
    for name in others:
        if languages.get(name):
            return OTHER_REGION, name
    if exact in languages or any(name in languages for name in others):
        return TEXT, ""
    return NONE, ""
