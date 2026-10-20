"""Duración de archivos de audio leyendo solo sus cabeceras (no decodifica nada)."""

from __future__ import annotations

import struct
from pathlib import Path

_MP3_KBPS = {
    3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],  # MPEG-1 Layer III
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],  # MPEG-2 Layer III
    0: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],  # MPEG-2.5 Layer III
}


def duration(path: Path) -> float | None:
    """Segundos de audio, o None si el formato no se reconoce o la cabecera está rota."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            head = f.read(64 * 1024)
            if head[:4] == b"OggS":
                f.seek(max(0, size - 64 * 1024))
                tail = f.read()
            else:
                tail = b""
    except OSError:
        return None
    try:
        if head[:4] in (b"RIFF", b"RIFX") and head[8:12] == b"WAVE":
            return _riff(head, big=head[:4] == b"RIFX")
        if head[:4] == b"OggS":
            return _ogg(head, tail)
        if head[:4] == b"fLaC":
            return _flac(head)
        if path.suffix.lower() == ".mp3":
            return _mp3(head, size)
    except (struct.error, IndexError, ZeroDivisionError):
        return None
    return None


def _riff(head: bytes, big: bool) -> float | None:
    end = ">" if big else "<"
    pos, byte_rate, data_size = 12, None, None
    while pos + 8 <= len(head):
        chunk, length = head[pos : pos + 4], struct.unpack_from(end + "I", head, pos + 4)[0]
        if chunk == b"fmt ":
            byte_rate = struct.unpack_from(end + "I", head, pos + 16)[0]
        elif chunk == b"data":
            data_size = length
        if byte_rate and data_size is not None:
            return data_size / byte_rate
        pos += 8 + length + (length & 1)
    return None


def _ogg(head: bytes, tail: bytes) -> float | None:
    if (i := head.find(b"\x01vorbis")) >= 0:
        rate = struct.unpack_from("<I", head, i + 12)[0]
    elif head.find(b"OpusHead") >= 0:
        rate = 48000  # Opus siempre cuenta en 48 kHz
    else:
        return None
    last = tail.rfind(b"OggS")
    if last < 0:
        return None
    granule = struct.unpack_from("<q", tail, last + 6)[0]
    return granule / rate if granule > 0 else None


def _flac(head: bytes) -> float | None:
    s = head[8:42]  # bloque STREAMINFO
    rate = s[10] << 12 | s[11] << 4 | s[12] >> 4
    total = (s[13] & 0x0F) << 32 | int.from_bytes(s[14:18], "big")
    return total / rate if rate and total else None


def _mp3(head: bytes, size: int) -> float | None:
    # ponytail: asume bitrate constante (CBR); en MP3 de bitrate variable la duración es aproximada.
    start = 0
    if head[:3] == b"ID3":
        start = 10 + (head[6] << 21 | head[7] << 14 | head[8] << 7 | head[9])
    for i in range(start, len(head) - 3):
        b1, b2 = head[i + 1], head[i + 2]
        if head[i] == 0xFF and b1 & 0xE0 == 0xE0 and (b1 >> 1) & 3 == 1:  # sincronía + Layer III
            kbps = _MP3_KBPS.get((b1 >> 3) & 3, [0] * 16)[b2 >> 4] if b2 >> 4 < 15 else 0
            return (size - i) * 8 / (kbps * 1000) if kbps else None
    return None
