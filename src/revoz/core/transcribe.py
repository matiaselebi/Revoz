"""Transcripción local y gratis con Whisper (faster-whisper). Corre en la PC, sin internet,
salvo la primera vez que se descarga el modelo elegido."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Modelos de Whisper: más grande = entiende mejor, pero tarda más y pesa más.
MODELS = {"tiny": 75, "base": 145, "small": 460, "medium": 1500}  # MB aproximados de descarga
DEFAULT_MODEL = "base"

# ponytail: umbrales fijos medidos con voces reales de Half-Life; ajustables si un juego da muchos falsos.
DOUBTFUL_LOGPROB = -0.5  # por debajo, Whisper no está seguro de lo que escuchó
NO_SPEECH_PROB = 0.6  # por encima, el segmento probablemente no es voz


@dataclass
class Transcript:
    text: str
    language: str
    no_voice: bool  # efectos, música o silencio
    doubtful: bool  # conviene revisarla a mano


def decode(path: Path, rate: int = 16000):
    """Decodifica cualquier audio que entienda FFmpeg (wav, ogg, mp3, flac…) a mono 16 kHz para Whisper."""
    import av
    import numpy as np

    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        chunks = []
        for frame in container.decode(container.streams.audio[0]):
            chunks += [f.to_ndarray() for f in resampler.resample(frame)]
        chunks += [f.to_ndarray() for f in resampler.resample(None)]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0


class Whisper:
    def __init__(self, model: str, models_dir: Path) -> None:
        if model not in MODELS:
            raise ValueError(f"modelo desconocido: {model}")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel  # import tardío: pesa y no siempre hace falta

        models_dir.mkdir(parents=True, exist_ok=True)
        self.name = f"whisper-{model}"
        self._model = WhisperModel(model, device="cpu", compute_type="int8", download_root=str(models_dir))

    def transcribe(self, path: Path, start: float | None = None, end: float | None = None, language: str | None = None) -> Transcript:
        audio = decode(path)
        if start is not None or end is not None:  # solo el tramo elegido (Desde / Hasta)
            rate = 16000
            audio = audio[int((start or 0) * rate) : int(end * rate) if end is not None else None]
        if audio.size == 0:
            return Transcript("", "", True, False)
        segments, info = self._model.transcribe(audio, language=language, vad_filter=True, beam_size=1)
        segments = [s for s in segments if s.no_speech_prob < NO_SPEECH_PROB]
        text = " ".join(s.text.strip() for s in segments).strip()
        doubtful = any(s.avg_logprob < DOUBTFUL_LOGPROB for s in segments)
        return Transcript(text, info.language, not text, doubtful)


def available() -> bool:
    try:
        import av  # noqa: F401
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True
