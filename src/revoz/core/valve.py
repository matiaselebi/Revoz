"""Lector incluido para juegos de Valve: saca los audios de los paquetes VPK a la carpeta Revoz del juego.

- Source 1 (Half-Life 2, Portal, L4D, TF2…): adentro hay .wav y .mp3 comunes. Para instalar no hace falta
  reempaquetar: el motor prioriza los archivos sueltos de `<mod>/custom/`, así que la voz traducida va a
  `<mod>/custom/revoz/sound/...` y el VPK original no se toca.
- Source 2 (CS2, Half-Life: Alyx…): los sonidos están compilados (.vsnd_c). Se convierten a WAV o MP3 para
  traducirlos; instalarlos de vuelta todavía no está hecho.
"""

from __future__ import annotations

import io
import json
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from revoz.core import vpk

FOLDER = "Revoz"  # dentro de la carpeta del juego
MAP_FILE = "revoz_extraido.json"
AUDIO_EXT = (".wav", ".mp3", ".vsnd_c")
VOICE_HINTS = ("/vo/", "/vo_", "voice", "dialog", "speech", "/npc/")


@dataclass
class Item:
    vpk: Path  # el *_dir.vpk
    inner: str  # ruta dentro del paquete
    size: int


def is_voice(inner: str) -> bool:
    return any(h in "/" + inner for h in VOICE_HINTS)


def find(game_path: Path) -> list[Item]:
    """Todos los audios dentro de los VPK del juego."""
    items = []
    for dir_vpk in sorted(game_path.rglob("*_dir.vpk")):
        if FOLDER.lower() in (p.lower() for p in dir_vpk.relative_to(game_path).parts):
            continue
        try:
            entries, _ = vpk.read_index(dir_vpk)
        except (OSError, vpk.VPKError):
            continue
        items += [Item(dir_vpk, inner, e.size) for inner, e in entries.items() if inner.endswith(AUDIO_EXT)]
    return items


# --- Source 2: .vsnd_c ---

PCM16, PCM8, MP3 = 0, 1, 2


def decode_vsnd(data: bytes) -> tuple[bytes, str]:
    """Saca el audio de un .vsnd_c (formato v4): devuelve (bytes, ".wav" o ".mp3"). ValueError si no se puede."""
    try:
        resource_size, _, _, block_offset, block_count = struct.unpack_from("<IHHII", data, 0)
        pos = 8 + block_offset
        info = None
        for _ in range(block_count):
            name = data[pos : pos + 4]
            offset, size = struct.unpack_from("<II", data, pos + 4)
            if name == b"DATA":
                info = data[pos + 4 + offset : pos + 4 + offset + size]
            pos += 12
        if info is None or len(info) < 32:
            raise ValueError
        rate, fmt, channels = struct.unpack_from("<HBB", info, 0)
        samples = struct.unpack_from("<I", info, 8)[0]
        audio_size = struct.unpack_from("<I", info, 28)[0]
        audio = data[resource_size : resource_size + audio_size]
    except (struct.error, ValueError):
        raise ValueError("Sonido de Source 2 en un formato que Revoz no conoce") from None
    if len(audio) != audio_size or not 1 <= channels <= 8 or not 4000 <= rate <= 192000:
        raise ValueError("Sonido de Source 2 en un formato que Revoz no conoce")
    if fmt == MP3:
        return audio, ".mp3"
    if fmt not in (PCM16, PCM8):
        raise ValueError("Sonido de Source 2 comprimido con ADPCM: todavía no se puede leer")
    width = 2 if fmt == PCM16 else 1
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(audio[: samples * channels * width] if samples else audio)
    return out.getvalue(), ".wav"


# --- extraer e instalar ---


def install_target(game_path: Path, item: Item) -> str:
    """Dónde va la voz traducida dentro del juego ("" si para ese formato todavía no se puede instalar)."""
    if item.inner.endswith(".vsnd_c"):
        return ""
    mod = item.vpk.parent.relative_to(game_path).as_posix()
    return f"{mod}/custom/revoz/{item.inner}"


def extracted_path(game_path: Path, item: Item, suffix: str) -> Path:
    pack = item.vpk.parent.relative_to(game_path) / item.vpk.name[: -len("_dir.vpk")]
    inner = Path(item.inner)
    return game_path / FOLDER / pack / inner.with_suffix(suffix)


def extract(game_path: Path, items: list[Item], out_dir: Path | None = None, progress=None) -> tuple[int, list[tuple[str, str]]]:
    """Extrae los audios a <juego>/Revoz/… (o a out_dir) y anota de dónde salió cada uno.
    Devuelve (extraídos, [(archivo, motivo del error)])."""
    base = out_dir or game_path / FOLDER
    mapping = load_map(base)
    indexes: dict[Path, tuple[dict, int]] = {}
    failed: list[tuple[str, str]] = []
    try:
        _extract_all(game_path, items, out_dir, base, mapping, indexes, failed, progress)
    finally:  # aunque se cancele a mitad, queda anotado de dónde salió cada audio ya extraído
        base.mkdir(parents=True, exist_ok=True)
        (base / MAP_FILE).write_text(json.dumps(mapping, ensure_ascii=False, indent=1), "utf-8")
    return len(items) - len(failed), failed


def _extract_all(game_path, items, out_dir, base, mapping, indexes, failed, progress) -> None:
    for n, item in enumerate(items, 1):
        try:
            if item.vpk not in indexes:
                indexes[item.vpk] = vpk.read_index(item.vpk)
            entries, start = indexes[item.vpk]
            data = vpk.read_file(item.vpk, entries[item.inner], start)
            suffix = Path(item.inner).suffix
            if suffix == ".vsnd_c":
                data, suffix = decode_vsnd(data)
            dst = extracted_path(game_path, item, suffix)
            if out_dir:
                dst = out_dir / dst.relative_to(game_path / FOLDER)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            mapping[dst.relative_to(base).as_posix()] = {
                "vpk": item.vpk.relative_to(game_path).as_posix(),
                "inner": item.inner,
                "target": install_target(game_path, item),
            }
        except (OSError, ValueError, vpk.VPKError, KeyError) as e:
            failed.append((item.inner, str(e)))
        if progress and (n % 50 == 0 or n == len(items)):
            progress(n, len(items))  # puede lanzar una excepción para cancelar


def load_map(base: Path) -> dict:
    try:
        return json.loads((base / MAP_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def targets(game_path: Path) -> dict[str, Path | None]:
    """Para cada audio extraído (por su ruta dentro del juego): dónde se instala, o None si todavía no se puede."""
    base = game_path / FOLDER
    out = {}
    for rel, info in load_map(base).items():
        key = f"{FOLDER}/{rel}"
        out[key] = game_path / info["target"] if info.get("target") else None
    return out
