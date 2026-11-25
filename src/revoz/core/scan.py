"""Escáner de audio. Lee solo cabeceras (primeros/últimos bytes); nunca escribe en el juego.

Clasifica cada archivo en:
  EASY   - audio legible directo (vgmstream lo abre): fácil de traducir.
  PACK   - contenedor sin cifrar: hace falta un plugin que extraiga el audio.
  LOCKED - contenedor o audio cifrado: hace falta un plugin con la clave.
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
    (b"CPK ", "CRI (.cpk)", PACK),
    (b"@UTF", "CRI (.acb)", PACK),
    (b"AFS2", "CRI (.awb)", PACK),
    (b"HCA\0", "CRI HCA", EASY),
    (b"scimitar\0", "Ubisoft (.forge)", PACK),
]
_RIFF = {b"WAVE": "WAV", b"FEV ": "FMOD (.bank)"}
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
    # formato -> {"kind", "exts": [extensiones], "folder": carpeta con más de ese formato, "bytes"}
    formats: dict[str, dict] = field(default_factory=dict)


# Lo que Revoz ya sabe leer solo (audios sueltos: una línea por archivo)
READABLE = {"WAV", "Ogg Vorbis", "MP3", "FLAC"}
# Lo que Revoz extrae solo con un lector incluido (botón Extraer)
EXTRACTABLE = {"Valve (.vpk)"}
READ, EXTRACT, PLUGIN, ENCRYPTED = "read", "extract", "plugin", "encrypted"


def support(fmt: str, kind: str) -> str:
    """Qué puede hacer Revoz con ese formato hoy: leerlo, necesita un plugin, o está cifrado."""
    if kind == LOCKED:
        return ENCRYPTED
    if fmt in READABLE:
        return READ
    return EXTRACT if fmt in EXTRACTABLE else PLUGIN


def dialogue_formats(result: ScanResult, limit: int = 4) -> list[tuple[str, dict]]:
    """Los formatos de audio del juego, primero los que más pesan (ahí suelen estar los diálogos)."""
    return sorted(result.formats.items(), key=lambda kv: -kv[1]["bytes"])[:limit]


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
    if suffix == ".vpk" and not path.name.lower().endswith("_dir.vpk"):
        return None  # datos de un VPK (pak01_000.vpk…): se cuentan con su índice, aunque empiecen como un WAV
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
        if head[8:12] == b"WAVE":  # Wwise usa la misma cabecera que WAV, con extensión .wem
            return ("Wwise (.wem)" if suffix == ".wem" else "WAV"), EASY
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
    if head[:2] == b"\x80\x00" and b"(c)CRI" in head:  # ADX: la cabecera trae el copyright de CRI
        return "CRI ADX", EASY
    if suffix in _AUDIO_EXT:
        return f"Audio cifrado/ofuscado ({suffix})", LOCKED
    return None


def _weight(path: Path) -> int:
    # El .utoc es solo el índice; los datos están en el .ucas de al lado.
    p = path.with_suffix(".ucas") if path.suffix.lower() == ".utoc" else path
    try:
        if path.name.lower().endswith("_dir.vpk"):  # el índice de un VPK pesa poco: sumar sus archivos de datos
            stem = path.name[: -len("dir.vpk")]
            return p.stat().st_size + sum(f.stat().st_size for f in path.parent.glob(stem + "[0-9][0-9][0-9].vpk"))
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


def _main_folder(sizes: Counter[str]) -> str:
    """La carpeta más profunda que tiene la mayor parte (60 %) de ese formato. Ej.: si los audios están repartidos en
    sound/barney, sound/gman… devuelve "sound" en vez de una sola de las subcarpetas."""
    total = sum(sizes.values())
    by_folder: Counter[str] = Counter()
    for folder, size in sizes.items():
        parts = [] if folder == "." else folder.split("/")
        for depth in range(len(parts) + 1):
            by_folder["/".join(parts[:depth]) or "."] += size
    covering = [f for f, size in by_folder.items() if size >= 0.6 * total]
    return max(covering, key=lambda f: (0 if f == "." else f.count("/") + 1, by_folder[f]))


def scan_dir(root: Path) -> ScanResult:
    counts: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    formats: dict[str, dict] = {}
    folders: dict[str, Counter[str]] = {}
    files = 0
    # os.walk no sigue symlinks por defecto: no salimos de la carpeta del juego.
    for dirpath, _, names in os.walk(root, onerror=lambda e: None):
        for n in names:
            files += 1
            path = Path(dirpath, n)
            hit = identify(path)
            if hit:
                size = _weight(path)
                counts[hit[0]] += 1
                by_kind[hit[1]] += size
                info = formats.setdefault(hit[0], {"kind": hit[1], "exts": [], "folder": "", "bytes": 0})
                info["bytes"] += size
                if path.suffix.lower() not in info["exts"]:
                    info["exts"].append(path.suffix.lower())
                folders.setdefault(hit[0], Counter())[Path(dirpath).relative_to(root).as_posix()] += size
    for fmt, info in formats.items():
        info["folder"] = _main_folder(folders[fmt])
    return ScanResult(classify(by_kind), dict(counts), files, dict(by_kind), formats)


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
