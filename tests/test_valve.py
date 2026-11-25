"""Lector de VPK de Valve: índice, datos repartidos, CRC, sonidos de Source 2, extraer e instalar en custom/."""

import io
import struct
import wave
import zlib
from pathlib import Path

import pytest

from revoz.core import audio, install, valve, vpk
from revoz.core import project as pj


def _wav(seconds: float, rate: int = 22050) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return out.getvalue()


def _vsnd(pcm: bytes, rate: int = 44100) -> bytes:
    """Un .vsnd_c mínimo como los de CS2: cabecera de recurso, bloque DATA de 48 bytes y el PCM al final."""
    info = struct.pack("<HBBiIf", rate, 0, 1, -1, len(pcm) // 2, len(pcm) / 2 / rate) + b"\0" * 12
    info += struct.pack("<I", len(pcm)) + b"\0" * 16
    header_size = 16 + 12 + len(info)
    head = struct.pack("<IHHII", header_size, 12, 4, 8, 1) + b"DATA" + struct.pack("<II", 8, len(info))
    return head + info + pcm


def _write_vpk(folder: Path, files: dict[str, bytes], in_dir: set[str] = frozenset(), preload: int = 0) -> Path:
    """Arma pak01_dir.vpk (v2) + pak01_000.vpk con esos archivos."""
    tree, data_dir, data_000 = b"", b"", b""
    by_ext: dict[str, dict[str, list[tuple[str, bytes]]]] = {}
    for path, content in files.items():
        folder_part, _, name = path.rpartition("/")
        stem, _, ext = name.rpartition(".")
        by_ext.setdefault(ext, {}).setdefault(folder_part or " ", []).append((stem, content))
    for ext, folders in by_ext.items():
        tree += ext.encode() + b"\0"
        for fold, names in folders.items():
            tree += fold.encode() + b"\0"
            for stem, content in names:
                full = f"{fold}/{stem}.{ext}"
                pre, rest = content[:preload], content[preload:]
                if full in in_dir:
                    archive, offset = 0x7FFF, len(data_dir)
                    data_dir += rest
                else:
                    archive, offset = 0, len(data_000)
                    data_000 += rest
                tree += stem.encode() + b"\0" + struct.pack("<IHHIIH", zlib.crc32(content), len(pre), archive, offset, len(rest), 0xFFFF) + pre
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"
    folder.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<IIIIIII", vpk.SIGNATURE, 2, len(tree), len(data_dir), 0, 0, 0)
    (folder / "pak01_dir.vpk").write_bytes(header + tree + data_dir)
    (folder / "pak01_000.vpk").write_bytes(data_000)
    return folder / "pak01_dir.vpk"


def test_reads_index_and_files_from_both_places(tmp_path):
    files = {"sound/vo/barney/ba_hi.wav": _wav(0.5), "scripts/x.txt": b"hola", "sound/vo/gman/time.wav": _wav(0.2)}
    d = _write_vpk(tmp_path / "hl2", files, in_dir={"scripts/x.txt"}, preload=4)
    entries, start = vpk.read_index(d)
    assert set(entries) == set(files)
    for path, content in files.items():
        assert vpk.read_file(d, entries[path], start) == content
    assert vpk.archive_path(d, 3).name == "pak01_003.vpk"


def test_damaged_data_is_detected(tmp_path):
    d = _write_vpk(tmp_path / "hl2", {"sound/a.wav": _wav(0.1)})
    entries, start = vpk.read_index(d)
    data = bytearray((tmp_path / "hl2" / "pak01_000.vpk").read_bytes())
    data[100] ^= 0xFF
    (tmp_path / "hl2" / "pak01_000.vpk").write_bytes(bytes(data))
    with pytest.raises(vpk.VPKError):
        vpk.read_file(d, entries["sound/a.wav"], start)
    with pytest.raises(vpk.VPKError):
        vpk.read_index(tmp_path / "hl2" / "pak01_000.vpk")  # los de datos no tienen índice


def test_source2_sound_becomes_a_wav():
    data, suffix = valve.decode_vsnd(_vsnd(b"\x01\x00" * 4410))
    assert suffix == ".wav"
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (44100, 1, 4410)
    with pytest.raises(ValueError):
        valve.decode_vsnd(b"basura" * 10)


def test_extract_translate_install_into_custom_and_restore(tmp_path):
    game = tmp_path / "Half-Life 2"
    _write_vpk(game / "hl2", {"sound/vo/npc/barney/ba_hi.wav": _wav(1.0), "sound/weapons/shot.wav": _wav(0.3)})
    items = valve.find(game)
    voices = [i for i in items if valve.is_voice(i.inner)]
    assert [i.inner for i in voices] == ["sound/vo/npc/barney/ba_hi.wav"]
    done, failed = valve.extract(game, voices)
    assert (done, failed) == (1, [])

    lines = pj.build_lines(game)  # lo extraído aparece como audio suelto: el resto de Revoz funciona igual
    assert [line.id for line in lines] == ["Revoz/hl2/pak01/sound/vo/npc/barney/ba_hi.wav"]
    assert lines[0].character == "barney"
    dub = tmp_path / "dub.wav"
    dub.write_bytes(_wav(0.4, 44100))
    lines[0].dub_path = str(dub)

    targets = valve.targets(game)
    result = install.install(lines, game, tmp_path / "bk", targets=targets)
    installed = game / "hl2" / "custom" / "revoz" / "sound" / "vo" / "npc" / "barney" / "ba_hi.wav"
    assert result.done == 1 and installed.is_file()
    assert audio.duration(installed) == pytest.approx(0.4, abs=0.02)
    with wave.open(str(installed)) as w:
        assert w.getframerate() == 22050  # mismo formato que el original del VPK
    assert install.restore(game, tmp_path / "bk").done == 1
    assert not installed.exists()  # lo había creado Revoz: restaurar lo borra; el VPK nunca se tocó


def test_source2_lines_are_extracted_but_not_installed_yet(tmp_path):
    game = tmp_path / "CS2"
    _write_vpk(game / "game" / "csgo", {"sounds/vo/agents/balkan/hi.vsnd_c": _vsnd(b"\0\0" * 4410)})
    valve.extract(game, valve.find(game))
    lines = pj.build_lines(game)
    assert lines[0].id == "Revoz/game/csgo/pak01/sounds/vo/agents/balkan/hi.wav"
    dub = tmp_path / "dub.wav"
    dub.write_bytes(_wav(0.1))
    lines[0].dub_path = str(dub)
    result = install.install(lines, game, tmp_path / "bk", targets=valve.targets(game))
    assert result.done == 0 and result.skipped[0][1] == install.NOT_YET
