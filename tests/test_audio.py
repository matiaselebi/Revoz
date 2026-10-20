import struct
import wave
from pathlib import Path

import pytest

from revoz.core import audio


def test_wav(tmp_path: Path):
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\0\0" * 22050 * 3)  # 3 segundos
    assert audio.duration(p) == pytest.approx(3.0)


def _ogg_page(granule: int, payload: bytes) -> bytes:
    return b"OggS\0\0" + struct.pack("<q", granule) + b"\0" * 12 + bytes([1, len(payload)]) + payload


def test_ogg_vorbis(tmp_path: Path):
    ident = b"\x01vorbis" + struct.pack("<I", 0) + bytes([2]) + struct.pack("<I", 44100) + b"\0" * 10
    p = tmp_path / "a.ogg"
    p.write_bytes(_ogg_page(0, ident) + _ogg_page(44100 * 5, b"datos"))
    assert audio.duration(p) == pytest.approx(5.0)


def test_flac(tmp_path: Path):
    s = bytearray(34)
    rate, total = 48000, 48000 * 2
    s[10], s[11], s[12] = rate >> 12, (rate >> 4) & 0xFF, (rate & 0x0F) << 4
    s[13] = (total >> 32) & 0x0F
    s[14:18] = (total & 0xFFFFFFFF).to_bytes(4, "big")
    p = tmp_path / "a.flac"
    p.write_bytes(b"fLaC" + b"\x80\0\0\x22" + bytes(s))
    assert audio.duration(p) == pytest.approx(2.0)


def test_mp3_cbr(tmp_path: Path):
    p = tmp_path / "a.mp3"
    frame = bytes([0xFF, 0xFB, 0x90, 0x00])  # MPEG-1 Layer III, 128 kbps
    p.write_bytes(b"\0" * 10 + frame + b"\0" * (16000 * 4 - 4))  # 4 s a 128 kbps = 64000 bytes
    assert audio.duration(p) == pytest.approx(4.0)


def test_unknown_and_broken(tmp_path: Path):
    (tmp_path / "x.bnk").write_bytes(b"BKHD" + b"\0" * 20)
    (tmp_path / "rota.wav").write_bytes(b"RIFF\0\0\0\0WAVEfmt ")
    assert audio.duration(tmp_path / "x.bnk") is None
    assert audio.duration(tmp_path / "rota.wav") is None
    assert audio.duration(tmp_path / "no-existe.wav") is None
