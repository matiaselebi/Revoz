import struct
from pathlib import Path

import pytest

import json

from revoz.core import scan, steam, stores

LIBRARY_VDF = r'''
"libraryfolders"
{
	"0"
	{
		"path"		"{lib}"
		"apps" { "100" "1" "228980" "1" }
	}
}
'''


def _manifest(appid: str, name: str, installdir: str) -> str:
    return f'"AppState"\n{{\n\t"appid" "{appid}"\n\t"name" "{name}"\n\t"installdir" "{installdir}"\n\t"buildid" "42"\n}}\n'


def test_parse_vdf_nested_and_escapes():
    data = steam.parse_vdf(r'"a" { "path" "C:\\Games" "q" "di \"hola\"" "b" { "c" "1" } }')
    assert data == {"a": {"path": "C:\\Games", "q": 'di "hola"', "b": {"c": "1"}}}


@pytest.mark.parametrize("bad", ['"a" {', '"a" "b" }', "{"])
def test_parse_vdf_rejects_unbalanced(bad):
    with pytest.raises(ValueError):
        steam.parse_vdf(bad)


def test_list_games(tmp_path: Path):
    apps = tmp_path / "steamapps"
    (apps / "common" / "Juego").mkdir(parents=True)
    (apps / "common" / "Redist").mkdir(parents=True)
    lib = str(tmp_path).replace("\\", "\\\\")
    (apps / "libraryfolders.vdf").write_text(LIBRARY_VDF.replace("{lib}", lib))
    (apps / "appmanifest_100.acf").write_text(_manifest("100", "Mi Juego", "Juego"))
    (apps / "appmanifest_228980.acf").write_text(_manifest("228980", "Redist", "Redist"))
    (apps / "appmanifest_200.acf").write_text(_manifest("200", "Desinstalado", "NoExiste"))
    (apps / "appmanifest_300.acf").write_text("roto {")

    games = steam.list_games(tmp_path)
    assert [(g.appid, g.name, g.buildid) for g in games] == [("100", "Mi Juego", "42")]


def _rpf3(encrypted: int) -> bytes:
    return b"RPF3" + struct.pack("<4I", 2048, 1, 0, encrypted)


@pytest.mark.parametrize(
    "name,data,expected",
    [
        ("a.wem", b"RIFF\0\0\0\0WAVEfmt ", ("WAV / Wwise (.wem)", scan.EASY)),
        ("b.bnk", b"BKHD" + b"\0" * 20, ("Wwise (.bnk)", scan.EASY)),
        ("speech.rpf", _rpf3(0xFFFFFFFF), ("RAGE (RPF3) cifrado", scan.LOCKED)),
        ("open.rpf", _rpf3(0), ("RAGE (RPF3)", scan.PACK)),
        ("x.rpf", b"RPF7" + b"\0" * 8 + b"OPEN", ("RAGE (RPF7)", scan.PACK)),
        ("enc.pak", b"\0" * 100 + b"\x01" + b"\xe1\x12\x6f\x5a" + b"\0" * 40, ("Unreal (.pak) cifrado", scan.LOCKED)),
        ("ok.pak", b"\0" * 100 + b"\x00" + b"\xe1\x12\x6f\x5a" + b"\0" * 40, ("Unreal (.pak)", scan.PACK)),
        ("g.utoc", b"-==--==--==--==-" + b"\0" * 64 + b"\x02", ("Unreal IoStore cifrado", scan.LOCKED)),
        ("voz.ogg", b"\x93\x11garbage", ("Audio cifrado/ofuscado (.ogg)", scan.LOCKED)),
        ("raw.mp3", b"\xff\xfb\x90\x00", ("MP3", scan.EASY)),
        ("padded.mp3", b"\0" * 300 + b"\xff\xfb\x90\x00", ("MP3", scan.EASY)),
        ("pak01_dir.vpk", b"\x34\x12\xaa\x55\x02\0\0\0", ("Valve (.vpk)", scan.PACK)),
        ("data.unity3d", b"UnityFS\0\0\0\0\0", ("Unity bundle", scan.PACK)),
        ("x.dll", b"MZ\0\0", None),
    ],
)
def test_identify(tmp_path: Path, name, data, expected):
    p = tmp_path / name
    p.write_bytes(data)
    assert scan.identify(p) == expected


def test_classify():
    mb = 1024 * 1024
    assert scan.classify({scan.EASY: 500 * mb, scan.LOCKED: 900 * mb}) == "green"
    # pocos sonidos sueltos (mods) no tapan el audio cifrado real
    assert scan.classify({scan.EASY: 1 * mb, scan.LOCKED: 900 * mb, scan.PACK: 15 * mb}) == "locked"
    assert scan.classify({scan.PACK: 900 * mb, scan.LOCKED: 1 * mb}) == "yellow"
    assert scan.classify({scan.EASY: 1 * mb}) == "green"
    assert scan.classify({}) == "red"


def test_scan_dir(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "speech.rpf").write_bytes(_rpf3(0xFFFFFFFF))
    (tmp_path / "c.txt").write_bytes(b"hola")
    r = scan.scan_dir(tmp_path)
    assert (r.status, r.counts, r.files) == ("locked", {"RAGE (RPF3) cifrado": 1}, 2)


def test_list_epic_skips_dlc_and_incomplete(tmp_path: Path):
    game = tmp_path / "Juego"
    game.mkdir()

    def item(name, **extra):
        data = {"AppName": name, "DisplayName": name.title(), "InstallLocation": str(game), "AppVersionString": "1.2", **extra}
        (tmp_path / f"{name}.item").write_text(json.dumps(data))

    item("juego")
    item("dlc", MainGameAppName="juego")
    item("amedias", bIsIncompleteInstall=True)
    (tmp_path / "roto.item").write_text("{")
    games = stores.list_epic(tmp_path)
    assert [(g.appid, g.name, g.buildid, g.store) for g in games] == [("epic:juego", "Juego", "1.2", "Epic")]


def test_gog_game(tmp_path: Path):
    base = {"gameName": "Juego", "path": str(tmp_path), "gameID": "123", "ver": "1.0"}
    g = stores.gog_game(base)
    assert (g.appid, g.name, g.store) == ("gog:123", "Juego", "GOG")
    assert stores.gog_game({**base, "dependsOn": "999"}) is None  # DLC
    assert stores.gog_game({**base, "path": str(tmp_path / "no")}) is None


def test_all_games_dedupes_only_across_stores(tmp_path: Path, monkeypatch):
    shared, other = tmp_path / "Half-Life", tmp_path / "Otro"
    shared.mkdir()
    other.mkdir()
    steam_games = [steam.Game("10", "Counter-Strike", shared, "", None), steam.Game("70", "Half-Life", shared, "", None)]
    monkeypatch.setattr(steam, "find_steam", lambda: tmp_path)
    monkeypatch.setattr(steam, "list_games", lambda root: steam_games)
    monkeypatch.setattr(stores, "list_epic", lambda: [stores.Game("epic:x", "Half-Life Epic", shared, "", None, "Epic")])
    monkeypatch.setattr(stores, "list_gog", lambda: [])
    stores.add_manual(tmp_path / "m.json", other)
    stores.add_manual(tmp_path / "m2.json", shared)
    names = [g.name for g in stores.all_games(tmp_path / "m.json")]
    assert names == ["Counter-Strike", "Half-Life", "Otro"]  # los dos de Steam quedan; el de Epic no


def test_manual_games(tmp_path: Path):
    file = tmp_path / "manual.json"
    folder = tmp_path / "Mi Juego"
    folder.mkdir()
    exe = folder / "juego.exe"
    exe.write_bytes(b"MZ")

    g = stores.add_manual(file, exe)  # por .exe: usa su carpeta
    assert (g.name, g.path, g.store) == ("Mi Juego", folder.resolve(), "Manual")
    with pytest.raises(ValueError, match="ya está"):
        stores.add_manual(file, folder)
    with pytest.raises(ValueError):
        stores.add_manual(file, tmp_path / "no-existe")
    with pytest.raises(ValueError, match="raíz"):
        stores.add_manual(file, Path(folder.anchor))
    assert [x.appid for x in stores.list_manual(file)] == [g.appid]

    stores.remove_manual(file, g.appid)
    assert stores.list_manual(file) == []


def test_group_games(tmp_path: Path):
    def g(appid, name, store):
        return steam.Game(appid, name, tmp_path / appid.replace(":", "_"), "", None, store)

    games = [
        g("epic:gta", "Grand Theft Auto V™", "Epic"),
        g("271590", "Grand Theft Auto V", "Steam"),
        g("3240220", "Grand Theft Auto V Enhanced", "Steam"),
        g("manual:a", "grand theft auto v", "Manual"),
    ]
    groups = [[x.appid for x in v] for v in stores.group_games(games)]
    # mismo nombre = un grupo, ordenado Steam > Epic > Manual; "Enhanced" queda aparte
    assert groups == [["271590", "epic:gta", "manual:a"], ["3240220"]]


def _make_game(root: Path, files: list[str]) -> Path:
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    return root


def test_same_game_score(tmp_path: Path):
    base = ["juego.exe", "Juego_Data/level1", "Juego_Data/sharedassets0.assets", "audio/en/voz.bnk",
            "audio/de/voz.bnk", "audio/es/voz.bnk", "UnityPlayer.dll", "steam_api64.dll"]  # fmt: skip
    original = _make_game(tmp_path / "steam", base)
    # copia con mods, sin el alemán y sin archivos de Steam: sigue siendo el mismo juego
    copia = [f for f in base if "/de/" not in f and "steam" not in f] + ["mods/mi_mod.asi", "EOSSDK-Win64-Shipping.dll"]
    assert stores.same_game_score(original, _make_game(tmp_path / "copia", copia)) >= stores.SAME_GAME_MIN
    # otro juego del mismo motor: solo comparte lo genérico, que no cuenta
    otro = _make_game(tmp_path / "otro", ["otro.exe", "Otro_Data/level1", "UnityPlayer.dll", "steam_api64.dll"])
    assert stores.same_game_score(original, otro) < stores.SAME_GAME_MIN
    assert stores.same_game_score(original, original / "audio") < stores.SAME_GAME_MIN  # una subcarpeta no vale


def test_list_folder_games(tmp_path: Path):
    lib = tmp_path / "Juegos"
    _make_game(lib / "Juego A", ["juegoa.exe"])
    _make_game(lib / "Juego B", ["bin/x64/juegob.exe"])  # .exe en subcarpeta
    _make_game(lib / "Solo instalador", ["unins000.exe", "leeme.txt"])  # solo un desinstalador: no es juego
    _make_game(lib / "Fotos", ["a.jpg"])
    games = stores.list_folder_games([lib, tmp_path / "no-existe"])
    assert sorted((g.name, g.store) for g in games) == [("Juego A", "Folder"), ("Juego B", "Folder")]
    assert all(g.appid.startswith("folder:") for g in games)
