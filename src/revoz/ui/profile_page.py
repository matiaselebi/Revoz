"""Perfil local: solo personaliza la app. No hay cuentas ni inicio de sesión; nada sale de la PC."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from revoz.core import providers
from revoz.ui import i18n, themes
from revoz.ui.i18n import tr

MAX_NAME = 40


def avatar_pixmap(name: str, avatar: Path | None, size: int) -> QPixmap:
    """Foto redonda; si no hay foto, un círculo con la inicial del nombre."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(clip)
    img = QPixmap(str(avatar)) if avatar and avatar.is_file() else QPixmap()
    if not img.isNull():
        p.drawPixmap(0, 0, img.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
    else:
        p.fillRect(0, 0, size, size, QColor("#1f6feb"))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(size * 0.45))
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (name.strip()[:1] or "?").upper())
    p.end()
    return pm


def _theme_swatch(name: str) -> QPixmap:
    """Vista previa del tema: fondo con un botón de color principal y una línea de texto."""
    if name == themes.SYSTEM:
        left, right = themes.THEMES["Claro"], themes.THEMES["Oscuro"]
    else:
        left = right = themes.THEMES[name]
    pm = QPixmap(54, 18)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(left["border"]))
    p.setBrush(QColor(left["bg"]))
    p.drawRoundedRect(QRectF(0.5, 0.5, 53, 17), 4, 4)
    if right is not left:  # Sistema: mitad clara, mitad oscura
        p.setBrush(QColor(right["bg"]))
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(27, 1, 26, 16))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(left["accent"]))
    p.drawRoundedRect(QRectF(5, 5, 14, 8), 2, 2)
    p.setBrush(QColor(right["text"]))
    p.drawRoundedRect(QRectF(24, 7, 24, 4), 2, 2)
    p.end()
    return pm


class ProfilePage(QWidget):
    def __init__(
        self,
        profile: dict,
        avatar_file: Path,
        provider_names: list[str],
        stats: dict,
        on_change,
        on_folders_changed,
        on_ui_language,
        actions,
    ) -> None:
        super().__init__()
        self.profile = profile
        self.avatar_file = avatar_file
        self.on_change = on_change
        self.on_folders_changed = on_folders_changed

        self.avatar = QLabel()
        change_photo = QPushButton(tr("Cambiar foto…"))
        change_photo.clicked.connect(self._pick_photo)
        remove_photo = QPushButton(tr("Quitar foto"))
        remove_photo.clicked.connect(self._remove_photo)
        photo_col = QVBoxLayout()
        photo_col.addWidget(self.avatar, alignment=Qt.AlignHCenter)
        photo_col.addWidget(change_photo)
        photo_col.addWidget(remove_photo)
        photo_col.addStretch()

        # --- datos personales ---
        self.name = QLineEdit(profile.get("name", ""), maxLength=MAX_NAME, placeholderText=tr("Tu nombre o apodo"))
        self.name.textChanged.connect(self._name_changed)
        about = QGroupBox(tr("Vos"))
        about_form = QFormLayout(about)
        about_form.addRow(tr("Nombre o apodo:"), self.name)
        note = QLabel(tr("Todo queda guardado solo en esta PC. Revoz no tiene cuentas ni manda tus datos a ningún lado."))
        note.setWordWrap(True)
        note.setObjectName("hint")
        about_form.addRow(note)

        # --- preferencias ---
        self.language = QComboBox()
        for code, lang in providers.TARGET_LANGUAGES:
            self.language.addItem(tr(lang), code)
        self.language.setCurrentIndex(max(0, self.language.findData(profile.get("language", "es-419"))))
        self.language.currentIndexChanged.connect(lambda: self._set("language", self.language.currentData()))
        self.provider = QComboBox()
        self.provider.addItems(provider_names)
        if profile.get("provider") in provider_names:
            self.provider.setCurrentText(profile["provider"])
        self.provider.currentTextChanged.connect(lambda t: self._set("provider", t))
        prefs = QGroupBox(tr("Preferencias"))
        prefs_form = QFormLayout(prefs)
        prefs_form.addRow(tr("Idioma al que traducís:"), self.language)
        prefs_form.addRow(tr("IA que usás normalmente:"), self.provider)
        hint = QLabel(tr("Cada juego arranca con estas opciones; después podés cambiarlas en el juego."))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        prefs_form.addRow(hint)

        # --- apariencia: idioma de la app y tema ---
        self.ui_language = QComboBox()
        for code, lang in i18n.LANGUAGES:
            self.ui_language.addItem(lang, code)  # cada idioma con su propio nombre, para encontrarlo siempre
        self.ui_language.setCurrentIndex(max(0, self.ui_language.findData(i18n.current())))
        self.ui_language.currentIndexChanged.connect(lambda: on_ui_language(self.ui_language.currentData()))
        self.theme = QComboBox()
        self.theme.setIconSize(QSize(54, 18))
        for name in themes.CHOICES:
            self.theme.addItem(QIcon(_theme_swatch(name)), tr(name), name)
        self.theme.setCurrentIndex(max(0, self.theme.findData(profile.get("theme", themes.DEFAULT))))
        self.theme.currentIndexChanged.connect(lambda: self._set("theme", self.theme.currentData()))
        theme_box = QGroupBox(tr("Apariencia"))
        theme_form = QFormLayout(theme_box)
        theme_form.addRow(tr("Idioma de la app:"), self.ui_language)
        theme_form.addRow(tr("Tema:"), self.theme)
        theme_hint = QLabel(
            tr("Revoz es el tema de la app. Sistema sigue el modo claro u oscuro de Windows. Alto contraste ayuda si te cuesta leer.")
        )
        theme_hint.setWordWrap(True)
        theme_hint.setObjectName("hint")
        theme_form.addRow(theme_hint)

        # --- carpetas de juegos ---
        self.folders = QListWidget()
        self.folders.setMaximumHeight(110)
        add_folder = QPushButton(tr("Agregar carpeta…"))
        add_folder.clicked.connect(self._add_folder)
        remove_folder = QPushButton(tr("Quitar"))
        remove_folder.clicked.connect(self._remove_folder)
        folder_buttons = QHBoxLayout()
        folder_buttons.addWidget(add_folder)
        folder_buttons.addWidget(remove_folder)
        folder_buttons.addStretch()
        folders_box = QGroupBox(tr("Carpetas de juegos"))
        folders_lay = QVBoxLayout(folders_box)
        explain = QLabel(
            tr("Para juegos que Revoz no detecta solo (no están en Steam, Epic ni GOG).") + " "
            + tr("Elegí la carpeta donde los tenés, por ejemplo D:\\Juegos: cada subcarpeta con un .exe aparece como un juego.")
        )
        explain.setWordWrap(True)
        explain.setObjectName("hint")
        folders_lay.addWidget(explain)
        folders_lay.addWidget(self.folders)
        folders_lay.addLayout(folder_buttons)
        self._fill_folders()

        # --- estadísticas ---
        stats_box = QGroupBox(tr("Tu actividad"))
        stats_form = QFormLayout(stats_box)
        for label, value in stats.items():
            stats_form.addRow(label + ":", QLabel(str(value)))

        # --- datos de la app ---
        data_box = QGroupBox(tr("Datos de Revoz"))
        data_lay = QHBoxLayout(data_box)
        for text, slot in actions:
            b = QPushButton(text)
            b.clicked.connect(slot)
            data_lay.addWidget(b)
        data_lay.addStretch()

        right = QVBoxLayout()
        title = QLabel(tr("Perfil"))
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        right.addWidget(title)
        for box in (about, prefs, theme_box, folders_box, stats_box, data_box):
            right.addWidget(box)
        right.addStretch()

        body = QHBoxLayout()
        body.addLayout(photo_col)
        body.addSpacing(20)
        body.addLayout(right, 1)

        content = QWidget()
        content.setLayout(body)
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root = QVBoxLayout(self)
        root.addWidget(scroll)
        self._refresh_avatar()

    def _fill_folders(self) -> None:
        self.folders.clear()
        for f in self.profile.get("library_folders", []):
            self.folders.addItem(f)
        if not self.folders.count():
            self.folders.addItem(tr("(ninguna)"))
            self.folders.item(0).setFlags(Qt.NoItemFlags)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Elegí la carpeta donde tenés varios juegos"))
        if not folder:
            return
        folders = self.profile.setdefault("library_folders", [])
        if folder not in folders:
            folders.append(folder)
            self.on_change()
            self._fill_folders()
            self.on_folders_changed()

    def _remove_folder(self) -> None:
        item = self.folders.currentItem()
        folders = self.profile.get("library_folders", [])
        if not item or item.text() not in folders:
            return
        folders.remove(item.text())
        self.on_change()
        self._fill_folders()
        self.on_folders_changed()

    def _set(self, key: str, value) -> None:
        self.profile[key] = value
        self.on_change()

    def _name_changed(self, text: str) -> None:
        self._set("name", text.strip())
        self._refresh_avatar()

    def _refresh_avatar(self) -> None:
        self.avatar.setPixmap(avatar_pixmap(self.profile.get("name", ""), self.avatar_file, 128))

    def _pick_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Elegí una foto"), str(Path.home() / "Pictures"), tr("Imágenes") + " (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Revoz", tr("No se pudo abrir esa imagen."))
            return
        # Se guarda una copia chica en PNG: no depende del archivo original y no ocupa lugar.
        self.avatar_file.parent.mkdir(parents=True, exist_ok=True)
        img.scaled(256, 256, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation).save(str(self.avatar_file), "PNG")
        self._refresh_avatar()
        self.on_change()

    def _remove_photo(self) -> None:
        self.avatar_file.unlink(missing_ok=True)
        self._refresh_avatar()
        self.on_change()
