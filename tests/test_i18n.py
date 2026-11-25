import ast
import re
from pathlib import Path

import pytest

from revoz.core import providers
from revoz.ui import i18n, themes
from revoz.ui.translations import TRANSLATIONS

UI = Path(__file__).parent.parent / "src" / "revoz" / "ui"

# Mensajes de error del núcleo que la interfaz muestra pasándolos por tr()
CORE_MESSAGES = {
    "Clave inválida o sin permisos",
    "No se pudo conectar con el servicio",
    "Proveedor y clave son obligatorios",
    "La clave tiene un formato inválido",
    "La carpeta no existe",
    "Elegí la carpeta del juego, no la raíz de un disco",
    "Ese juego ya está en la biblioteca",
    "Cargando el modelo de voz (la primera vez se descarga)…",
    "La IA cortó; reintentando en unos segundos…",
    "La IA pidió esperar: se llegó al límite de uso",
    "Respuesta inesperada del servicio",
    "La IA no devolvió las traducciones en el formato pedido",
    "Elegí un modelo de la IA",
    "Ollama no está abierto. Instalalo desde ollama.com y abrilo.",
    "Ollama no tiene modelos. Descargá uno, por ejemplo: ollama pull qwen2.5",
    "Este servicio no traduce texto",
    "La IA no devolvió esta línea; se reintenta en la próxima pasada",
    "'Hasta' tiene que ser mayor que 'Desde'",
    "'Desde' está después del final del audio",
    "Elegí una voz para este personaje (botón Voces)",
    "Piper no está instalado",
    "Piper no pudo generar el audio",
    "El archivo de Piper descargado no es válido",
}


def _ui_texts() -> set[str]:
    """Todos los textos marcados con tr() o N_() en la interfaz, más los que se traducen por nombre."""
    texts = set(CORE_MESSAGES) | set(themes.CHOICES) | {name for _, name in providers.TARGET_LANGUAGES}
    for f in UI.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) in ("tr", "N_")
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                texts.add(node.args[0].value)
    return texts


@pytest.mark.parametrize("lang", [code for code, _ in i18n.LANGUAGES if code != "es"])
def test_every_text_is_translated(lang):
    missing = sorted(_ui_texts() - set(TRANSLATIONS[lang]))
    assert not missing, f"Faltan traducciones en '{lang}': {missing}"


@pytest.mark.parametrize("lang", [code for code, _ in i18n.LANGUAGES if code != "es"])
def test_placeholders_are_kept(lang):
    for spanish, translated in TRANSLATIONS[lang].items():
        assert set(re.findall(r"{\w+}", spanish)) == set(re.findall(r"{\w+}", translated)), (lang, spanish)


def test_tr_switches_language_and_falls_back():
    try:
        i18n.set_language("en")
        assert i18n.tr("Biblioteca") == "Library"
        assert i18n.tr("{total} juegos", total=3) == "3 games"
        assert i18n.tr("texto sin traducir") == "texto sin traducir"
        i18n.set_language("xx")  # idioma desconocido: español
        assert i18n.current() == "es"
        assert i18n.tr("Biblioteca") == "Biblioteca"
    finally:
        i18n.set_language("es")
