"""Reproductor de la pantalla de traducción: onda del sonido (como Audacity), play/pausa, anterior/siguiente,
barra de tiempo y elegir el tramo a traducir arrastrando sobre la onda."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QVBoxLayout, QWidget

from revoz.core import project as pj
from revoz.ui.i18n import tr

WAVE_RATE = 8000  # para dibujar alcanza con poca resolución: decodifica rápido aunque el audio sea largo


class Waveform(QWidget):
    """Dibuja la onda, el tramo elegido y por dónde va la reproducción."""

    seek = Signal(float)  # clic: ir a ese segundo
    range_chosen = Signal(float, float)  # arrastre: tramo elegido (desde, hasta)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(90)
        self.setCursor(Qt.IBeamCursor)
        self.samples = None
        self.duration = 0.0
        self.position = 0.0
        self.clip: tuple[float | None, float | None] = (None, None)
        self._drag_from: float | None = None
        self._drag_to: float | None = None
        self._peaks: list[tuple[float, float]] = []
        self._peaks_width = 0
        self.message = ""

    def set_audio(self, samples, duration: float) -> None:
        self.samples, self.duration, self.position = samples, duration, 0.0
        self._peaks_width = 0  # recalcular al dibujar
        self.update()

    def _compute_peaks(self, width: int) -> None:
        import numpy as np

        self._peaks, self._peaks_width = [], width
        if self.samples is None or not len(self.samples) or width <= 0:
            return
        chunks = np.array_split(self.samples, width)
        self._peaks = [(float(c.min()), float(c.max())) if len(c) else (0.0, 0.0) for c in chunks]

    def _x_to_time(self, x: float) -> float:
        return max(0.0, min(self.duration, x / max(1, self.width()) * self.duration))

    def _time_to_x(self, t: float) -> float:
        return t / self.duration * self.width() if self.duration else 0.0

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0d1117"))
        if self.samples is None:
            p.setPen(QColor("#8b949e"))
            p.drawText(self.rect(), Qt.AlignCenter, self.message or tr("Tocá ▶ en una línea para escucharla"))
            return
        if self._peaks_width != w:
            self._compute_peaks(w)
        # onda
        mid = h / 2
        p.setPen(QPen(QColor("#58a6ff"), 1))
        for x, (lo, hi) in enumerate(self._peaks):
            p.drawLine(x, int(mid - hi * mid * 0.95), x, int(mid - lo * mid * 0.95))
        p.setPen(QColor("#30363d"))
        p.drawLine(0, int(mid), w, int(mid))
        # tramo elegido (o el que se está arrastrando): lo de afuera se oscurece y los bordes van en verde
        start, end = self.clip
        if self._drag_from is not None and self._drag_to is not None:
            start, end = sorted((self._drag_from, self._drag_to))
        if start is not None or end is not None:
            x1, x2 = self._time_to_x(start or 0), self._time_to_x(end if end is not None else self.duration)
            shade = QColor(0, 0, 0, 150)
            p.fillRect(QRectF(0, 0, x1, h), shade)
            p.fillRect(QRectF(x2, 0, w - x2, h), shade)
            p.setPen(QPen(QColor("#3fb950"), 2))
            p.drawLine(int(x1), 0, int(x1), h)
            p.drawLine(int(x2), 0, int(x2), h)
        # por dónde va
        x = self._time_to_x(self.position)
        p.setPen(QPen(QColor("#f0f6fc"), 2))
        p.drawLine(int(x), 0, int(x), h)

    def mousePressEvent(self, event) -> None:
        if self.samples is not None and event.button() == Qt.LeftButton:
            self._drag_from = self._drag_to = self._x_to_time(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None:
            self._drag_to = self._x_to_time(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_from is None:
            return
        a, b = sorted((self._drag_from, self._x_to_time(event.position().x())))
        self._drag_from = self._drag_to = None
        if b - a < 0.15:  # fue un clic, no un arrastre
            self.seek.emit(a)
        else:
            self.range_chosen.emit(round(a, 1), round(b, 1))
        self.update()


class Player(QWidget):
    previous = Signal()
    next = Signal()
    clip_chosen = Signal(object, object)  # desde, hasta (None = todo)

    def __init__(self) -> None:
        super().__init__()
        self.line: pj.Line | None = None
        self._player = None
        self._dragging_slider = False

        self.title = QLabel(tr("Reproductor"))
        self.title.setStyleSheet("font-weight: bold;")
        self.wave = Waveform()
        self.wave.seek.connect(self._seek)
        self.wave.range_chosen.connect(lambda a, b: self.clip_chosen.emit(a, b))

        # Íconos de reproductor del sistema (los símbolos ⏮ ⏭ salen como emojis de color en Windows)
        icon = self.style().standardIcon
        self._icons = {"play": icon(QStyle.SP_MediaPlay), "pause": icon(QStyle.SP_MediaPause)}
        self.prev_btn = QPushButton(icon(QStyle.SP_MediaSkipBackward), "")
        self.play_btn = QPushButton(self._icons["play"], "")
        self.next_btn = QPushButton(icon(QStyle.SP_MediaSkipForward), "")
        for b, tip in ((self.prev_btn, tr("Anterior")), (self.play_btn, tr("Reproducir / pausar")), (self.next_btn, tr("Siguiente"))):
            b.setToolTip(tip)
            b.setFixedWidth(44)
        self.prev_btn.clicked.connect(self.previous)
        self.next_btn.clicked.connect(self.next)
        self.play_btn.clicked.connect(self.toggle)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging_slider", True))
        self.slider.sliderReleased.connect(self._slider_released)
        self.time = QLabel("0:00.0 / 0:00.0")
        whole = QPushButton(tr("Todo el audio"))
        whole.setToolTip(tr("Traducir el audio completo (borra Desde / Hasta)"))
        whole.clicked.connect(lambda: self.clip_chosen.emit(None, None))
        hint = QLabel(tr("Arrastrá sobre la onda para traducir solo esa parte."))
        hint.setObjectName("hint")

        controls = QHBoxLayout()
        for w in (self.prev_btn, self.play_btn, self.next_btn):
            controls.addWidget(w)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.time)
        controls.addWidget(whole)
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(hint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.wave)
        lay.addLayout(controls)

    # --- reproducción ---

    def _media(self):
        if self._player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._out = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._out)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(lambda ms: self.slider.setRange(0, ms))
            self._player.playbackStateChanged.connect(self._on_state)
        return self._player

    def load(self, line: pj.Line, play: bool = True) -> None:
        self.line = line
        self.title.setText(line.id)
        self.wave.clip = (line.clip_start, line.clip_end)
        if not line.path or not Path(line.path).is_file():
            self.wave.samples = None
            self.wave.message = tr("Este audio está dentro de un paquete del juego y no se puede escuchar directo.")
            self.wave.update()
            return
        try:
            from revoz.core.transcribe import decode

            samples = decode(Path(line.path), WAVE_RATE)
            self.wave.set_audio(samples, len(samples) / WAVE_RATE)
        except Exception:  # sin la onda igual se puede escuchar
            self.wave.samples = None
            self.wave.message = tr("No se pudo dibujar la onda de este audio.")
            self.wave.update()
        try:
            media = self._media()
        except ImportError:
            self.wave.message = tr("La reproducción de audio no está disponible en esta instalación.")
            return
        media.setSource(QUrl.fromLocalFile(line.path))
        if play:
            self.play()

    def load_file(self, path: Path, title: str) -> None:
        """Reproduce un audio suelto (la voz traducida), sin tramo."""
        self.load(pj.Line(title, str(path)))
        self.title.setText(title)

    def set_clip(self, start: float | None, end: float | None) -> None:
        self.wave.clip = (start, end)
        self.wave.update()

    def play(self) -> None:
        if self._player is None or self.line is None:
            return
        start = self.line.clip_start or 0.0
        if self.line.clip_start is not None and self._player.position() / 1000 < start:
            self._player.setPosition(int(start * 1000))  # con tramo elegido, arranca desde ahí
        self._player.play()

    def toggle(self) -> None:
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    def _seek(self, seconds: float) -> None:
        if self._player is not None:
            self._player.setPosition(int(seconds * 1000))

    def _slider_released(self) -> None:
        self._dragging_slider = False
        self._seek(self.slider.value() / 1000)

    def _on_position(self, ms: int) -> None:
        seconds = ms / 1000
        if not self._dragging_slider:
            self.slider.setValue(ms)
        total = (self._player.duration() or 0) / 1000
        self.time.setText(f"{pj.format_time(seconds)} / {pj.format_time(total)}")
        self.wave.position = seconds
        self.wave.update()
        end = self.line.clip_end if self.line else None
        if end is not None and seconds >= end:  # con tramo elegido, corta al llegar a "Hasta"
            self._player.pause()
            self._player.setPosition(int((self.line.clip_start or 0) * 1000))

    def _on_state(self, state) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        self.play_btn.setIcon(self._icons["pause" if state == QMediaPlayer.PlayingState else "play"])
