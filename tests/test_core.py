import struct
from pathlib import Path

import pytest

from revoz.core import scan, steam

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
