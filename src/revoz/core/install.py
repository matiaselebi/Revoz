"""Instalar la voz traducida en el juego y volver al original con un clic.

Antes de reemplazar un audio se guarda una copia del original en la carpeta de backups de Revoz
(nunca dentro del juego). Un manifiesto anota, por archivo, el hash del original y el del instalado:
- Restaurar solo pisa los archivos que siguen siendo los que puso Revoz.
- Si la tienda actualizó el juego y cambió un audio, se nota (el hash ya no coincide) y no se pisa a ciegas.
- Si algo falla a mitad de la instalación, lo que se instaló en esa pasada vuelve al original solo.

Audios extraídos de un paquete (por ejemplo un VPK de Valve): no se reemplaza la copia extraída sino el archivo
"destino" que indica el lector (ej. `hl2/custom/revoz/sound/...`). Si ese archivo no existía, restaurar lo borra.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from revoz.core import fit
from revoz.core.cache import file_hash

MANIFEST = "manifest.json"


@dataclass
class Result:
    done: int = 0  # archivos instalados o restaurados
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (archivo, motivo)
    changed: list[str] = field(default_factory=list)  # archivos que el juego cambió por su cuenta (actualización)


def _load(backup_dir: Path) -> dict:
    try:
        return json.loads((backup_dir / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return {"files": {}}


def _save(backup_dir: Path, manifest: dict) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    tmp = backup_dir / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, backup_dir / MANIFEST)


def _relative(path: Path, game_path: Path) -> str | None:
    """Ruta dentro del juego ("valve/sound/x.wav"), o None si el archivo no está adentro (nunca se toca nada afuera)."""
    try:
        return path.resolve().relative_to(game_path.resolve()).as_posix()
    except ValueError:
        return None


def installed(backup_dir: Path) -> int:
    return len(_load(backup_dir)["files"])


def changed(game_path: Path, backup_dir: Path) -> list[str]:
    """Archivos instalados que ya no son los de Revoz: los reemplazó una actualización del juego."""
    out = []
    for rel, info in _load(backup_dir)["files"].items():
        current = game_path / rel
        if not current.is_file() or file_hash(current) != info["installed"]:
            out.append(rel)
    return out


NOT_YET = "Este formato se extrajo para traducirlo, pero instalarlo en el juego todavía no se puede"


def install(lines, game_path: Path, backup_dir: Path, progress=None, targets: dict | None = None) -> Result:
    """Reemplaza los audios del juego por la voz traducida (en el mismo formato que el original).
    lines: líneas del proyecto; se instalan las que tienen voz generada. progress(hechos, total).
    targets: {id de línea: archivo destino, o None si no se puede instalar} para audios extraídos de paquetes."""
    targets = targets or {}
    manifest = _load(backup_dir)
    manifest["game_path"] = str(game_path)
    files = manifest["files"]
    result = Result()
    todo = [line for line in lines if line.dub_path and Path(line.dub_path).is_file()]
    this_run: list[str] = []
    try:
        for n, line in enumerate(todo, 1):
            extracted = line.id in targets
            if extracted and targets[line.id] is None:
                result.skipped.append((line.id, NOT_YET))
                continue
            target = Path(targets[line.id]) if extracted else Path(line.path)
            rel = _relative(target, game_path)
            if rel is None or (not extracted and not target.is_file()):
                result.skipped.append((line.id, "El audio no está suelto en la carpeta del juego"))
                continue
            backup = backup_dir / "files" / rel
            if rel not in files or not target.is_file() or file_hash(target) != files[rel]["installed"]:
                if target.is_file():
                    # Primera vez, o el juego trajo un audio nuevo en una actualización: ese es el original ahora
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    files[rel] = {"original": file_hash(backup), "installed": ""}
                else:  # archivo nuevo (ej. en la carpeta custom de Valve): restaurar = borrarlo
                    files[rel] = {"original": "", "installed": ""}
            reference = Path(line.path) if extracted else backup  # de dónde sale el formato del original
            try:
                fit.encode_like(Path(line.dub_path), reference, target)
            except ValueError as e:  # formato que no se sabe escribir: el original queda intacto
                result.skipped.append((line.id, str(e)))
                if not files[rel]["installed"]:
                    del files[rel]
                    backup.unlink(missing_ok=True)
                continue
            except BaseException:
                if not files[rel]["installed"]:
                    del files[rel]  # no llegó a instalarse: no queda anotado
                raise
            files[rel]["installed"] = file_hash(target)
            this_run.append(rel)
            result.done += 1
            _save(backup_dir, manifest)
            if progress:
                progress(n, len(todo))
    except BaseException:
        if this_run:
            restore(game_path, backup_dir, only=set(this_run))  # vuelta atrás automática
        raise
    manifest["date"] = datetime.now().isoformat(timespec="seconds")
    _save(backup_dir, manifest)
    return result


def restore(game_path: Path, backup_dir: Path, only: set[str] | None = None) -> Result:
    """Vuelve a poner los audios originales. Si el juego cambió un archivo por su cuenta, no lo pisa."""
    manifest = _load(backup_dir)
    files = manifest["files"]
    result = Result()
    for rel in [r for r in files if only is None or r in only]:
        target, backup = game_path / rel, backup_dir / "files" / rel
        if target.is_file() and file_hash(target) != files[rel]["installed"]:
            result.changed.append(rel)  # lo actualizó la tienda: ese ya es el original nuevo
        elif not files[rel]["original"]:  # lo creó Revoz: volver al original es borrarlo
            target.unlink(missing_ok=True)
            result.done += 1
        elif backup.is_file():
            tmp = target.with_name(target.name + ".revoz-tmp")
            shutil.copy2(backup, tmp)
            os.replace(tmp, target)
            result.done += 1
        else:
            result.skipped.append((rel, "Falta la copia del original"))
            continue
        backup.unlink(missing_ok=True)
        del files[rel]
    if files:
        _save(backup_dir, manifest)
    else:
        shutil.rmtree(backup_dir, ignore_errors=True)
    return result
