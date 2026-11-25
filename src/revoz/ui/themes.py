"""Temas de color. Cada tema es una paleta; la hoja de estilos se arma con ella.
Los nombres de los temas se guardan en español en el perfil y se traducen solo al mostrarlos."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

DEFAULT = "Revoz"
SYSTEM = "Sistema (Windows)"

# bg: fondo · surface: hover y encabezados · button / button_hover · border · input: campos de texto
# text / muted: texto normal y secundario · accent: color principal · accent_text: texto sobre accent
# select: selección en tablas y listas
THEMES: dict[str, dict[str, str]] = {
    "Revoz": {
        "bg": "#16181d", "surface": "#262a33", "button": "#2d333b", "button_hover": "#373e47", "border": "#30363d",
        "input": "#0d1117", "text": "#e6e6e6", "muted": "#8b949e", "accent": "#2ea043", "accent_text": "#ffffff",
        "select": "#1f6feb",
    },
    "Oscuro": {
        "bg": "#121212", "surface": "#1f1f1f", "button": "#2a2a2a", "button_hover": "#353535", "border": "#333333",
        "input": "#0a0a0a", "text": "#f0f0f0", "muted": "#9e9e9e", "accent": "#5c9dff", "accent_text": "#ffffff",
        "select": "#2d5aa8",
    },
    "Claro": {
        "bg": "#f6f8fa", "surface": "#eaeef2", "button": "#ffffff", "button_hover": "#f0f3f6", "border": "#d0d7de",
        "input": "#ffffff", "text": "#1f2328", "muted": "#656d76", "accent": "#1f883d", "accent_text": "#ffffff",
        "select": "#b6d7ff",
    },
    "Medianoche": {
        "bg": "#0b1220", "surface": "#16213a", "button": "#1b2a4a", "button_hover": "#24375f", "border": "#24375f",
        "input": "#070d18", "text": "#dbe7ff", "muted": "#8597bd", "accent": "#22d3ee", "accent_text": "#04111c",
        "select": "#1e40af",
    },
    "Arcade": {
        "bg": "#140b24", "surface": "#24123f", "button": "#2c1650", "button_hover": "#3b1d6b", "border": "#4c2a85",
        "input": "#0d0619", "text": "#f5e9ff", "muted": "#b394d6", "accent": "#ff3fa4", "accent_text": "#ffffff",
        "select": "#7c3aed",
    },
    "Bosque": {
        "bg": "#121a14", "surface": "#1d2a20", "button": "#233327", "button_hover": "#2d4232", "border": "#2f4634",
        "input": "#0b120d", "text": "#e3efe5", "muted": "#93a998", "accent": "#a3c94f", "accent_text": "#10180b",
        "select": "#3f6b2f",
    },
    "Alto contraste": {
        "bg": "#000000", "surface": "#1a1a1a", "button": "#000000", "button_hover": "#262626", "border": "#ffffff",
        "input": "#000000", "text": "#ffffff", "muted": "#ffff00", "accent": "#ffff00", "accent_text": "#000000",
        "select": "#0000ff",
    },
}  # fmt: skip

CHOICES = [DEFAULT, SYSTEM, *[n for n in THEMES if n != DEFAULT]]
BUILTIN = set(THEMES)
COLOR_KEYS = set(THEMES[DEFAULT])
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


# --- temas de la comunidad (plugins de tipo "theme") ---


def register(name: str, colors: object) -> None:
    """Suma un tema de un plugin. Valida todo: los colores van dentro de la hoja de estilos,
    así que solo se aceptan colores #rrggbb (nada de texto que pueda alterar los estilos)."""
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 40:
        raise ValueError("el nombre del tema debe tener entre 1 y 40 caracteres")
    if name in BUILTIN or name == SYSTEM:
        raise ValueError(f"'{name}' es un tema incluido en Revoz; usá otro nombre")
    if not isinstance(colors, dict):
        raise ValueError("falta la tabla [colors]")
    missing, extra = COLOR_KEYS - set(colors), set(colors) - COLOR_KEYS
    if missing or extra:
        raise ValueError(f"colores faltantes: {sorted(missing)}; sobrantes: {sorted(extra)}")
    bad = [k for k, v in colors.items() if not (isinstance(v, str) and _HEX.fullmatch(v))]
    if bad:
        raise ValueError(f"colores inválidos (deben ser #rrggbb): {sorted(bad)}")
    THEMES[name] = {k: v.lower() for k, v in colors.items()}
    if name not in CHOICES:
        CHOICES.append(name)


def reset_custom() -> None:
    """Saca los temas de plugins (antes de volver a cargarlos)."""
    for name in [n for n in THEMES if n not in BUILTIN]:
        del THEMES[name]
    CHOICES[:] = [c for c in CHOICES if c in BUILTIN or c == SYSTEM]


def write_template(folder: Path, base: str = DEFAULT) -> Path:
    """Crea un plugin de tema listo para editar, a partir de los colores de un tema existente."""
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Tema para Revoz. Cambiá el nombre y los colores (#rrggbb) y reiniciá Revoz o tocá Recargar.",
        'name = "Mi tema"',
        'version = "1.0"',
        'author = "tu nombre"',
        'kind = "theme"',
        "",
        "[colors]",
    ]
    for key, value in resolve(base).items():
        lines.append(f'{key} = "{value}"')
    file = folder / "plugin.toml"
    file.write_text("\n".join(lines) + "\n", "utf-8")
    return file


def resolve(name: str) -> dict[str, str]:
    """Paleta a usar. "Sistema" sigue el modo claro/oscuro de Windows."""
    if name == SYSTEM:
        dark = QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
        return THEMES["Oscuro" if dark else "Claro"]
    return THEMES.get(name, THEMES[DEFAULT])


def stylesheet(c: dict[str, str]) -> str:
    return f"""
QWidget {{ background: {c['bg']}; color: {c['text']}; font-size: 10pt; }}
QLabel#hint {{ color: {c['muted']}; }}
QWidget#sep {{ background: {c['border']}; }}
QListWidget {{ border: none; }}
QListWidget::item:selected, QListWidget::item:hover {{ background: {c['surface']}; border-radius: 6px; }}
QPushButton {{ background: {c['button']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 6px 14px; }}
QPushButton:hover {{ background: {c['button_hover']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: bold; }}
QPushButton#primary:disabled {{ background: {c['button']}; color: {c['muted']}; border-color: {c['border']}; }}
QLineEdit, QComboBox, QDoubleSpinBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 5px; }}
QComboBox QAbstractItemView {{ background: {c['input']}; selection-background-color: {c['select']}; }}
QMenu {{ background: {c['button']}; border: 1px solid {c['border']}; }}
QMenu::item:selected {{ background: {c['select']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 6px; margin-top: 14px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; }}
QPushButton#nav, QToolButton#nav {{ background: transparent; border: none; border-bottom: 2px solid transparent;
    border-radius: 0; padding: 8px 16px; font-size: 10.5pt; }}
QPushButton#nav:hover, QToolButton#nav:hover {{ background: {c['surface']}; }}
QPushButton#nav:checked, QToolButton#nav:checked {{ border-bottom: 2px solid {c['accent']}; }}
QTableWidget, QTableView {{ border: 1px solid {c['border']}; gridline-color: {c['border']};
    selection-background-color: {c['select']}; selection-color: {c['text']}; }}
QHeaderView::section {{ background: {c['button']}; border: none; padding: 5px; }}
QProgressBar {{ border: 1px solid {c['border']}; border-radius: 4px; background: {c['input']}; text-align: center; min-height: 16px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QScrollBar:vertical {{ background: {c['bg']}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {c['bg']}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 30px; }}
QCheckBox::indicator, QRadioButton::indicator, QTableView::indicator {{
    width: 14px; height: 14px; border: 1px solid {c['muted']}; background: {c['input']}; }}
QCheckBox::indicator, QTableView::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked, QTableView::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {c['border']}; }}
QToolTip {{ background: {c['button']}; color: {c['text']}; border: 1px solid {c['border']}; }}
"""


def apply(name: str) -> None:
    QApplication.instance().setStyleSheet(stylesheet(resolve(name)))
