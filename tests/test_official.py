"""Idiomas oficiales según Steam (sin conectarse: con la respuesta real de Assassin's Creed 2)."""

from revoz.core import official

AC2 = (
    "English<strong>*</strong>, French<strong>*</strong>, German<strong>*</strong>, Italian<strong>*</strong>, "
    "Spanish - Spain<strong>*</strong>, Simplified Chinese, Traditional Chinese, Danish, Dutch, Korean, Swedish"
    "<br><strong>*</strong>languages with full audio support"
)


def test_parse_and_check():
    langs = official.parse_languages(AC2)
    assert langs["Spanish - Spain"] is True and langs["Korean"] is False and len(langs) == 11
    assert official.check(langs, "es") == (official.AUDIO, "")
    assert official.check(langs, "es-419") == (official.OTHER_REGION, "Spanish - Spain")  # hay voces, pero de España
    assert official.check(langs, "ko") == (official.TEXT, "")  # solo subtítulos
    assert official.check(langs, "ru") == (official.NONE, "")


def test_only_steam_ids_are_asked(monkeypatch):
    monkeypatch.setattr(official, "get_json", lambda *a, **k: 1 / 0)  # no tiene que conectarse
    assert official.steam_languages("epic_abc") is None


def test_steam_answer(monkeypatch):
    monkeypatch.setattr(official, "get_json", lambda url, headers: {"33230": {"success": True, "data": {"supported_languages": AC2}}})
    assert official.steam_languages("33230")["French"] is True
    monkeypatch.setattr(official, "get_json", lambda url, headers: {"1": {"success": False}})
    assert official.steam_languages("1") is None
