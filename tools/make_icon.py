"""Dibuja el ícono de Revoz (sin archivos externos) y lo guarda como .ico (varios tamaños) y .png.

Uso:  python tools/make_icon.py [variante] [carpeta]
      variante: boca, erre, perfiles o sol; carpeta: dónde guardar (por defecto src/revoz/assets)
      python tools/make_icon.py preview <carpeta>   -> arma una imagen comparando las variantes
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")  # offscreen no tiene fuentes

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

SIZES = (16, 24, 32, 48, 64, 128, 256)
VARIANTS: tuple[str, ...] = ()  # se completa abajo, cuando están definidos los estilos de la R
CHOSEN = "erreplay:remates-agujero"  # el elegido: R con remates y el agujero de la pancita en forma de play
WAVE = ("#22d3ee", "#818cf8", "#e879f9", "#fb7185", "#fbbf24")  # de cian a amarillo: la voz que cambia


def _grad(x0, y0, x1, y1, *colors) -> QLinearGradient:
    g = QLinearGradient(x0, y0, x1, y1)
    for i, c in enumerate(colors):
        g.setColorAt(i / (len(colors) - 1), QColor(c))
    return g


def _wave(p: QPainter, cx: float, cy: float, s: float, heights, width: float, gap: float, colors=WAVE) -> None:
    p.setPen(Qt.NoPen)
    n = len(heights)
    for i, h in enumerate(heights):
        p.setBrush(QColor(colors[i % len(colors)]) if colors else QColor("#ffffff"))
        x = cx + (i - (n - 1) / 2) * s * gap
        w = s * width
        p.drawRoundedRect(QRectF(x - w / 2, cy - s * h / 2, w, s * h), w / 2, w / 2)


def _tile(p: QPainter, s: float, *colors) -> QRectF:
    """El cuadrado redondeado de fondo (como los íconos de apps), con un brillo suave arriba."""
    r = QRectF(s * 0.03, s * 0.03, s * 0.94, s * 0.94)
    p.setPen(Qt.NoPen)
    p.setBrush(_grad(0, 0, s * 0.6, s, *colors))
    p.drawRoundedRect(r, s * 0.22, s * 0.22)
    shine = QLinearGradient(0, 0, 0, s * 0.5)
    shine.setColorAt(0, QColor(255, 255, 255, 38))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(shine)
    p.drawRoundedRect(r, s * 0.22, s * 0.22)
    return r


def _sine(x0: float, y0: float, x1: float, y1: float, amp: float, waves: float, steps: int = 80) -> QPainterPath:
    """Una onda que va de (x0, y0) a (x1, y1)."""
    import math

    path = QPainterPath(QPointF(x0, y0))
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    for i in range(1, steps + 1):
        f = i / steps
        o = amp * math.sin(f * waves * 2 * math.pi) * math.sin(f * math.pi)  # más fuerte en el medio
        path.lineTo(x0 + dx * f + nx * o, y0 + dy * f + ny * o)
    return path


def boca(p: QPainter, s: float, small: bool) -> None:
    """Unos labios: lo que sale entre ellos es una onda de voz."""
    _tile(p, s, "#1e1b4b", "#4c1d95")
    upper = QPainterPath(QPointF(s * 0.14, s * 0.52))
    upper.cubicTo(s * 0.24, s * 0.40, s * 0.30, s * 0.30, s * 0.40, s * 0.31)
    upper.cubicTo(s * 0.45, s * 0.32, s * 0.48, s * 0.36, s * 0.50, s * 0.38)
    upper.cubicTo(s * 0.52, s * 0.36, s * 0.55, s * 0.32, s * 0.60, s * 0.31)
    upper.cubicTo(s * 0.70, s * 0.30, s * 0.76, s * 0.40, s * 0.86, s * 0.52)
    upper.cubicTo(s * 0.70, s * 0.47, s * 0.30, s * 0.47, s * 0.14, s * 0.52)
    lower = QPainterPath(QPointF(s * 0.14, s * 0.52))
    lower.cubicTo(s * 0.30, s * 0.58, s * 0.70, s * 0.58, s * 0.86, s * 0.52)
    lower.cubicTo(s * 0.76, s * 0.70, s * 0.62, s * 0.78, s * 0.50, s * 0.78)
    lower.cubicTo(s * 0.38, s * 0.78, s * 0.24, s * 0.70, s * 0.14, s * 0.52)
    p.setPen(Qt.NoPen)
    p.setBrush(_grad(s * 0.1, s * 0.3, s * 0.9, s * 0.8, "#fb7185", "#f43f5e", "#f97316"))
    p.drawPath(upper)
    p.drawPath(lower)
    if not small:  # brillo del labio de abajo
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QRectF(s * 0.40, s * 0.62, s * 0.14, s * 0.05))
    pen = QPen(_grad(s * 0.1, 0, s * 0.9, 0, *WAVE), s * (0.06 if small else 0.032), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_sine(s * 0.08, s * 0.53, s * 0.92, s * 0.53, s * (0.07 if small else 0.09), 3 if small else 4))


BLUE = ("#3b82f6", "#1d4ed8", "#1e3a8a")
R_FONTS = {  # estilo -> (fuente, peso, cursiva)
    "bahnschrift": ("Bahnschrift", QFont.Bold, False),
    "segoe": ("Segoe UI Black", QFont.Black, False),
    "redondeada": ("Arial Rounded MT Bold", QFont.Bold, False),
    "rockwell": ("Rockwell", QFont.Black, False),
    "bauhaus": ("Bauhaus 93", QFont.Normal, False),
    "century": ("Century Gothic", QFont.Bold, False),
    "georgia": ("Georgia", QFont.Bold, True),
    "impact": ("Impact", QFont.Normal, False),
}


def erre(p: QPainter, s: float, small: bool, style: str = "bahnschrift") -> None:
    """La R de Revoz, blanca y de un solo color, sobre azul."""
    _tile(p, s, *BLUE)
    family, weight, italic = R_FONTS[style]
    f = QFont(family)
    f.setWeight(weight)
    f.setItalic(italic)
    f.setPixelSize(100)
    glyph = QPainterPath()
    glyph.addText(0, 0, f, "R")
    box = glyph.boundingRect()
    scale = s * (0.62 if small else 0.56) / max(box.width(), box.height())
    p.save()
    p.translate(s * 0.5, s * 0.5)  # centrar la letra por su forma real, no por la caja de la fuente
    p.scale(scale, scale)
    p.translate(-box.center())
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawPath(glyph)
    p.restore()


def perfiles(p: QPainter, s: float, small: bool) -> None:
    """Dos caras de perfil hablando: de una sale la voz y le llega a la otra."""
    _tile(p, s, "#f59e0b", "#db2777")

    def head(mirror: bool) -> QPainterPath:
        pts = [  # perfil mirando a la derecha: frente, nariz, boca, mentón, cuello
            (0.10, 0.22), (0.22, 0.18), (0.30, 0.24), (0.33, 0.36), (0.38, 0.45), (0.33, 0.48),
            (0.34, 0.53), (0.31, 0.56), (0.33, 0.60), (0.28, 0.66), (0.22, 0.68), (0.20, 0.80), (0.03, 0.80), (0.03, 0.24),
        ]
        path = QPainterPath()
        for i, (x, y) in enumerate(pts):
            x = 1 - x if mirror else x
            path.moveTo(s * x, s * y) if i == 0 else path.lineTo(s * x, s * y)
        path.closeSubpath()
        return path

    tile = QPainterPath()
    tile.addRoundedRect(QRectF(s * 0.03, s * 0.03, s * 0.94, s * 0.94), s * 0.22, s * 0.22)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1e1b4b"))
    p.drawPath(head(False).intersected(tile))
    p.drawPath(head(True).intersected(tile))
    arcs = (0.08, 0.16) if small else (0.06, 0.12, 0.18)
    for i, r in enumerate(arcs):
        p.setPen(QPen(QColor("#ffffff"), s * (0.06 if small else 0.035), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        c = QPointF(s * 0.36, s * 0.51)
        p.drawArc(QRectF(c.x() - s * r, c.y() - s * r, 2 * s * r, 2 * s * r), -45 * 16, 90 * 16)


def sol(p: QPainter, s: float, small: bool) -> None:
    """Atardecer synthwave: el sol está hecho de barras de audio y abajo una grilla de videojuego retro."""
    import math

    r = _tile(p, s, "#0b1026", "#3b0764")
    horizon = s * 0.62
    cx, cy, rad = s * 0.5, s * 0.50, s * 0.30
    n = 7 if small else 13
    bar_w = 2 * rad / n
    for i in range(n):  # cada barra llega hasta el borde del círculo: barras de audio con forma de sol
        x = cx - rad + bar_w * (i + 0.5)
        half = math.sqrt(max(0.0, rad * rad - (x - cx) ** 2))
        top = cy - half
        p.setPen(Qt.NoPen)
        p.setBrush(_grad(0, cy - rad, 0, horizon, "#fde047", "#fb923c", "#ec4899"))
        p.drawRoundedRect(QRectF(x - bar_w * 0.36, top, bar_w * 0.72, horizon - top), bar_w * 0.3, bar_w * 0.3)
    clip = QPainterPath()
    clip.addRoundedRect(r, s * 0.22, s * 0.22)
    p.setClipPath(clip)
    p.fillRect(QRectF(0, horizon, s, s - horizon), _grad(0, horizon, 0, s, "#4c1d95", "#1e1b4b"))
    if not small:  # grilla en perspectiva
        p.setPen(QPen(QColor("#e879f9"), s * 0.012))
        for k in range(1, 6):
            y = horizon + (s - horizon) * (k / 6) ** 1.6
            p.drawLine(QPointF(0, y), QPointF(s, y))
        for k in range(-6, 7):
            p.drawLine(QPointF(cx + k * s * 0.04, horizon), QPointF(cx + k * s * 0.22, s))
    p.setPen(QPen(QColor("#f0abfc"), s * (0.03 if small else 0.016)))
    p.drawLine(QPointF(0, horizon), QPointF(s, horizon))
    p.setClipping(False)


def _rounded(path: QPainterPath, radius: float) -> QPainterPath:
    """Redondea las puntas de una forma: la achica y la vuelve a agrandar con bordes redondos."""
    from PySide6.QtGui import QPainterPathStroker

    st = QPainterPathStroker()
    st.setWidth(radius * 2)
    st.setJoinStyle(Qt.RoundJoin)
    st.setCapStyle(Qt.RoundCap)
    return path.united(st.createStroke(path)).simplified()


def _play(x0: float, top: float, bottom: float, x1: float, radius: float) -> QPainterPath:
    """Triángulo de play (apunta a la derecha) con las puntas redondeadas. Se dibuja un poco más chico y el
    redondeo lo devuelve al tamaño pedido."""
    tri = QPainterPath(QPointF(x0 + radius, top + radius * 1.6))
    tri.lineTo(x0 + radius, bottom - radius * 1.6)
    tri.lineTo(x1 - radius * 1.8, (top + bottom) / 2)
    tri.closeSubpath()
    return _rounded(tri, radius)


def _rect(x: float, y: float, w: float, h: float) -> QPainterPath:
    r = QPainterPath()
    r.addRect(QRectF(x, y, w, h))
    return r


def _runs(glyph: QPainterPath, points) -> list[tuple[float, float]]:
    """Tramos (desde, hasta) donde la línea de puntos cae dentro de la letra."""
    runs, inside, first = [], False, 0.0
    for v, pt in points:
        now = glyph.contains(pt)
        if now and not inside:
            first = v
        if inside and not now:
            runs.append((first, v))
        inside = now
    if inside:
        runs.append((first, v))
    return runs


def _r_glyph(family: str, weight, mode: str, wide: float = 0.95) -> QPainterPath:
    """La R de una fuente (en unidades de 100 px) con la pata cambiada por un signo de play.
    El corte se hace en los bordes reales de la letra: el lado derecho del palo y el final de la pancita.
    mode: "" (la R tal cual), "pata" (play pegado al palo) o "separado" (play suelto)."""
    f = QFont(family)
    f.setWeight(weight)
    f.setItalic(False)
    f.setPixelSize(100)
    glyph = QPainterPath()
    glyph.setFillRule(Qt.WindingFill)
    glyph.addText(0, 0, f, "R")
    glyph = glyph.simplified()
    if not mode:
        return glyph
    b = glyph.boundingRect()
    steps = [b.top() + b.height() * i / 400 for i in range(401)]
    # final de la pancita: siguiendo una línea vertical por el medio de la panza, el fin del segundo tramo lleno
    bowl_x = b.left() + b.width() * 0.58
    runs = _runs(glyph, [(y, QPointF(bowl_x, y)) for y in steps])
    split = runs[1][1] if len(runs) > 1 else b.top() + b.height() * 0.52
    # lado derecho del palo, a la altura de la pata
    y_mid = (split + b.bottom()) / 2
    xs = [b.left() + b.width() * i / 400 for i in range(401)]
    stem_right = _runs(glyph, [(x, QPointF(x, y_mid)) for x in xs])[0][1]
    stem = _rect(b.left() - 5, b.top() - 5, stem_right - b.left() + 5, b.height() + 10)
    upper = _rect(b.left() - 5, b.top() - 5, b.width() + 10, split - b.top() + 5)
    base = glyph.intersected(stem.united(upper)).simplified()
    radius = b.width() * 0.045
    gap = b.width() * 0.07 if mode == "separado" else -1.0
    top = split + (b.height() * 0.05 if mode == "separado" else -1.5)
    h = b.bottom() - top
    play = _play(stem_right + gap, top, b.bottom(), stem_right + gap + h * wide, radius)
    return base.united(play).simplified()


def _poly(*pts) -> QPainterPath:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    path.closeSubpath()
    return path


def _build_r(style: str, leg: str, counter: str) -> QPainterPath:
    """R dibujada con formas, en una caja de 100x100.
    style: "sans" (redondeada) o "slab" (con remates rectos, estilo Rockwell).
    leg: "play" (la pata es un play pegado al palo), "suelto" (play separado) o "diagonal" (pata común).
    counter: "redondo" (agujero común) o "play" (el agujero de la pancita tiene forma de play)."""
    round_ = style == "sans"
    stem_l, stem_r, top, bottom, bowl_bottom = 22.0, 42.0, 10.0, 90.0, 56.0
    shape = QPainterPath()
    shape.setFillRule(Qt.WindingFill)
    stem = QPainterPath()
    stem.addRoundedRect(QRectF(stem_l, top, stem_r - stem_l, bottom - top), 6 if round_ else 1, 6 if round_ else 1)
    shape = shape.united(stem)
    # pancita: semicírculo achatado pegado al palo
    outer = QPainterPath()
    outer.addRoundedRect(QRectF(stem_l, top, 60, bowl_bottom - top), (bowl_bottom - top) / 2, (bowl_bottom - top) / 2)
    outer = outer.united(_rect(stem_l, top, 30, bowl_bottom - top))
    if counter == "play":
        hole = _rounded(_poly((stem_r + 2, top + 15), (stem_r + 2, bowl_bottom - 15), (stem_r + 21, (top + bowl_bottom) / 2)), 2.5)
    else:
        hole = QPainterPath()
        h = bowl_bottom - top - 30
        hole.addRoundedRect(QRectF(stem_r, top + 15, 24, h), h / 2, h / 2)
        hole = hole.united(_rect(stem_r, top + 15, 10, h))
    shape = shape.united(outer.subtracted(hole))
    if leg == "diagonal":
        shape = shape.united(_rounded(_poly((50, bowl_bottom - 6), (66, bowl_bottom - 6), (84, bottom - 3), (68, bottom - 3)), 3 if round_ else 0.5))
    else:
        gap = 7.0 if leg == "suelto" else -2.0
        y0 = bowl_bottom + (6 if leg == "suelto" else -4)
        h = bottom - y0
        shape = shape.united(_play(stem_r + gap, y0, bottom, stem_r + gap + h * 1.05, 3.5))
    if not round_:  # remates rectos arriba y abajo del palo
        shape = shape.united(_rect(stem_l - 9, top, 9 + (stem_r - stem_l) + 4, 7))
        shape = shape.united(_rect(stem_l - 9, bottom - 7, 9 + (stem_r - stem_l) + 9, 7))
        if leg == "diagonal":  # remate recto también al pie de la pata
            shape = shape.united(_rect(64, bottom - 7, 28, 7))
    return shape.simplified()


R_PLAY = {  # nombre -> (estilo, pata, agujero)
    "redonda-play": ("sans", "play", "redondo"),
    "redonda-suelto": ("sans", "suelto", "redondo"),
    "remates-play": ("slab", "play", "redondo"),
    "remates-suelto": ("slab", "suelto", "redondo"),
    "redonda-agujero": ("sans", "diagonal", "play"),
    "remates-agujero": ("slab", "diagonal", "play"),
    "redonda-doble": ("sans", "play", "play"),
    "remates-doble": ("slab", "play", "play"),
}


def erreplay(p: QPainter, s: float, small: bool, style: str = "georgia-pata") -> None:
    """La R de Revoz blanca sobre azul, con el signo de play integrado."""
    _tile(p, s, *BLUE)
    glyph = _build_r(*R_PLAY[style])
    box = glyph.boundingRect()
    scale = s * (0.62 if small else 0.56) / max(box.width(), box.height())
    p.save()
    p.translate(s * 0.5, s * 0.5)
    p.scale(scale, scale)
    p.translate(-box.center())
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawPath(glyph)
    p.restore()


VARIANTS = tuple(f"erreplay:{k}" for k in R_PLAY)


def draw(variant: str, size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    name, _, style = variant.partition(":")
    globals()[name](p, float(size), size <= 32, *([style] if style else []))
    p.end()
    return img


def _png(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def save_ico(variant: str, path: Path) -> None:
    """.ico con todos los tamaños (cada uno como PNG adentro, lo que Windows Vista+ entiende)."""
    images = [_png(draw(variant, s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(SIZES, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(images))


def preview(folder: Path) -> Path:
    """Una imagen con las variantes en grande, en tamaños chicos, y sobre fondo claro y oscuro."""
    col, row_h = 290, 470
    per_row = 4
    rows = (len(VARIANTS) + per_row - 1) // per_row
    w, h = per_row * col + 20, rows * row_h
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("#f3f4f6"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont("Segoe UI", 1, QFont.Bold)
    f.setPixelSize(22)
    p.setFont(f)
    for r in range(rows):
        p.fillRect(QRectF(0, r * row_h + 380, w, 90), QColor("#111827"))  # barra de tareas oscura
    for i, v in enumerate(VARIANTS):
        x, y0 = 15 + (i % per_row) * col, (i // per_row) * row_h
        p.setPen(QColor("#111827"))
        p.drawText(QRectF(x, y0 + 6, 256, 30), Qt.AlignCenter, f"{i + 1}. {v.partition(':')[2].replace('-', ' + ').capitalize() or v.capitalize()}")
        p.drawImage(x, y0 + 40, draw(v, 256))
        sx = x + 10
        for size in (64, 48, 32, 24, 16):
            p.drawImage(int(sx), y0 + 340 - size // 2, draw(v, size))
            p.drawImage(int(sx), y0 + 425 - size // 2, draw(v, size))
            sx += size + 10
    p.end()
    out = folder / "iconos_opciones.png"
    img.save(str(out))
    return out


if __name__ == "__main__":
    app = QGuiApplication([])
    if len(sys.argv) > 1 and sys.argv[1] == "preview":
        print(preview(Path(sys.argv[2])))
    else:
        variant = sys.argv[1] if len(sys.argv) > 1 else CHOSEN
        folder = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "src" / "revoz" / "assets"
        save_ico(variant, folder / "revoz.ico")
        draw(variant, 256).save(str(folder / "revoz.png"))
        print("ok", folder)
