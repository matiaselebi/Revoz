"""Ajuste del audio generado: misma duración que el original (sin cambiar el tono) y mismo formato.

El español suele durar 20-30 % más que el inglés. Para que el doblaje entre en el mismo tiempo, se acelera
con el filtro "atempo" de FFmpeg (viene con PyAV): cambia la velocidad sin hacer la voz más aguda.
Más de 1.35x ya suena apurado, así que hasta ahí se ajusta; si igual no entra, la línea queda marcada.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MAX_SPEEDUP = 1.35  # acelerar el audio ya hecho más que esto suena mal
NATIVE_MAX = 1.5  # la voz misma puede hablar hasta 1.5x más rápido y sigue sonando natural
TOLERANCE = 1.05  # hasta un 5 % más largo se deja como está


@dataclass
class FitResult:
    duration: float  # duración final en segundos
    tempo: float  # cuánto se aceleró (1 = nada)
    fits: bool  # False si ni acelerando al máximo entra en el tiempo pedido


def duration(path: Path) -> float:
    return _info(path)[0]


def _info(path: Path) -> tuple[float, int, str]:
    import av

    with av.open(str(path)) as c:
        s = c.streams.audio[0]
        frames = sum(f.samples for f in c.decode(s))
        return frames / s.rate, s.rate, s.layout.name


def process(src: Path, dst: Path, target: float | None = None, rate: int | None = None, layout: str | None = None, natural: bool = False) -> FitResult:
    """Copia src a dst como WAV, acelerándolo si hace falta para durar como mucho `target` segundos.
    rate/layout: frecuencia y canales del audio original del juego (para que el reemplazo sea compatible)."""
    import av

    duration, src_rate, src_layout = _info(src)
    tempo = 1.0
    fits = True
    if target and not natural and duration > target * TOLERANCE:
        tempo = min(duration / target, MAX_SPEEDUP)
        fits = duration / tempo <= target * TOLERANCE
    elif target and natural:
        fits = duration <= target * TOLERANCE

    rate, layout = rate or src_rate, layout or src_layout
    _transcode(src, dst, "wav", "pcm_s16le", rate, layout, "s16", tempo)
    return FitResult(duration / tempo, tempo, fits)


# Códec con el que se lee un audio -> codificador para escribir uno igual
_ENCODERS = {"vorbis": "vorbis", "mp3": "libmp3lame", "mp3float": "libmp3lame", "opus": "libopus", "flac": "flac"}


def encode_like(src: Path, original: Path, dst: Path) -> None:
    """Escribe src en dst con el mismo formato que el audio original del juego (contenedor, códec, frecuencia,
    canales y bits). Así el juego lo lee igual que el suyo. Lanza ValueError si ese formato no se puede escribir."""
    import av

    with av.open(str(original)) as c:
        s = c.streams.audio[0]
        container, codec, rate, layout, fmt = c.format.name.split(",")[0], s.codec_context.name, s.rate, s.layout.name, s.format.name
    encoder = codec if codec.startswith("pcm_") else _ENCODERS.get(codec)
    if not encoder:
        raise ValueError(f"No se puede escribir audio {codec}")
    try:
        formats = [f.name for f in av.codec.Codec(encoder, "w").audio_formats or []]
    except Exception:
        raise ValueError(f"No se puede escribir audio {codec}") from None
    if formats and fmt not in formats:
        fmt = formats[0]
    _transcode(src, dst, container, encoder, rate, layout, fmt)


def _transcode(src: Path, dst: Path, container: str, encoder: str, rate: int, layout: str, fmt: str, tempo: float = 1.0) -> None:
    import av

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    with av.open(str(src)) as inp, av.open(str(tmp), "w", format=container) as out:
        stream = inp.streams.audio[0]
        ostream = out.add_stream(encoder, rate=rate)
        ostream.layout = layout
        ostream.format = fmt
        if encoder == "vorbis":
            ostream.codec_context.options = {"strict": "experimental"}  # el de FFmpeg está marcado experimental
        graph = av.filter.Graph()
        nodes = [graph.add_abuffer(template=stream)]
        if abs(tempo - 1) > 0.005:
            nodes.append(graph.add("atempo", f"{tempo:.4f}"))
        nodes.append(graph.add("aresample", str(rate)))
        nodes.append(graph.add("aformat", f"sample_fmts={fmt}:channel_layouts={layout}"))
        nodes.append(graph.add("abuffersink"))
        for a, b in zip(nodes, nodes[1:]):
            a.link_to(b)
        graph.configure()
        fifo = av.audio.fifo.AudioFifo()  # algunos códecs (mp3, vorbis) piden bloques de un tamaño exacto

        def write(frame) -> None:
            frame.pts = None
            for packet in ostream.encode(frame):
                out.mux(packet)

        def drain(final: bool = False) -> None:
            while True:
                try:
                    fifo.write(graph.pull())
                except (av.error.BlockingIOError, av.error.EOFError):
                    break
            size = ostream.codec_context.frame_size or fifo.samples
            while size and fifo.samples >= size:
                write(fifo.read(size))
            if final and fifo.samples:
                write(fifo.read())

        for frame in inp.decode(stream):
            graph.push(frame)
            drain()
        graph.push(None)
        drain(final=True)
        for packet in ostream.encode(None):
            out.mux(packet)
    os.replace(tmp, dst)


def audio_format(path: Path) -> tuple[int, str] | None:
    """Frecuencia y canales del audio original (None si no se puede leer)."""
    try:
        _, rate, layout = _info(path)
        return rate, layout
    except Exception:
        return None
