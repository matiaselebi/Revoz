"""Idiomas de la interfaz.

Los textos se escriben en español dentro del código (`tr("Biblioteca")`) y se traducen con las
tablas de `translations.py`. Si falta una traducción se usa el inglés y, si tampoco está, el español.
Para listas de textos definidas antes de saber el idioma, se marcan con `N_()` y se traducen al usarlas.
La comunidad puede sumar idiomas con plugins de tipo "language" (ver `register_language`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QLocale

from revoz.ui.translations import TRANSLATIONS

LANGUAGES = [("es", "Español"), ("en", "English"), ("pt", "Português (Brasil)"), ("zh", "中文 (简体)")]
BUILTIN = {code for code, _ in LANGUAGES}
_CODE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})?")
_FIELD = re.compile(r"{(\w*)}")
_current = "es"


def N_(text: str) -> str:
    """Marca un texto para traducir más tarde (no traduce nada)."""
    return text


def system_language() -> str:
    """Idioma de Windows si Revoz lo tiene; si no, inglés."""
    code = QLocale.system().name().split("_")[0]
    return code if any(code == c for c, _ in LANGUAGES) else "en"


def set_language(code: str) -> None:
    global _current
    _current = code if any(code == c for c, _ in LANGUAGES) else "es"


def current() -> str:
    return _current


def tr(text: str, **values) -> str:
    if _current == "es":
        translated = text
    else:
        translated = TRANSLATIONS.get(_current, {}).get(text) or TRANSLATIONS["en"].get(text, text)
    return translated.format(**values) if values else translated


# --- idiomas de la comunidad (plugins de tipo "language") ---


def _safe(spanish: str, translated: object) -> bool:
    """Una traducción es válida si es texto y usa solo los {datos} del original.
    Así un idioma de la comunidad no puede hacer fallar la app con un {dato} inventado."""
    if not isinstance(translated, str) or not translated.strip():
        return False
    # Solo {nombre} simples: nada de {dato.atributo} ni {dato[...]}, que dejarían leer datos internos de la app.
    if "{" in _FIELD.sub("", translated) or "}" in _FIELD.sub("", translated):
        return False
    try:
        allowed = set(_FIELD.findall(spanish))
        used = set(_FIELD.findall(translated))
        translated.format(**{k: "" for k in allowed})
        return used <= allowed
    except (ValueError, IndexError, KeyError):
        return False


def register_language(code: object, name: object, texts: object) -> int:
    """Suma (o completa) un idioma desde un plugin. Devuelve cuántos textos se descartaron por inválidos."""
    if not (isinstance(code, str) and _CODE.fullmatch(code)):
        raise ValueError("'code' debe ser un código de idioma, por ejemplo \"fr\" o \"pt-PT\"")
    if code == "es":
        raise ValueError("el español es el idioma base del código; no se reemplaza")
    if not (isinstance(name, str) and 0 < len(name.strip()) <= 40):
        raise ValueError("'language_name' debe tener entre 1 y 40 caracteres")
    if not isinstance(texts, dict):
        raise ValueError("falta la tabla [texts]")
    good = {k: v for k, v in texts.items() if isinstance(k, str) and _safe(k, v)}
    TRANSLATIONS.setdefault(code, {}).update(good)
    if not any(code == c for c, _ in LANGUAGES):
        LANGUAGES.append((code, name.strip()))
    return len(texts) - len(good)


_ORIGINAL = {code: dict(table) for code, table in TRANSLATIONS.items()}


def reset_custom() -> None:
    """Deja solo los idiomas incluidos (antes de volver a cargar los plugins)."""
    LANGUAGES[:] = [(c, n) for c, n in LANGUAGES if c in BUILTIN]
    TRANSLATIONS.clear()
    TRANSLATIONS.update({code: dict(table) for code, table in _ORIGINAL.items()})


def write_template(folder: Path) -> Path:
    """Crea un plugin de idioma con todos los textos de Revoz, en inglés, listos para traducir."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Idioma para Revoz. Traducí cada valor (a la derecha del =). No cambies las claves (a la izquierda)",
        "# ni los {datos} entre llaves. Los textos que borres se muestran en inglés.",
        'name = "Revoz en mi idioma"',
        'version = "1.0"',
        'author = "tu nombre"',
        'kind = "language"',
        'code = "fr"              # código del idioma: fr, it, de, ja, pt-PT…',
        'language_name = "Français" # cómo se llama el idioma en ese idioma',
        "",
        "[texts]",
    ]
    for spanish, english in sorted(TRANSLATIONS["en"].items()):
        # json.dumps escapa comillas y barras igual que TOML
        lines.append(f"{json.dumps(spanish, ensure_ascii=False)} = {json.dumps(english, ensure_ascii=False)}")
    file = folder / "plugin.toml"
    file.write_text("\n".join(lines) + "\n", "utf-8")
    return file
