"""Lector de paquetes VPK de Valve (Source y Source 2), versiones 1 y 2. Solo lee: nunca escribe en el juego.

Formato (documentado por Valve y la comunidad): un archivo `*_dir.vpk` con el índice (árbol extensión -> carpeta ->
nombre) y los datos repartidos en `*_000.vpk`, `*_001.vpk`… o al final del propio `_dir.vpk`.
Cada archivo trae su CRC32, así que se puede comprobar que lo extraído está bien.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

SIGNATURE = 0x55AA1234
IN_DIR = 0x7FFF  # el archivo está dentro del propio _dir.vpk


class VPKError(Exception):
    pass


@dataclass
class Entry:
    crc: int
    preload: bytes
    archive: int
    offset: int
    length: int

    @property
    def size(self) -> int:
        return len(self.preload) + self.length


def _cstring(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def read_index(dir_vpk: Path) -> tuple[dict[str, Entry], int]:
    """{ruta interna: Entry} y dónde empiezan los datos guardados en el propio _dir.vpk."""
    data = dir_vpk.read_bytes()
    if len(data) < 12:
        raise VPKError("Archivo VPK demasiado corto")
    sig, version, tree_size = struct.unpack_from("<III", data, 0)
    if sig != SIGNATURE:
        raise VPKError("No es un índice VPK (*_dir.vpk)")
    header = {1: 12, 2: 28}.get(version)
    if header is None:
        raise VPKError(f"Versión de VPK desconocida: {version}")
    entries: dict[str, Entry] = {}
    pos, end = header, header + tree_size
    try:
        while pos < end:
            ext, pos = _cstring(data, pos)
            if not ext:
                break
            while True:
                folder, pos = _cstring(data, pos)
                if not folder:
                    break
                while True:
                    name, pos = _cstring(data, pos)
                    if not name:
                        break
                    crc, preload_len, archive, offset, length, term = struct.unpack_from("<IHHIIH", data, pos)
                    pos += 18
                    if term != 0xFFFF:
                        raise VPKError("Índice VPK dañado")
                    preload = data[pos : pos + preload_len]
                    pos += preload_len
                    path = "/".join(p for p in (folder.strip(), name) if p and p != " ")
                    if ext.strip():
                        path += "." + ext
                    entries[path.lower()] = Entry(crc, preload, archive, offset, length)
    except (ValueError, struct.error):
        raise VPKError("Índice VPK dañado") from None
    return entries, end


def archive_path(dir_vpk: Path, archive: int) -> Path:
    """pak01_dir.vpk + 3 -> pak01_003.vpk"""
    return dir_vpk.with_name(dir_vpk.name[: -len("dir.vpk")] + f"{archive:03d}.vpk")


def read_file(dir_vpk: Path, entry: Entry, data_start: int) -> bytes:
    """Los bytes de un archivo del paquete, comprobados con su CRC32."""
    body = b""
    if entry.length:
        if entry.archive == IN_DIR:
            source, offset = dir_vpk, data_start + entry.offset
        else:
            source, offset = archive_path(dir_vpk, entry.archive), entry.offset
        with open(source, "rb") as f:
            f.seek(offset)
            body = f.read(entry.length)
        if len(body) != entry.length:
            raise VPKError(f"Falta parte del archivo en {source.name}")
    data = entry.preload + body
    if zlib.crc32(data) != entry.crc:
        raise VPKError("El archivo extraído no coincide con su CRC32")
    return data
