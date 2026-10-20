import re

from revoz.ui import themes


def test_every_theme_has_every_color():
    keys = set(themes.THEMES[themes.DEFAULT])
    for name, palette in themes.THEMES.items():
        assert set(palette) == keys, name
        assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in palette.values()), name


def test_stylesheet_uses_palette_and_choices():
    css = themes.stylesheet(themes.THEMES["Arcade"])
    assert themes.THEMES["Arcade"]["accent"] in css and "{" in css
    assert themes.CHOICES[0] == themes.DEFAULT and themes.SYSTEM in themes.CHOICES
    assert set(themes.CHOICES) == set(themes.THEMES) | {themes.SYSTEM}
