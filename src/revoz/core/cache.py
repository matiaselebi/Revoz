"""Caché de transcripciones y traducciones: lo que ya se hizo (y se pagó) no se vuelve a pedir.

- Transcripción: se identifica por el contenido del audio (hash) y el modelo de Whisper.
  Si dos juegos traen el mismo archivo, o se reinstala el juego, se reutiliza.
- Traducción: se identifica por el texto, el idioma destino, la IA, el modelo y todo lo que
  cambia el resultado (glosario, personaje, contexto). Si algo de eso cambia, se traduce de nuevo.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def translation_key(text: str, target: str, provider: str, model: str, extra: object) -> str:
    raw = json.dumps([text, target, provider, model, extra], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class Cache:
    def __init__(self, file: Path) -> None:
        file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # el motor corre en un hilo aparte
        self._db = sqlite3.connect(str(file), check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS transcripts (
                audio TEXT, model TEXT, text TEXT, language TEXT, no_voice INTEGER, doubtful INTEGER,
                PRIMARY KEY (audio, model));
            CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, text TEXT);
            """
        )

    def get_transcript(self, audio: str, model: str) -> tuple[str, str, bool, bool] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT text, language, no_voice, doubtful FROM transcripts WHERE audio = ? AND model = ?", (audio, model)
            ).fetchone()
        return (row[0], row[1], bool(row[2]), bool(row[3])) if row else None

    def put_transcript(self, audio: str, model: str, text: str, language: str, no_voice: bool, doubtful: bool) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO transcripts VALUES (?, ?, ?, ?, ?, ?)",
                (audio, model, text, language, int(no_voice), int(doubtful)),
            )

    def get_translation(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT text FROM translations WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put_translation(self, key: str, text: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO translations VALUES (?, ?)", (key, text))

    def close(self) -> None:
        with self._lock:
            self._db.close()
