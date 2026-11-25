"""Temas e idiomas aportados por la comunidad (plugins de solo datos)."""

import shutil
import tomllib
from pathlib import Path

import pytest

from revoz.core import plugins
from revoz.ui import i18n, themes
from revoz.ui.translations import TRANSLATIONS

EXAMPLES = Path(__file__).parent.parent / "examples" / "plugins"


@pytest.fixture(autouse=True)
def _clean():
    yield
    themes.reset_custom()
    i18n.reset_custom()
    i18n.set_language("es")


def test_data_plugins_need_no_code_and_cannot_be_loaded_as_code(tmp_path: Path):
    for name in ("tema_atardecer", "idioma_italiano_ejemplo"):
        shutil.copytree(EXAMPLES / name, tmp_path / name)
    found, errors = plugins.discover(tmp_path)
    assert errors == []
    assert {p.kind for p in found} == {plugins.THEME, plugins.LANGUAGE}
    with pytest.raises(plugins.PluginError):
        plugins.load(found[0], {})


def test_example_theme_registers():
    meta = tomllib.loads((EXAMPLES / "tema_atardecer" / "plugin.toml").read_text("utf-8"))
    themes.register(meta["name"], meta["colors"])
    assert "Atardecer" in themes.CHOICES and themes.resolve("Atardecer")["accent"] == "#ff8a3d"
    themes.reset_custom()
    assert "Atardecer" not in themes.CHOICES and "Atardecer" not in themes.THEMES


@pytest.mark.parametrize(
    "name,colors,error",
    [
        ("Revoz", dict(themes.THEMES["Revoz"]), "incluido"),  # no pisa los temas de Revoz
        ("Nuevo", {"bg": "#000000"}, "faltantes"),
        ("Nuevo", {**themes.THEMES["Revoz"], "bg": "red; } QWidget { background: url(x)"}, "inválidos"),  # inyección de estilos
        ("Nuevo", "no es una tabla", "colors"),
        ("", dict(themes.THEMES["Revoz"]), "nombre"),
    ],
)
def test_bad_themes_are_rejected(name, colors, error):
    with pytest.raises(ValueError, match=error):
        themes.register(name, colors)


def test_language_plugin_drops_unsafe_texts_and_falls_back_to_english():
    skipped = i18n.register_language(
        "it",
        "Italiano",
        {
            "Biblioteca": "Libreria",
            "{total} juegos": "{total} giochi",
            "Perfil": "{inventado} rompe la app",  # dato que no existe en el original
            "Plugins": "{0.__class__}",  # intento de leer atributos con format
            "Carpeta: {path}": "Cartella: {path.__class__.__init__.__globals__}",  # leer datos internos
            "Guardar": "",
        },
    )
    assert skipped == 4
    assert ("it", "Italiano") in i18n.LANGUAGES
    i18n.set_language("it")
    assert i18n.tr("Biblioteca") == "Libreria"
    assert i18n.tr("{total} juegos", total=2) == "2 giochi"
    assert i18n.tr("Perfil") == "Profile"  # lo descartado se ve en inglés


@pytest.mark.parametrize("code", ["es", "xx_yy", "", None, "../../etc"])
def test_bad_language_codes_are_rejected(code):
    with pytest.raises(ValueError):
        i18n.register_language(code, "Nombre", {})


def test_templates_roundtrip(tmp_path: Path):
    theme_file = themes.write_template(tmp_path / "t", "Arcade")
    meta = tomllib.loads(theme_file.read_text("utf-8"))
    themes.register("Mi Arcade", meta["colors"])
    assert themes.THEMES["Mi Arcade"] == themes.THEMES["Arcade"]

    lang_file = i18n.write_template(tmp_path / "l")
    meta = tomllib.loads(lang_file.read_text("utf-8"))  # TOML válido aunque haya comillas, barras y {datos}
    assert meta["kind"] == "language" and set(meta["texts"]) == set(TRANSLATIONS["en"])
    assert i18n.register_language(meta["code"], meta["language_name"], meta["texts"]) == 0
