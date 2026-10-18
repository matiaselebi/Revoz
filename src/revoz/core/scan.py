"""Escáner de audio. Lee solo cabeceras (primeros/últimos bytes); nunca escribe en el juego.

Clasifica cada archivo en:
  EASY   – audio legible directo (vgmstream lo abre): fácil de traducir.
  PACK   – contenedor sin cifrar: hace falta un plugin que extraiga el audio.
  LOCKED – contenedor o audio cifrado: hace falta un plugin con la clave.
"""

from __future__ import annotations

import json
import os
import struct
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

EASY, PACK, LOCKED = "easy", "pack", "locked"

# ponytail: umbral fijo para no marcar verde por unos pocos sonidos sueltos (ej. mods); un plugin por juego lo reemplaza.
EASY_MIN_BYTES = 20 * 1024 * 1024

_MAGIC = [
    (b"OggS", "Ogg Vorbis", EASY),
    (b"fLaC", "FLAC", EASY),
    (b"ID3", "MP3", EASY),
    (b"FSB5", "FMOD (.fsb)", EASY),
    (b"FSB4", "FMOD (.fsb)", EASY),
    (b"BKHD", "Wwise (.bnk)", EASY),
    (b"AKPK", "Wwise (.pck)", EASY),
    (b"UnityFS", "Unity bundle", PACK),
    (b"UnityWeb", "Unity bundle", PACK),
    (b"UnityRaw", "Unity bundle", PACK),
    (b"GDPC", "Godot (.pck)", PACK),
    (b"\x34\x12\xaa\x55", "Valve (.vpk)", PACK),
]
_RIFF = {b"WAVE": "WAV / Wwise (.wem)", b"FEV ": "FMOD (.bank)"}
# Extensiones de audio: si la firma no coincide, el archivo está cifrado u ofuscado.
_AUDIO_EXT = {".wav", ".wem", ".ogg", ".mp3", ".flac", ".fsb", ".bank", ".bnk"}
_PAK_MAGIC = b"\xe1\x12\x6f\x5a"
_UTOC_MAGIC = b"-==--==--==--==-"


@dataclass
class ScanResult:
    status: str  # "green" | "yellow" | "locked" | "red"
    counts: dict[str, int]  # formato -> cantidad de archivos
    files: int
    bytes_by_kind: dict[str, int] = field(default_factory=dict)


def _rpf(head: bytes) -> tuple[str, str]:
    name = f"RAGE ({head[:4].decode()})"
    if head[3:4] == b"7":  # GTA V: campo de cifrado en offset 12, "OPEN" = sin cifrar
        locked = head[12:16] != b"OPEN"
    else:  # RPF2/3/4 (GTA IV, Max Payne 3): flag en offset 16
        locked = len(head) >= 20 and struct.unpack_from("<I", head, 16)[0] != 0
    return (name + " cifrado", LOCKED) if locked else (name, PACK)


def _pak(path: Path) -> tuple[str, str]:
    # ponytail: busca el footer de Unreal en los últimos 512 bytes; paks muy viejos o modificados caen en "desconocido".
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - 512))
        tail = f.read()
    i = tail.rfind(_PAK_MAGIC)
    if i < 1:
        return "Paquete .pak (formato desconocido)", PACK
    return ("Unreal (.pak) cifrado", LOCKED) if tail[i - 1] == 1 else ("Unreal (.pak)", PACK)


def identify(path: Path) -> tuple[str, str] | None:
    """Devuelve (formato, tipo) o None si no es audio ni contenedor conocido."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pak":
            return _pak(path)
        with open(path, "rb") as f:
            head = f.read(96)
            if suffix == ".mp3" and not head.strip(b"\0"):  # MP3 con relleno de ceros al inicio
                head = f.read(4096).lstrip(b"\0")
    except OSError:
        return None
    if head[:4] in (b"RIFF", b"RIFX") and head[8:12] in _RIFF:
        return _RIFF[head[8:12]], EASY
    if head[:3] == b"RPF":
        return _rpf(head)
    if head[:16] == _UTOC_MAGIC:  # Unreal IoStore: flags del contenedor en offset 80, bit 2 = cifrado
        return ("Unreal IoStore cifrado", LOCKED) if len(head) > 80 and head[80] & 2 else ("Unreal IoStore", PACK)
    for magic, name, kind in _MAGIC:
        if head.startswith(magic):
            return name, kind
    if suffix == ".mp3" and len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:  # MP3 sin ID3: frame sync
        return "MP3", EASY
    if suffix in _AUDIO_EXT:
        return f"Audio cifrado/ofuscado ({suffix})", LOCKED
    return None


def _weight(path: Path) -> int:
    # El .utoc es solo el índice; los datos están en el .ucas de al lado.
    p = path.with_suffix(".ucas") if path.suffix.lower() == ".utoc" else path
    try:
        return p.stat().st_size
    except OSError:
        return 0


def classify(by_kind: dict[str, int]) -> str:
    easy, pack, locked = (by_kind.get(k, 0) for k in (EASY, PACK, LOCKED))
    if easy >= EASY_MIN_BYTES:
        return "green"
    if pack or locked:
        return "locked" if locked > pack else "yellow"
    return "green" if easy else "red"


def scan_dir(root: Path) -> ScanResult:
    counts: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    files = 0
    # os.walk no sigue symlinks por defecto: no salimos de la carpeta del juego.
    for dirpath, _, names in os.walk(root, onerror=lambda e: None):
        for n in names:
            files += 1
            path = Path(dirpath, n)
            hit = identify(path)
            if hit:
                counts[hit[0]] += 1
                by_kind[hit[1]] += _weight(path)
    return ScanResult(classify(by_kind), dict(counts), files, dict(by_kind))


def load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1), "utf-8")


def to_dict(r: ScanResult) -> dict:
    return asdict(r)
