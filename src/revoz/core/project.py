"""Proyecto de traducción de un juego: sus líneas de voz, el texto, la traducción y el estado de revisión.

Se guarda como JSON en el perfil del usuario y se puede exportar/importar en CSV
(para repartir el trabajo entre varias personas o revisar en una planilla).
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from revoz.core import audio, scan

PENDING, TRANSCRIBED, TRANSLATED, REVIEWED = "pendiente", "transcrita", "traducida", "revisada"
STATUSES = [PENDING, TRANSCRIBED, TRANSLATED, REVIEWED]

# Formatos donde cada archivo es una sola línea de voz (los bancos .bnk/.fsb traen muchas juntas).
SINGLE_LINE_FORMATS = {"WAV / Wwise (.wem)", "Ogg Vorbis", "MP3", "FLAC"}

# ponytail: promedio de habla (~15 caracteres por segundo) para estimar el texto antes de transcribir.
CHARS_PER_SECOND = 15


@dataclass
class Line:
    id: str  # ruta relativa al juego, o el id que da el plugin
    path: str = ""  # archivo para escuchar ("" si el audio está dentro de un paquete)
    duration: float | None = None
    character: str = ""
    original: str = ""
    translation: str = ""
    status: str = PENDING


@dataclass
class Project:
    appid: str
    game: str
    language: str = ""
    provider: str = ""
    lines: list[Line] = field(default_factory=list)


def build_lines(game_path: Path, plugin=None) -> list[Line]:
    """Lista las líneas de voz: las da el plugin si hay uno; si no, los audios sueltos del juego."""
    if plugin is not None:
        return [Line(str(i)) for i in plugin.list_audio(game_path)]
    lines = []
    for dirpath, _, names in os.walk(game_path, onerror=lambda e: None):
        for n in names:
            p = Path(dirpath, n)
            hit = scan.identify(p)
            if hit and hit[0] in SINGLE_LINE_FORMATS:
                lines.append(Line(p.relative_to(game_path).as_posix(), str(p), audio.duration(p)))
    return sorted(lines, key=lambda line: line.id.lower())


def update(line: Line, field_name: str, value: str) -> None:
    """Cambia un campo y avanza el estado solo: con texto original pasa a transcrita, con traducción a traducida."""
    setattr(line, field_name, value)
    if field_name == "original" and value.strip() and line.status == PENDING:
        line.status = TRANSCRIBED
    elif field_name == "translation" and value.strip() and line.status in (PENDING, TRANSCRIBED):
        line.status = TRANSLATED


def progress(lines: list[Line]) -> dict[str, int]:
    """Cuántas líneas llegaron al menos a cada estado."""
    rank = {s: i for i, s in enumerate(STATUSES)}
    return {s: sum(rank.get(line.status, 0) >= rank[s] for line in lines) for s in STATUSES[1:]}


@dataclass
class Estimate:
    minutes: float
    without_duration: int
    chars: int
    cost: float


def estimate(lines: list[Line], usd_per_minute: float, usd_per_million_chars: float) -> Estimate:
    seconds = sum(line.duration or 0 for line in lines)
    without = sum(line.duration is None for line in lines)
    chars = sum(len(line.original) if line.original else int((line.duration or 0) * CHARS_PER_SECOND) for line in lines)
    cost = seconds / 60 * usd_per_minute + chars / 1_000_000 * usd_per_million_chars
    return Estimate(seconds / 60, without, chars, cost)


# --- guardar / cargar ---


def load(file: Path) -> Project | None:
    try:
        d = json.loads(file.read_text("utf-8"))
        lines = [Line(**{k: v for k, v in x.items() if k in Line.__dataclass_fields__}) for x in d.pop("lines")]
        return Project(**{k: v for k, v in d.items() if k in Project.__dataclass_fields__}, lines=lines)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save(file: Path, project: Project) -> None:
    # Se escribe a un archivo temporal y después se reemplaza: si se corta la luz no se pierde el proyecto.
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(project), ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, file)


# --- CSV ---

CSV_COLUMNS = ["id", "personaje", "original", "traduccion", "estado", "duracion"]


def _safe_cell(text: str) -> str:
    # Evita que Excel/Sheets ejecute una "fórmula" si un texto empieza con = + - @.
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def export_csv(file: Path, lines: list[Line]) -> None:
    with open(file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for line in lines:
            dur = f"{line.duration:.2f}" if line.duration is not None else ""
            w.writerow([_safe_cell(x) for x in (line.id, line.character, line.original, line.translation, line.status)] + [dur])


def import_csv(file: Path, lines: list[Line]) -> int:
    """Suma al proyecto lo que trae el CSV (por id). Las celdas vacías no borran nada. Devuelve cuántas líneas cambió."""
    by_id = {line.id: line for line in lines}
    changed = 0
    with open(file, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            line = by_id.get((row.get("id") or "").removeprefix("'"))
            if not line:
                continue
            before = asdict(line)
            for col, attr in (("personaje", "character"), ("original", "original"), ("traduccion", "translation")):
                if value := (row.get(col) or "").removeprefix("'"):
                    update(line, attr, value)
            if (row.get("estado") or "") in STATUSES:
                line.status = row["estado"]
            changed += asdict(line) != before
    return changed
