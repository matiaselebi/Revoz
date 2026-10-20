"""Idiomas de la interfaz.

Los textos se escriben en español dentro del código (`tr("Biblioteca")`) y se traducen con las
tablas de `translations.py`. Si falta una traducción se muestra el texto en español.
Para listas de textos definidas antes de saber el idioma, se marcan con `N_()` y se traducen al usarlas.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale

from revoz.ui.translations import TRANSLATIONS

LANGUAGES = [("es", "Español"), ("en", "English"), ("pt", "Português (Brasil)"), ("zh", "中文 (简体)")]
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
    translated = TRANSLATIONS.get(_current, {}).get(text, text) if _current != "es" else text
    return translated.format(**values) if values else translated
