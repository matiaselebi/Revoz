"""Interfaz PySide6. Solo muestra; la lógica vive en revoz.core."""

from __future__ import annotations

import html
import json
import os
import queue
import re
import shutil
import sys
import threading
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QFile,
    QFileInfo,
    QObject,
    QPointF,
    QProcess,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from revoz.core import keys, official, plugins, providers, scan, stores, transcribe, tts, valve
from revoz.core.cache import Cache
from revoz.core import project as pj
from revoz.core.plugins import GamePlugin, PluginInfo
from revoz.core.providers import Provider, ProviderError
from revoz.core.scan import ScanResult
from revoz.core.engine import Engine
from revoz.core.steam import Game
from revoz.ui import i18n, themes
from revoz.ui.i18n import N_, tr
from revoz.ui.profile_page import ProfilePage, avatar_pixmap
from revoz.ui.translation_page import TranslationPage
from revoz.version import VERSION

# Los plugins viven junto a la app (al lado del .exe, o en la raíz del proyecto al correr desde el código).
# ponytail: si algún día se instala en Program Files (sin permiso de escritura), mover plugins a APPDATA.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
PLUGIN_DIR = APP_DIR / "plugins"
# La lista de plugins aprobados queda en el perfil del usuario, no en la carpeta de la app:
# así un paquete descargado no puede traer plugins "pre-aprobados".
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Revoz"
CACHE_FILE = DATA_DIR / "scan_cache_v2.json"
TRUST_FILE = DATA_DIR / "trusted_plugins.json"
MANUAL_FILE = DATA_DIR / "manual_games.json"
PREFS_FILE = DATA_DIR / "preferred_versions.json"  # juego -> versión elegida
PRICES_FILE = DATA_DIR / "prices.json"  # proveedor -> precios que cargó el usuario
PROJECTS_DIR = DATA_DIR / "projects"  # un proyecto de traducción por versión de juego
PROFILE_FILE = DATA_DIR / "profile.json"  # perfil local: nombre y preferencias (sin cuentas)
AVATAR_FILE = DATA_DIR / "avatar.png"
WINDOW_FILE = DATA_DIR / "window.json"  # posición y tamaño de la ventana
GAME_PREFS_FILE = DATA_DIR / "game_prefs.json"  # juego -> IA e idioma elegidos la última vez
DISABLED_FILE = DATA_DIR / "disabled_plugins.json"  # plugins desactivados (siguen instalados)
MODELS_DIR = DATA_DIR / "models"  # modelos de Whisper descargados
ENGINE_CACHE = DATA_DIR / "cache.db"  # transcripciones y traducciones ya hechas
RESTART_FLAG = "--perfil"  # al reiniciar por cambio de idioma, abrir directo en el Perfil
STORE_COLOR = {"Epic": "#2a2a2a", "GOG": "#7b2fbe", "Folder": "#9a6700", "Manual": "#1f6feb"}

COVER = QSize(150, 225)
STATUS_TEXT = {
    "plugin": N_("Compatible gracias a un plugin"),
    "green": N_("Audio legible: fácil de traducir"),
    "yellow": N_("Audio empaquetado sin cifrar: necesita un plugin que lo extraiga"),
    "locked": N_("Audio cifrado: necesita un plugin con la clave del juego"),
    "red": N_("No se encontró audio"),
    "scanning": N_("Analizando…"),
    "pending": N_("En cola para analizar"),
}
BADGE_COLOR = {
    "plugin": "#2ea043",
    "green": "#2ea043",
    "yellow": "#d29922",
    "locked": "#da3633",
    "red": "#da3633",
    "scanning": "#6e7681",
    "pending": "#6e7681",
}
STATUS_RANK = ["plugin", "green", "yellow", "locked", "red"]  # de mejor a peor
STORE_NAMES = {"Folder": N_("Carpeta")}  # nombre a mostrar de cada origen
LIBRARY_FILTERS = [
    (N_("Todos los juegos"), ""),
    (N_("Fáciles de traducir"), "easy"),
    (N_("Necesitan plugin"), "needs_plugin"),
    (N_("Sin audio"), "none"),
    (N_("Analizándose"), "scanning"),
]
LIBRARY_FILTER_STATUSES = {
    "easy": {"green", "plugin"},
    "needs_plugin": {"yellow", "locked"},
    "none": {"red"},
    "scanning": {"pending", "scanning"},
}
KIND_TEXT = {scan.EASY: N_("legible"), scan.PACK: N_("empaquetado"), scan.LOCKED: N_("cifrado")}


def store_name(store: str) -> str:
    return tr(STORE_NAMES[store]) if store in STORE_NAMES else store

def cover_pixmap(versions: list[Game], status: str | None, size: QSize = COVER) -> QPixmap:
    """Carátula de un juego; versions = el mismo juego en una o más tiendas."""
    game = versions[0]
    pm = QPixmap(size)
    pm.fill(QColor("#2a2d34"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    cover = next((v.cover for v in versions if v.cover), None)
    src = QPixmap(str(cover)) if cover else QPixmap()
    if not src.isNull():
        p.drawPixmap(0, 0, src.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    else:
        p.setPen(QColor("#e6e6e6"))
        p.drawText(pm.rect().adjusted(10, 10, -10, -10), Qt.AlignCenter | Qt.TextWordWrap, game.name)

    # Etiqueta de tienda arriba a la derecha. Steam solo no lleva; varias tiendas: "STEAM + EPIC".
    if len(versions) > 1 or game.store in STORE_COLOR:
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(9, size.width() // 14))
        p.setFont(f)
        text = " + ".join(store_name(v.store).upper() for v in versions)
        w = min(p.fontMetrics().horizontalAdvance(text) + 10, size.width() - 12)
        h = p.fontMetrics().height() + 4
        tag = QRectF(size.width() - w - 6, 6, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(STORE_COLOR.get(game.store, "#444c56") if len(versions) == 1 else "#444c56"))
        p.drawRoundedRect(tag, 4, 4)
        p.setPen(QColor("white"))
        p.drawText(tag, Qt.AlignCenter, text)

    if status in BADGE_COLOR:
        d = size.width() * 0.22
        r = QRectF(8, size.height() - d - 8, d, d)
        p.setPen(QPen(QColor("white"), 2))
        p.setBrush(QColor(BADGE_COLOR[status]))
        p.drawEllipse(r)
        p.setPen(QPen(QColor("white"), max(2.0, d / 9), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y, w = r.x(), r.y(), r.width()
        if status in ("green", "plugin"):
            p.drawPolyline([QPointF(x + w * 0.27, y + w * 0.52), QPointF(x + w * 0.44, y + w * 0.68), QPointF(x + w * 0.74, y + w * 0.34)])
        elif status in ("red", "locked"):
            p.drawLine(QPointF(x + w * 0.32, y + w * 0.32), QPointF(x + w * 0.68, y + w * 0.68))
            p.drawLine(QPointF(x + w * 0.68, y + w * 0.32), QPointF(x + w * 0.32, y + w * 0.68))
        else:
            f = QFont()
            f.setBold(True)
            f.setPixelSize(int(w * 0.6))
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, "!" if status == "yellow" else "…")
    p.end()
    return pm


def _load_json(file: Path) -> dict:
    try:
        data = json.loads(file.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_json(file: Path, data: dict) -> None:
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(data, indent=1), "utf-8")
    except OSError:
        pass  # una preferencia que no se guarda no rompe nada


def version_label(g: Game) -> str:
    if g.store == "Manual":
        return tr("Carpeta agregada")
    if g.store == "Folder":
        return tr("Carpeta de juegos")
    return tr("{store} (original)", store=g.store)


def _open_folder(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class _Bus(QObject):
    """Lleva resultados de hilos de fondo al hilo de la UI."""

    started = Signal(str)
    scanned = Signal(str, object)
    checked = Signal(bool, str)
    official = Signal(object)  # idiomas oficiales según Steam ({nombre: con voces}) o None
    extract_progress = Signal(int, int)
    extracted = Signal(object)  # (extraídos, fallidos) o el error


class GamePage(QWidget):
    def __init__(
        self,
        game: Game,
        result: ScanResult,
        plugin: PluginInfo | None,
        provider_list: list[Provider],
        on_back,
        on_add_plugin,
        versions: list[Game],
        on_switch,
        on_add_folder,
        on_translate,
        defaults: dict,
        on_prefs_changed,
        on_extracted=None,  # después de extraer: volver a analizar el juego
    ) -> None:
        super().__init__()
        self.game, self.on_extracted = game, on_extracted or (lambda: None)
        self.providers = {p.name: p for p in provider_list}
        self.bus = _Bus()
        self.bus.checked.connect(self._on_checked)
        self.bus.official.connect(self._on_official)
        self._official: dict | None = None
        self.official_label = QLabel()
        self.official_label.setWordWrap(True)

        back = QPushButton(tr("← Biblioteca"))
        back.clicked.connect(on_back)

        status = "plugin" if plugin else result.status
        cover = QLabel()
        cover.setPixmap(cover_pixmap([game], status, QSize(200, 300)))

        title = QLabel(game.name)
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")

        # Versiones del mismo juego (tienda original, copia con mods, otra carpeta): elegir en cuál trabajar.
        version_row = QHBoxLayout()
        version_row.addWidget(QLabel(tr("Usando:")))
        if len(versions) > 1:
            version = QComboBox()
            for v in versions:
                version.addItem(f"{version_label(v)} - {v.path}", v.appid)
            version.setCurrentIndex([v.appid for v in versions].index(game.appid))
            version.currentIndexChanged.connect(lambda: on_switch(version.currentData()))
            version_row.addWidget(version, 1)
        else:
            version_row.addWidget(QLabel(f"{version_label(game)} - {game.path}"), 1)
        other = QPushButton(tr("Usar otra carpeta de este juego…"))
        other.setToolTip(tr("Por ejemplo, una copia con mods o sin mods. Revoz comprueba que sea el mismo juego."))
        other.clicked.connect(on_add_folder)
        version_row.addWidget(other)
        sizes = ", ".join(
            f"{tr(KIND_TEXT[k])} {b / 1048576:,.0f} MB" for k, b in sorted(result.bytes_by_kind.items(), key=lambda kv: -kv[1])
        )
        info = QLabel(
            tr("Tienda: {store}", store=store_name(game.store)) + "\n\n"
            + tr("Análisis de archivos: {status}", status=tr(STATUS_TEXT[result.status])) + "\n\n"
            + "\n".join("• " + tr("{format}: {n} archivos", format=fmt, n=n) for fmt, n in sorted(result.counts.items()))
            + ("\n\n" + tr("Tamaño: {sizes}", sizes=sizes) if sizes else "")
            + "\n\n" + tr("Carpeta: {path}", path=game.path) + "\n" + tr("Archivos analizados: {n}", n=result.files)
        )
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # --- IA, clave e idioma ---
        self.provider = QComboBox()
        self.provider.addItems(list(self.providers))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText(tr("Pegá tu API key"))
        self.key_state = QLabel()
        save = self.save_btn = QPushButton(tr("Guardar"))
        save.clicked.connect(self._save_key)
        delete = self.delete_btn = QPushButton(tr("Borrar"))
        delete.clicked.connect(self._delete_key)
        self.provider.currentTextChanged.connect(self._on_provider_changed)

        self.language = QComboBox()
        for code, name in providers.TARGET_LANGUAGES:
            self.language.addItem(tr(name), code)
        self.language.currentIndexChanged.connect(lambda: self.lang_state.clear())
        self.language.currentIndexChanged.connect(self._show_official)
        self.check = QPushButton(tr("Comprobar idioma"))
        self.check.clicked.connect(self._check_language)
        self.lang_state = QLabel()
        self.lang_state.setWordWrap(True)

        key_buttons = QHBoxLayout()
        key_buttons.addWidget(save)
        key_buttons.addWidget(delete)
        key_buttons.addStretch()
        lang_row = QHBoxLayout()
        lang_row.addWidget(self.language, 1)
        lang_row.addWidget(self.check)
        form = QFormLayout()
        form.addRow(tr("Proveedor de IA:"), self.provider)
        form.addRow("API key:", self.key)
        form.addRow("", self.key_state)
        form.addRow("", key_buttons)
        form.addRow(tr("Traducir al:"), lang_row)
        form.addRow("", self.official_label)
        form.addRow("", self.lang_state)
        api_box = QGroupBox(tr("Traducción"))
        api_box.setLayout(form)

        translate = QPushButton(tr("Traducir voces →"))
        translate.setObjectName("primary")
        extracted = (game.path / valve.FOLDER / valve.MAP_FILE).is_file()  # ya se sacaron audios de los paquetes
        can_translate = bool(plugin) or result.status == "green" or extracted
        translate.setEnabled(can_translate)
        translate.setToolTip(
            tr("Abre el proyecto de traducción: progreso, costo y revisión de líneas")
            if can_translate
            else tr("Este juego necesita un plugin para poder leer sus voces")
        )
        translate.clicked.connect(
            lambda: on_translate(self.provider.currentText(), self.language.currentData(), self.language.currentText())
        )

        right = QVBoxLayout()
        open_dir = QPushButton(tr("Abrir carpeta del juego"))
        open_dir.clicked.connect(lambda: _open_folder(game.path))

        # --- compatibilidad (plugin del juego) ---
        if plugin:
            compat_text = tr("✓ Compatible con el plugin {name} v{version} (de {author})", name=plugin.name, version=plugin.version, author=plugin.author)
        else:
            compat_text = tr("Este juego no tiene plugin instalado.")
        compat_label = QLabel(compat_text + "\n\n" + tr("ID del juego para plugins: {id}", id=game.appid))
        compat_label.setWordWrap(True)
        compat_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        add_plugin = QPushButton(tr("Agregar plugin para este juego"))
        add_menu = QMenu(add_plugin)
        add_menu.addAction(tr("Desde un .zip…"), lambda: on_add_plugin(True))
        add_menu.addAction(tr("Desde una carpeta…"), lambda: on_add_plugin(False))
        add_plugin.setMenu(add_menu)
        compat_lay = QVBoxLayout()
        compat_lay.addWidget(compat_label)
        compat_lay.addWidget(add_plugin, alignment=Qt.AlignLeft)
        compat_box = QGroupBox(tr("Compatibilidad"))
        compat_box.setLayout(compat_lay)

        right.addWidget(title)
        right.addLayout(version_row)
        right.addWidget(info)
        right.addWidget(open_dir, alignment=Qt.AlignLeft)
        right.addWidget(self._dialogue_box(result, plugin, on_add_plugin, self._extract))
        right.addWidget(compat_box)
        right.addWidget(api_box)
        right.addWidget(translate, alignment=Qt.AlignLeft)
        right.addStretch()

        body = QHBoxLayout()
        body.addWidget(cover, alignment=Qt.AlignTop)
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
        root.addWidget(back, alignment=Qt.AlignLeft)
        root.addWidget(scroll, 1)
        # Arranca con lo último elegido en este juego o, si no, con las preferencias del perfil
        if defaults.get("provider") in self.providers:
            self.provider.setCurrentText(defaults["provider"])
        self.language.setCurrentIndex(max(0, self.language.findData(defaults.get("language", "es-419"))))
        self._on_provider_changed()
        # Desde acá, cada cambio queda recordado para este juego
        remember = lambda: on_prefs_changed(self.provider.currentText(), self.language.currentData())  # noqa: E731
        self.provider.currentTextChanged.connect(remember)
        self.language.currentIndexChanged.connect(remember)
        if game.store == "Steam":
            self._ask_official(game.appid)

    @staticmethod
    def _dialogue_box(result: ScanResult, plugin, on_add_plugin, on_extract=None) -> QGroupBox:
        """Dónde están los audios del juego, en qué formato, y si Revoz los lee, necesita un plugin o están cifrados."""
        box = QGroupBox(tr("Diálogos"))
        lay = QVBoxLayout(box)
        found = scan.dialogue_formats(result)
        if not found:
            lay.addWidget(QLabel(tr("No se encontraron archivos de audio.")))
            return box
        needs_plugin = can_extract = False
        for fmt, info in found:
            state = scan.support(fmt, info["kind"])
            if plugin and state == scan.PLUGIN:
                text, color = tr("lo lee el plugin {name}", name=plugin.name), "#3fb950"
            elif state == scan.READ:
                text, color = tr("se lee directo"), "#3fb950"
            elif state == scan.EXTRACT:
                text, color = tr("se puede extraer"), "#3fb950"
                can_extract = True
            elif state == scan.PLUGIN:
                text, color = tr("necesita un plugin"), "#d29922"
                needs_plugin = True
            else:
                text, color = tr("cifrado: no se puede"), "#f85149"
            folder = info["folder"] if info["folder"] not in ("", ".") else tr("repartidos en toda la carpeta del juego")
            row = QLabel(
                f"<b>{html.escape(fmt)}</b> ({html.escape(', '.join(info['exts']))}) - "
                + f"<span style='color:{color}'>{html.escape(text)}</span><br>"
                + html.escape(tr("Carpeta: {path}", path=folder) + f"  ·  {info['bytes'] / 1048576:,.0f} MB")
            )  # fmt: skip
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(row)
        if can_extract and on_extract:
            extract = QPushButton(tr("Extraer…"))
            extract.setObjectName("primary")
            extract.setToolTip(tr("Saca los audios del paquete a la carpeta Revoz del juego para poder traducirlos."))
            extract.clicked.connect(on_extract)
            lay.addWidget(extract, alignment=Qt.AlignLeft)
        if needs_plugin:
            add = QPushButton(tr("Agregar plugin"))
            menu = QMenu(add)
            menu.addAction(tr("Desde un .zip…"), lambda: on_add_plugin(True))
            menu.addAction(tr("Desde una carpeta…"), lambda: on_add_plugin(False))
            add.setMenu(menu)
            lay.addWidget(add, alignment=Qt.AlignLeft)
        return box

    def _extract(self) -> None:
        """Saca los audios de los paquetes del juego a <juego>/Revoz, con aviso de espacio y progreso."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            items = valve.find(self.game.path)
        finally:
            QApplication.restoreOverrideCursor()
        if not items:
            QMessageBox.information(self, "Revoz", tr("No se encontraron audios dentro de los paquetes."))
            return
        voices = [i for i in items if valve.is_voice(i.inner)]
        mb = lambda its: sum(i.size for i in its) / 1048576  # noqa: E731
        target = self.game.path / valve.FOLDER
        try:
            free = shutil.disk_usage(self.game.path).free / 1048576
        except OSError:
            free = 0
        box = QMessageBox(QMessageBox.Question, "Revoz", tr("Extraer los audios a:") + f"\n{target}\n\n" + tr("Espacio libre en ese disco: {mb} MB", mb=f"{free:,.0f}"), parent=self)
        only = box.addButton(tr("Solo voces ({n} audios, {mb} MB)", n=len(voices), mb=f"{mb(voices):,.0f}"), QMessageBox.AcceptRole) if voices else None
        everything = box.addButton(tr("Todo el audio ({n}, {mb} MB)", n=len(items), mb=f"{mb(items):,.0f}"), QMessageBox.AcceptRole)
        box.addButton(tr("Cancelar"), QMessageBox.RejectRole)
        box.exec()
        chosen = voices if box.clickedButton() is only else items if box.clickedButton() is everything else None
        if not chosen:
            return

        dialog = QProgressDialog(tr("Extrayendo audios…"), tr("Cancelar"), 0, len(chosen), self)
        dialog.setWindowTitle("Revoz")
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setValue(0)
        bus = self.bus
        bus.extract_progress.connect(lambda done, total: dialog.setValue(done))

        class Stop(Exception):
            pass

        def progress(done: int, total: int) -> None:
            if dialog.wasCanceled():
                raise Stop
            bus.extract_progress.emit(done, total)

        game_path = self.game.path

        def work() -> None:
            try:
                result = valve.extract(game_path, chosen, progress=progress)
            except Stop:
                result = (len(valve.load_map(game_path / valve.FOLDER)), [])
            except Exception as e:
                result = e
            try:
                bus.extracted.emit(result)
            except RuntimeError:
                pass

        def done(result) -> None:
            dialog.close()
            if isinstance(result, Exception):
                QMessageBox.warning(self, "Revoz", tr("No se pudo extraer:") + f"\n\n{type(result).__name__}: {result}")
                return
            count, failed = result
            text = tr("Listo: {n} audios extraídos en:", n=count) + f"\n{target}"
            if failed:
                text += "\n\n" + tr("No se pudieron extraer: {n}", n=len(failed)) + "\n" + "\n".join(sorted({f"• {tr(r)}" for _, r in failed})[:4])
            text += "\n\n" + tr("Si querés, ordená los audios en subcarpetas por personaje: Revoz usa el nombre de la carpeta.")
            msg = QMessageBox(QMessageBox.Information, "Revoz", text, parent=self)
            open_btn = msg.addButton(tr("Abrir carpeta"), QMessageBox.ActionRole)
            msg.addButton(QMessageBox.Ok)
            msg.exec()
            if msg.clickedButton() is open_btn:
                _open_folder(target)
            self.on_extracted()

        bus.extracted.connect(done)
        threading.Thread(target=work, daemon=True).start()

    def _ask_official(self, appid: str) -> None:
        """Pregunta a la tienda de Steam qué idiomas oficiales tiene el juego (en segundo plano)."""
        bus = self.bus

        def work() -> None:
            try:
                langs = official.steam_languages(appid)
            except Exception:  # sin internet o respuesta rara: simplemente no se muestra nada
                langs = None
            try:
                bus.official.emit(langs)
            except RuntimeError:  # la página se cerró mientras consultaba
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_official(self, langs) -> None:
        self._official = langs
        self._show_official()

    def _show_official(self) -> None:
        if not self._official:
            self.official_label.clear()
            return
        lang = self.language.currentText()
        state, other = official.check(self._official, self.language.currentData())
        if state == official.AUDIO:
            color, text = "#3fb950", tr("✓ Este juego ya tiene voces oficiales en {lang}. Activalas en Steam: Propiedades → Idioma. No hace falta traducirlo.", lang=lang)
        elif state == official.OTHER_REGION:
            # el nombre como figura en Revoz ("Español (España)") si lo tenemos; si no, el de Steam
            other = next((tr(n) for c, n in providers.TARGET_LANGUAGES if official.STEAM_NAMES.get(c, [""])[0] == other), other)
            color, text = "#d29922", tr("Tiene voces oficiales en {other} (Steam: Propiedades → Idioma), pero no en {lang}.", other=other, lang=lang)
        elif state == official.TEXT:
            color, text = "#d29922", tr("Tiene textos oficiales en {lang}, pero no voces.", lang=lang)
        else:
            color, text = "", tr("Steam no lista {lang} para este juego: no hay traducción oficial.", lang=lang)
        self.official_label.setStyleSheet(f"color: {color};" if color else "")
        self.official_label.setText(text)

    def _on_provider_changed(self) -> None:
        self.lang_state.clear()
        provider = self.providers.get(self.provider.currentText())
        needs_key = provider is None or provider.needs_key
        for w in (self.key, self.save_btn, self.delete_btn):
            w.setEnabled(needs_key)
        if not needs_key:
            self.key_state.setText(tr("No necesita API key: corre en tu PC."))
            return
        try:
            ok = keys.has(self.provider.currentText())
        except Exception:  # backend de keyring no disponible
            ok = False
        self.key_state.setText(tr("✓ Clave guardada") if ok else tr("Sin clave guardada"))

    def _save_key(self) -> None:
        try:
            keys.save(self.provider.currentText(), self.key.text())
        except ValueError as e:
            QMessageBox.warning(self, "Revoz", tr(str(e)))
            return
        except Exception:
            QMessageBox.critical(self, "Revoz", tr("No se pudo guardar en el almacén seguro del sistema."))
            return
        finally:
            self.key.clear()  # no dejar la clave en el widget
        self._on_provider_changed()

    def _delete_key(self) -> None:
        try:
            keys.delete(self.provider.currentText())
        except Exception:
            QMessageBox.critical(self, "Revoz", tr("No se pudo borrar del almacén seguro del sistema."))
        self._on_provider_changed()

    def _check_language(self) -> None:
        name = self.provider.currentText()
        try:
            key = keys.get(name) if self.providers[name].needs_key else ""
        except Exception:
            key = None
        if key is None or (not key and self.providers[name].needs_key):
            self.lang_state.setText(tr("Primero guardá la API key de este proveedor."))
            return
        provider, code, lang = self.providers[name], self.language.currentData(), self.language.currentText()
        self.check.setEnabled(False)
        self.lang_state.setText(tr("Consultando a {name}…", name=name))

        def work() -> None:
            try:
                langs = provider.languages(key)
                if langs is None:
                    ok, msg = True, tr("✓ Clave válida. {name} es un modelo de lenguaje: traduce al {lang} sin problema.", name=name, lang=lang)
                elif providers.supports(langs, code):
                    ok, msg = True, tr("✓ {name} soporta {lang}.", name=name, lang=lang)
                else:
                    ok, msg = False, tr("✗ {name} no soporta {lang}.", name=name, lang=lang)
            except ProviderError as e:
                ok, msg = False, "✗ " + tr(str(e))
            except Exception:  # respuesta inesperada o plugin con errores
                ok, msg = False, tr("✗ Respuesta inesperada del servicio")
            try:
                self.bus.checked.emit(ok, msg)
            except RuntimeError:  # la página se cerró mientras consultaba
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_checked(self, ok: bool, msg: str) -> None:
        self.check.setEnabled(True)
        self.lang_state.setStyleSheet(f"color: {'#3fb950' if ok else '#f85149'};")
        self.lang_state.setText(msg)


class MainWindow(QMainWindow):
    def __init__(self, section: int = 0) -> None:
        super().__init__()
        self._closing = False
        self.setWindowTitle(f"Revoz {VERSION}")
        self.resize(1100, 720)
        self._restore_geometry()  # misma posición y tamaño que la última vez

        self.cache = scan.load_cache(CACHE_FILE)
        self.preferred: dict[str, str] = _load_json(PREFS_FILE)
        self.prices: dict[str, list[float]] = _load_json(PRICES_FILE)
        self.profile: dict = _load_json(PROFILE_FILE)
        self.game_prefs: dict[str, dict] = _load_json(GAME_PREFS_FILE)
        # Idioma de la interfaz: el que eligió la persona, o el de Windows la primera vez.
        # Se aplica dos veces: antes de los plugins (para sus avisos) y después (por si es un idioma de un plugin).
        ui_language = self.profile.get("ui_language") or i18n.system_language()
        i18n.set_language(ui_language)
        self.open_when_ready: str | None = None
        self.status: dict[str, str] = {}
        self.bus = _Bus()
        self.bus.started.connect(self._on_started)
        self.bus.scanned.connect(self._on_scanned)

        self._load_plugins()  # también registra temas e idiomas de la comunidad
        i18n.set_language(ui_language)
        self._theme = self.profile.get("theme", themes.DEFAULT)
        themes.apply(self._theme)
        # Si el tema es "Sistema", seguir a Windows cuando cambia entre claro y oscuro
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(COVER)
        self.grid.setGridSize(QSize(COVER.width() + 24, COVER.height() + 50))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setWordWrap(True)
        self.grid.setSortingEnabled(True)
        self.grid.itemClicked.connect(self._on_click)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._game_menu)

        # Un solo hilo de análisis, en orden: no saturar el disco escaneando todo en paralelo.
        self.scan_queue: queue.Queue[Game] = queue.Queue()
        self.pending = 0
        threading.Thread(target=self._scan_worker, daemon=True).start()

        self.games: dict[str, Game] = {}  # appid -> versión (cada tienda es una versión)
        self.groups: dict[str, list[Game]] = {}  # nombre normalizado -> versiones del mismo juego
        self.group_of: dict[str, str] = {}  # appid -> nombre normalizado
        self.items: dict[str, QListWidgetItem] = {}  # nombre normalizado -> carátula
        for g in stores.all_games(MANUAL_FILE, self._library_folders()):
            self._add_item(g)

        self.header = QLabel()
        self.header.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 8px;")
        add_btn = QPushButton(tr("+ Agregar juego"))
        add_menu = QMenu(add_btn)
        add_menu.addAction(tr("Elegir el .exe o un acceso directo del juego…"), self._add_game_file)
        add_menu.addAction(tr("Elegir la carpeta del juego…"), self._add_game_folder)
        add_menu.addAction(tr("Agregar una carpeta con varios juegos…"), self._add_library_folder)
        add_btn.setMenu(add_menu)
        top = QHBoxLayout()
        top.addWidget(self.header)
        top.addStretch()
        top.addWidget(add_btn)

        # --- buscador y filtros ---
        self.search = QLineEdit(placeholderText=tr("Buscar juego…"))
        self.search.setClearButtonEnabled(True)
        self.filter_status = QComboBox()
        for text, key in LIBRARY_FILTERS:
            self.filter_status.addItem(tr(text), key)
        self.filter_store = QComboBox()
        self.filter_store.addItem(tr("Todas las tiendas"), "")
        for store in stores.STORE_ORDER:
            self.filter_store.addItem(store_name(store), store)
        self.search.textChanged.connect(self._apply_library_filter)
        self.filter_status.currentIndexChanged.connect(self._apply_library_filter)
        self.filter_store.currentIndexChanged.connect(self._apply_library_filter)
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.filter_status)
        filters.addWidget(self.filter_store)

        library = QWidget()
        lay = QVBoxLayout(library)
        lay.addLayout(top)
        lay.addLayout(filters)
        lay.addWidget(self.grid)

        # La biblioteca tiene su propia pila: grilla -> página del juego -> traducción.
        self.stack = QStackedWidget()
        self.stack.addWidget(library)

        # --- menú de arriba: siempre visible, en cualquier pantalla ---
        self.pages = QStackedWidget()
        self.pages.addWidget(self.stack)
        self.pages.addWidget(self._build_plugins_tab())
        self.profile_holder = QWidget()  # la página de perfil se arma al abrirla (estadísticas al día)
        QVBoxLayout(self.profile_holder).setContentsMargins(0, 0, 0, 0)
        self.pages.addWidget(self.profile_holder)

        self.nav = QButtonGroup(self)
        nav_bar = QHBoxLayout()
        nav_bar.setContentsMargins(8, 4, 8, 0)
        for i, text in enumerate((tr("Biblioteca"), tr("Plugins"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setObjectName("nav")
            self.nav.addButton(b, i)
            nav_bar.addWidget(b)
        nav_bar.addStretch()
        self.profile_btn = QToolButton()
        self.profile_btn.setObjectName("nav")
        self.profile_btn.setCheckable(True)
        self.profile_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.profile_btn.setIconSize(QSize(26, 26))
        self.profile_btn.setCursor(Qt.PointingHandCursor)
        self.nav.addButton(self.profile_btn, 2)
        nav_bar.addWidget(self.profile_btn)
        self.nav.idClicked.connect(self._go_section)
        self.nav.button(0).setChecked(True)
        self._refresh_profile_btn()

        central = QWidget()
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(0, 0, 0, 0)
        central_lay.setSpacing(0)
        central_lay.addLayout(nav_bar)
        line = QWidget()
        line.setFixedHeight(1)
        line.setObjectName("sep")
        central_lay.addWidget(line)
        central_lay.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self._apply_library_filter()
        self._show_progress()
        if section:
            self._go_section(section)

    def _go_section(self, index: int) -> None:
        if index == 0 and self.pages.currentIndex() == 0:
            while self.stack.count() > 1:  # tocar Biblioteca estando en un juego vuelve a la grilla
                self._back()
        if index == 2:
            self._build_profile()
        self.nav.button(index).setChecked(True)
        self.pages.setCurrentIndex(index)

    # --- biblioteca ---

    def _add_item(self, g: Game) -> None:
        key = stores.group_key(g.name)
        self.games[g.appid] = g
        self.group_of[g.appid] = key
        if key in self.groups:
            self.groups[key] = stores.group_games(self.groups[key] + [g])[0]
        else:
            self.groups[key] = [g]
            item = QListWidgetItem(g.name)
            item.setData(Qt.UserRole, key)
            self.grid.addItem(item)
            self.items[key] = item
        cached = self.cache.get(g.appid)
        if cached and cached["buildid"] == g.buildid and "formats" in cached["result"]:  # análisis viejo sin formatos: rehacer
            self._set_status(g.appid, cached["result"]["status"])
        else:  # nunca analizado o la tienda lo actualizó
            self._queue_scan(g)

    def _shown(self, appid: str) -> str:
        """Estado a mostrar de una versión: un plugin la vuelve compatible."""
        st = self.status[appid]
        return "plugin" if appid in self.game_plugins and st not in ("pending", "scanning") else st

    def _refresh_item(self, key: str) -> None:
        versions = self.groups[key]
        best = self._best_status(key)  # el ícono muestra la mejor versión
        if best is None:
            return
        item = self.items[key]
        item.setData(Qt.UserRole + 1, best)  # para los filtros
        item.setIcon(QIcon(cover_pixmap(versions, best)))
        lines = []
        for v in versions:
            if v.appid in self.status:
                text = tr(STATUS_TEXT[self._shown(v.appid)])
                if v.appid in self.game_plugins:
                    text += f": {self.game_plugins[v.appid][0].name}"
                lines.append(f"{store_name(v.store)}: {text}" if len(versions) > 1 else text)
        item.setToolTip("\n".join(lines))
        self._filter_item(key)

    def _filter_item(self, key: str) -> None:
        if not hasattr(self, "search"):  # todavía armando la ventana
            return
        item, versions = self.items[key], self.groups[key]
        text = self.search.text().strip().lower()
        status = item.data(Qt.UserRole + 1)
        wanted_status = self.filter_status.currentData()
        store = self.filter_store.currentData()
        visible = (
            (not text or text in versions[0].name.lower())
            and (not wanted_status or status in LIBRARY_FILTER_STATUSES[wanted_status])
            and (not store or any(v.store == store for v in versions))
        )
        item.setHidden(not visible)

    def _apply_library_filter(self) -> None:
        for key in self.items:
            self._filter_item(key)
        shown = sum(not item.isHidden() for item in self.items.values())
        filtering = self.search.text().strip() or self.filter_status.currentData() or self.filter_store.currentData()
        self._update_header(shown if filtering else None)

    def _update_header(self, shown: int | None = None) -> None:
        if not hasattr(self, "header"):
            return
        by_store: dict[str, int] = {}
        for g in self.games.values():
            by_store[g.store] = by_store.get(g.store, 0) + 1
        detail = ", ".join(tr("{n} de {store}", n=n, store=store_name(s)) for s, n in sorted(by_store.items()))
        if shown is not None:
            count = tr("{shown} de {total} juegos", shown=shown, total=len(self.groups))
        else:
            count = tr("{total} juegos", total=len(self.groups))
        self.header.setText(tr("Biblioteca") + f" - {count}" + (f" ({detail})" if detail else ""))

    def _add_game(self, path: str) -> None:
        if not path:
            return
        info = QFileInfo(path)
        if info.isShortcut():  # .lnk del escritorio: usar el juego al que apunta
            path = info.symLinkTarget()
        try:
            game = stores.add_manual(MANUAL_FILE, Path(path))
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Revoz", tr("No se pudo agregar el juego:") + "\n\n" + tr(str(e)))
            return
        if any(str(g.path).lower() == str(game.path).lower() for g in self.games.values()):
            stores.remove_manual(MANUAL_FILE, game.appid)
            QMessageBox.information(self, "Revoz", tr("Ese juego ya está en la biblioteca."))
            return
        self._add_item(game)
        self._update_header()
        self.grid.scrollToItem(self.items[self.group_of[game.appid]])

    def _add_game_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Elegí el juego"), str(Path.home() / "Desktop"), tr("Juego") + " (*.exe *.lnk);;" + tr("Todos los archivos") + " (*)"
        )
        self._add_game(path)

    def _add_game_folder(self) -> None:
        self._add_game(QFileDialog.getExistingDirectory(self, tr("Elegí la carpeta del juego")))

    def _game_menu(self, pos) -> None:
        item = self.grid.itemAt(pos)
        if not item:
            return
        versions = self.groups[item.data(Qt.UserRole)]
        multi = len(versions) > 1
        menu = QMenu(self)
        folders = menu.addMenu(tr("Abrir carpeta del juego")) if multi else menu
        for v in versions:
            folders.addAction(f"{store_name(v.store)}: {v.path}" if multi else tr("Abrir carpeta del juego"), lambda v=v: _open_folder(v.path))
        idle = [v for v in versions if self.status[v.appid] not in ("pending", "scanning")]
        rescan = menu.addAction(tr("Volver a analizar"), lambda: [self._queue_scan(v) for v in idle])
        rescan.setEnabled(bool(idle))
        for v in versions:
            if v.store == "Manual":
                menu.addAction(tr("Quitar de la biblioteca") + (" (" + tr("versión manual") + ")" if multi else ""), lambda v=v: self._remove_game(v.appid))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _remove_game(self, appid: str) -> None:
        if self.status[appid] in ("pending", "scanning"):
            self.statusBar().showMessage(tr("Esperá a que termine de analizarse"))
            return
        stores.remove_manual(MANUAL_FILE, appid)
        self._drop_version(appid)

    def _drop_version(self, appid: str) -> None:
        """Saca una versión de la biblioteca (y la carátula, si era la última)."""
        key = self.group_of.pop(appid)
        self.groups[key] = [v for v in self.groups[key] if v.appid != appid]
        del self.games[appid], self.status[appid]
        if self.groups[key]:
            self._refresh_item(key)
        else:
            del self.groups[key]
            self.grid.takeItem(self.grid.row(self.items.pop(key)))
        self._update_header()

    # --- plugins ---

    def _load_plugins(self) -> None:
        self.game_plugins: dict[str, tuple[PluginInfo, GamePlugin]] = {}
        self.providers: list[Provider] = list(providers.BUILTIN)
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        found, errors = plugins.discover(PLUGIN_DIR)
        trusted = plugins.load_trusted(TRUST_FILE)
        themes.reset_custom()
        i18n.reset_custom()
        # Plugins que la persona desactivó: siguen en la carpeta, pero no se cargan ni se preguntan.
        self.disabled: set[str] = set(_load_json(DISABLED_FILE).get("disabled", []))
        # filas de la pestaña Plugins: (carpeta, nombre, versión, autor, tipo, estado)
        self.plugin_rows = []
        for e in list(errors):
            folder = e.split(":")[0]
            if folder in self.disabled:  # uno roto pero desactivado no molesta con avisos
                errors.remove(e)
                self.plugin_rows.append((folder, "", "", "", "", N_("Desactivado")))
            else:
                self.plugin_rows.append((folder, "", "", "", "", "Error:" + e.split(":", 1)[1]))
        for info in found:
            kind = {
                plugins.GAME: tr("Juego") + f" ({', '.join(info.appids)})",
                plugins.PROVIDER: tr("Servicio de IA"),
                plugins.THEME: tr("Tema"),
                plugins.LANGUAGE: tr("Idioma de la interfaz"),
            }[info.kind]
            row = (info.id, info.name, info.version, info.author, kind)
            if info.id in self.disabled:
                self.plugin_rows.append((*row, N_("Desactivado")))
                continue
            if info.kind in (plugins.THEME, plugins.LANGUAGE):
                # Solo datos: no ejecutan código, así que no se pide aprobación; se validan al registrarlos.
                try:
                    meta = plugins.read_meta(info)
                    if info.kind == plugins.THEME:
                        themes.register(info.name, meta.get("colors"))
                        status = N_("Activo")
                    else:
                        skipped = i18n.register_language(meta.get("code"), meta.get("language_name"), meta.get("texts"))
                        status = tr("Activo ({n} textos inválidos ignorados)", n=skipped) if skipped else N_("Activo")
                except (plugins.PluginError, ValueError) as e:
                    errors.append(f"{info.id}: {e}")
                    status = f"Error: {e}"
                self.plugin_rows.append((*row, status))
                continue
            if not plugins.is_trusted(trusted, info):
                answer = QMessageBox.question(
                    self,
                    tr("Revoz: plugin nuevo"),
                    tr("Se encontró un plugin nuevo o modificado:") + "\n\n"
                    + f"{info.name} v{info.version}\n" + tr("Autor: {author}", author=info.author) + "\n"
                    + tr("Carpeta: {path}", path=info.path) + "\n\n"
                    + tr("Los plugins ejecutan código en tu PC. Activalo solo si confiás en quien lo hizo.") + "\n\n"
                    + tr("¿Activar este plugin?"),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    self.plugin_rows.append((*row, N_("No aprobado")))
                    continue
                plugins.trust(TRUST_FILE, trusted, info)
            try:
                instance = plugins.load(info, trusted)
            except plugins.PluginError as e:
                errors.append(f"{info.id}: {e}")
                self.plugin_rows.append((*row, f"Error: {e}"))
                continue
            self.plugin_rows.append((*row, N_("Activo")))
            if info.kind == plugins.GAME:
                for appid in info.appids:
                    self.game_plugins[appid] = (info, instance)
            else:
                instance.name = instance.name or info.name
                self.providers.append(instance)
        self.plugin_rows.sort()
        if errors:
            QMessageBox.warning(self, "Revoz", tr("Algunos plugins no se cargaron:") + "\n\n" + "\n".join(errors))

    def _build_plugins_tab(self) -> QWidget:
        hint = QLabel(
            tr("Los plugins se guardan en: {path}", path=PLUGIN_DIR) + "\n"
            + tr("Agregá un plugin desde un .zip o una carpeta. Antes de activarse, Revoz te pide que lo apruebes.")
            + "\n"
            + tr("Los temas y los idiomas no ejecutan código, así que se activan solos.")
            + "\n"
            + tr("Crear plantilla arma un tema o un idioma nuevo, listo para editar con el Bloc de notas y compartir.")
        )
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.plugin_table = QTableWidget(0, 7)
        self.plugin_table.setHorizontalHeaderLabels(
            [tr("Carpeta"), tr("Nombre"), tr("Versión"), tr("Autor"), tr("Tipo"), tr("Estado"), ""]
        )
        header = self.plugin_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)  # el estado ocupa el espacio que sobra
        self.plugin_table.verticalHeader().hide()
        self.plugin_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.plugin_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.plugin_table.setSelectionMode(QAbstractItemView.SingleSelection)

        buttons = QHBoxLayout()
        for text, slot in (
            (tr("Agregar desde .zip"), self._add_plugin_zip),
            (tr("Agregar carpeta"), self._add_plugin_folder),
            (tr("Quitar"), self._remove_plugin),
            (tr("Recargar"), self._reload_plugins),
            (tr("Abrir carpeta"), self._open_plugin_dir),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch()
        create = QPushButton(tr("Crear plantilla"))
        create.setToolTip(tr("Arma un plugin listo para editar, para compartir con la comunidad"))
        create_menu = QMenu(create)
        create_menu.addAction(tr("Tema (con los colores del tema actual)…"), lambda: self._create_template(plugins.THEME))
        create_menu.addAction(tr("Idioma de la interfaz (con todos los textos)…"), lambda: self._create_template(plugins.LANGUAGE))
        create.setMenu(create_menu)
        buttons.addWidget(create)

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.addWidget(hint)
        lay.addLayout(buttons)
        lay.addWidget(self.plugin_table)
        self._fill_plugin_table()
        return tab

    def _fill_plugin_table(self) -> None:
        self.plugin_table.setRowCount(len(self.plugin_rows))
        for r, row in enumerate(self.plugin_rows):
            for c, text in enumerate(row):
                cell = QTableWidgetItem(tr(text) if c == 5 else text)
                if c == 5:
                    active = text == "Activo" or text.startswith(tr("Activo"))
                    color = "#8b949e" if text == "Desactivado" else "#3fb950" if active else "#f85149"
                    cell.setForeground(QColor(color))
                self.plugin_table.setItem(r, c, cell)
            folder = row[0]
            toggle = QPushButton(tr("Activar") if folder in self.disabled else tr("Desactivar"))
            toggle.setToolTip(tr("Activar o desactivar este plugin sin borrarlo"))
            toggle.clicked.connect(lambda _=False, f=folder: self._toggle_plugin(f))
            self.plugin_table.setCellWidget(r, 6, toggle)
        self.plugin_table.resizeRowsToContents()

    def _toggle_plugin(self, folder: str) -> None:
        if folder in self.disabled:
            self.disabled.discard(folder)
        else:
            self.disabled.add(folder)
        _save_json(DISABLED_FILE, {"disabled": sorted(self.disabled)})
        self._reload_plugins()

    def _install_plugin(self, source: str) -> PluginInfo | None:
        if not source:
            return None
        try:
            info = plugins.install(Path(source), PLUGIN_DIR)
        except (plugins.PluginError, OSError) as e:
            QMessageBox.warning(self, "Revoz", tr("No se pudo agregar el plugin:") + f"\n\n{e}")
            return None
        self.statusBar().showMessage(tr("Plugin '{name}' agregado", name=info.name))
        self._reload_plugins()
        return info

    def _add_plugin_for_game(self, appid: str, from_zip: bool) -> None:
        if from_zip:
            source = QFileDialog.getOpenFileName(self, tr("Elegí el plugin"), "", "Plugin (*.zip)")[0]
        else:
            source = QFileDialog.getExistingDirectory(self, tr("Elegí la carpeta del plugin"))
        info = self._install_plugin(source)
        if info and info.kind == plugins.GAME and appid not in info.appids:
            QMessageBox.warning(
                self,
                "Revoz",
                tr("El plugin '{name}' se instaló, pero es para otros juegos ({ids}), no para este ({id}).",
                   name=info.name, ids=", ".join(info.appids), id=appid),
            )
        self._back()
        self._open_game(appid)

    def _add_plugin_zip(self) -> None:
        self._install_plugin(QFileDialog.getOpenFileName(self, tr("Elegí el plugin"), "", "Plugin (*.zip)")[0])

    def _add_plugin_folder(self) -> None:
        self._install_plugin(QFileDialog.getExistingDirectory(self, tr("Elegí la carpeta del plugin")))

    def _remove_plugin(self) -> None:
        rows = self.plugin_table.selectionModel().selectedRows()
        if not rows:
            self.statusBar().showMessage(tr("Elegí un plugin de la lista para quitarlo"))
            return
        folder = self.plugin_rows[rows[0].row()][0]
        path = PLUGIN_DIR / folder
        answer = QMessageBox.question(
            self, "Revoz", tr("¿Quitar el plugin '{name}'?", name=folder) + "\n\n" + tr("La carpeta se manda a la papelera de reciclaje.")
        )
        if answer != QMessageBox.Yes:
            return
        if not QFile.moveToTrash(str(path)):
            QMessageBox.warning(self, "Revoz", tr("No se pudo mandar a la papelera:") + f"\n{path}")
            return
        self._reload_plugins()

    def _reload_plugins(self) -> None:
        self._load_plugins()
        self._fill_plugin_table()
        themes.apply(self._theme)  # por si se editó el tema que está en uso
        for key in self.items:
            self._refresh_item(key)

    def _create_template(self, kind: str) -> None:
        """Crea un plugin de tema o de idioma dentro de la carpeta de plugins, listo para editar."""
        base = "mi_tema" if kind == plugins.THEME else "mi_idioma"
        folder = PLUGIN_DIR / base
        n = 2
        while folder.exists():
            folder = PLUGIN_DIR / f"{base}_{n}"
            n += 1
        try:
            if kind == plugins.THEME:
                file = themes.write_template(folder, self._theme)
            else:
                file = i18n.write_template(folder)
        except OSError as e:
            QMessageBox.warning(self, "Revoz", tr("No se pudo crear la plantilla:") + f"\n\n{e}")
            return
        QMessageBox.information(
            self,
            "Revoz",
            tr("Plantilla creada en:") + f"\n{file}\n\n"
            + tr("Editala con el Bloc de notas y tocá Recargar para verla en Revoz."),
        )
        _open_folder(folder)
        self._reload_plugins()

    def _open_plugin_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(PLUGIN_DIR)))

    # --- análisis ---

    def _queue_scan(self, game: Game) -> None:
        self._set_status(game.appid, "pending")
        self.pending += 1
        self.scan_queue.put(game)
        self._show_progress()

    def _scan_worker(self) -> None:
        while True:
            game = self.scan_queue.get()
            if game is None or self._closing:  # la ventana se cerró o se rearmó
                return
            try:
                self.bus.started.emit(game.appid)
                try:
                    result = scan.scan_dir(game.path)
                except Exception:
                    result = ScanResult("red", {}, 0)
                if self._closing:
                    return
                self.bus.scanned.emit(game.appid, result)
            except RuntimeError:  # la ventana ya no existe
                return

    def _show_progress(self) -> None:
        if not hasattr(self, "stack"):  # todavía armando la ventana
            return
        if self.pending:
            self.statusBar().showMessage(tr("Analizando audio de la biblioteca… faltan {n} juegos", n=self.pending))
        else:
            self.statusBar().showMessage(tr("Biblioteca analizada. Hacé clic en un juego para ver el detalle"))

    def _set_status(self, appid: str, status: str) -> None:
        self.status[appid] = status
        self._refresh_item(self.group_of[appid])

    def _on_click(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        versions = self.groups[key]
        ready = [v for v in versions if self._shown(v.appid) not in ("pending", "scanning", "red")]
        preferred = self.preferred.get(key)
        if preferred in self.cache and preferred in self.status and self.status[preferred] not in ("pending", "scanning"):
            self._open_game(preferred)  # la versión que el usuario eligió la última vez
        elif ready:  # si no eligió ninguna, la mejor
            self._open_game(min(ready, key=lambda v: STATUS_RANK.index(self._shown(v.appid))).appid)
        else:
            st = self._shown(versions[0].appid)
            self.statusBar().showMessage(f"{versions[0].name}: {tr(STATUS_TEXT[st])}")

    def _on_started(self, appid: str) -> None:
        self._set_status(appid, "scanning")

    def _on_scanned(self, appid: str, result: ScanResult) -> None:
        self.pending -= 1
        self._show_progress()
        game = self.games[appid]
        self.cache[appid] = {"buildid": game.buildid, "result": scan.to_dict(result)}
        try:
            scan.save_cache(CACHE_FILE, self.cache)
        except OSError:
            pass
        self._set_status(appid, result.status)
        if appid == self.open_when_ready:  # el usuario la eligió mientras se analizaba
            self.open_when_ready = None
            if isinstance(self.stack.currentWidget(), GamePage):
                self._back()
                self._open_game(appid)

    # --- navegación ---

    def _open_game(self, appid: str) -> None:
        if appid not in self.cache or self.status[appid] in ("pending", "scanning"):
            self.statusBar().showMessage(tr("Esa versión todavía se está analizando"))
            return
        result = ScanResult(**self.cache[appid]["result"])
        plugin = self.game_plugins.get(appid, (None, None))[0]
        page = GamePage(
            self.games[appid],
            result,
            plugin,
            self.providers,
            self._back,
            lambda from_zip: self._add_plugin_for_game(appid, from_zip),
            self.groups[self.group_of[appid]],
            self._switch_version,
            lambda: self._add_version(appid),
            lambda provider, code, lang: self._open_translation(appid, provider, f"{lang} ({code})", code),
            {**self.profile, **self.game_prefs.get(self.group_of[appid], {})},
            lambda provider, language: self._remember_game_prefs(appid, provider, language),
            on_extracted=lambda: self._rescan(appid),
        )
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    # --- perfil ---

    def _refresh_profile_btn(self) -> None:
        name = self.profile.get("name", "")
        self.profile_btn.setIcon(QIcon(avatar_pixmap(name, AVATAR_FILE, 52)))
        self.profile_btn.setText(name or tr("Perfil"))
        self.profile_btn.setToolTip(tr("Tu perfil y preferencias"))

    def _profile_changed(self) -> None:
        _save_json(PROFILE_FILE, self.profile)
        self._refresh_profile_btn()
        theme = self.profile.get("theme", themes.DEFAULT)
        if theme != getattr(self, "_theme", None):  # cambiar el tema solo si cambió (rearmar estilos es caro)
            self._theme = theme
            themes.apply(theme)

    def _on_system_theme_changed(self) -> None:
        if self.profile.get("theme") == themes.SYSTEM:
            themes.apply(themes.SYSTEM)

    def _best_status(self, key: str) -> str | None:
        shown = [self._shown(v.appid) for v in self.groups[key] if v.appid in self.status]
        if "scanning" in shown or "pending" in shown:
            return "scanning" if "scanning" in shown else "pending"
        return min((s for s in shown if s in STATUS_RANK), key=STATUS_RANK.index, default=None)

    def _profile_stats(self) -> dict:
        best = [self._best_status(key) for key in self.groups]
        translated = reviewed = projects = 0
        for f in PROJECTS_DIR.glob("*.json"):
            if p := pj.load(f):
                projects += 1
                done = pj.progress(p.lines)
                translated += done[pj.TRANSLATED]
                reviewed += done[pj.REVIEWED]
        return {
            tr("Juegos en la biblioteca"): len(self.groups),
            tr("Juegos fáciles de traducir"): sum(s in ("green", "plugin") for s in best),
            tr("Plugins activos"): sum(row[5] == "Activo" for row in self.plugin_rows),
            tr("Proyectos de traducción"): projects,
            tr("Líneas traducidas"): f"{translated:,}",
            tr("Líneas revisadas"): f"{reviewed:,}",
        }

    def _rescan_all(self) -> None:
        for g in self.games.values():
            if self.status.get(g.appid) not in ("pending", "scanning"):
                self._queue_scan(g)
        self._go_section(0)

    def _build_profile(self) -> None:
        lay = self.profile_holder.layout()
        while lay.count():
            old = lay.takeAt(0).widget()
            old.deleteLater()
        lay.addWidget(
            ProfilePage(
                self.profile,
                AVATAR_FILE,
                [p.name for p in self.providers],
                self._profile_stats(),
                self._profile_changed,
                self._reload_library,
                self._set_ui_language,
                [
                    (tr("Abrir carpeta de datos"), lambda: _open_folder(DATA_DIR)),
                    (tr("Volver a analizar toda la biblioteca"), self._rescan_all),
                ],
            )
        )

    def _set_ui_language(self, code: str) -> None:
        if code == i18n.current():
            return
        self.profile["ui_language"] = code
        _save_json(PROFILE_FILE, self.profile)
        # Se reinicia después de que termine el evento del desplegable que disparó el cambio.
        QTimer.singleShot(0, self._restart)

    def _restart(self) -> None:
        """Cierra Revoz y lo vuelve a abrir solo, en el mismo lugar y en la pantalla de Perfil."""
        self._save_state()  # antes de lanzar la nueva: que encuentre la posición ya guardada
        if getattr(sys, "frozen", False):
            program, args = sys.executable, []
        else:  # desde el código: pythonw, para que no aparezca una consola negra
            windowless = Path(sys.executable).with_name("pythonw.exe")
            program, args = str(windowless if windowless.exists() else sys.executable), ["-m", "revoz"]
        QProcess.startDetached(program, args + [RESTART_FLAG], str(APP_DIR))
        self._closing = True
        QApplication.quit()

    def _save_state(self) -> None:
        """Guarda lo que no se puede perder al cerrar: ediciones abiertas y la posición de la ventana."""
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            if isinstance(page, TranslationPage):
                page.stop()
                page._save()
        _save_json(WINDOW_FILE, {"geometry": bytes(self.saveGeometry().toBase64()).decode()})

    def _restore_geometry(self) -> None:
        saved = _load_json(WINDOW_FILE).get("geometry")
        if isinstance(saved, str):
            self.restoreGeometry(QByteArray.fromBase64(saved.encode()))  # Qt corrige si la pantalla cambió

    def closeEvent(self, event) -> None:
        if not self._closing:
            self._save_state()
        self._closing = True
        self.scan_queue.put(None)  # despierta al hilo de análisis para que termine
        super().closeEvent(event)

    # --- carpetas de juegos ---

    def _library_folders(self) -> list[Path]:
        return [Path(f) for f in self.profile.get("library_folders", []) if isinstance(f, str)]

    def _add_library_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Elegí la carpeta donde tenés varios juegos"))
        if not folder:
            return
        folders = self.profile.setdefault("library_folders", [])
        if folder not in folders:
            folders.append(folder)
            self._profile_changed()
        self._reload_library()

    def _reload_library(self) -> None:
        """Vuelve a leer todas las tiendas y carpetas: agrega lo nuevo y saca lo que ya no está."""
        found = {g.appid: g for g in stores.all_games(MANUAL_FILE, self._library_folders())}
        for appid in [a for a in self.games if a not in found and self.status.get(a) not in ("pending", "scanning")]:
            self._drop_version(appid)
        for appid, g in found.items():
            if appid not in self.games:
                self._add_item(g)
        self._update_header()
        self._apply_library_filter()
        if self.pages.currentIndex() == 2:
            self._build_profile()

    def _open_translation(self, appid: str, provider: str, language: str, code: str = "es-419") -> None:
        game = self.games[appid]
        file = PROJECTS_DIR / (re.sub(r"[^\w.-]", "_", appid) + ".json")
        project = pj.load(file)
        if project is None:  # primera vez: armar la lista de líneas de voz
            plugin = self.game_plugins.get(appid, (None, None))[1]
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self.statusBar().showMessage(tr("Buscando las líneas de voz de {name}…", name=game.name))
            QApplication.processEvents()
            try:
                lines = pj.build_lines(game.path, plugin)
            except Exception as e:  # un plugin con errores no tiene que cerrar la app
                QApplication.restoreOverrideCursor()
                QMessageBox.warning(self, "Revoz", tr("No se pudieron listar las voces:") + f"\n\n{type(e).__name__}: {e}")
                return
            QApplication.restoreOverrideCursor()
            if not lines:
                QMessageBox.information(self, "Revoz", tr("No se encontraron líneas de voz sueltas en este juego."))
                return
            project = pj.Project(appid, game.name, lines=lines)
        project.language, project.provider, project.target = language, provider, code
        try:
            pj.save(file, project)
        except OSError as e:
            QMessageBox.critical(self, "Revoz", tr("No se pudo crear el proyecto:") + f"\n\n{e}")
            return
        self.statusBar().clearMessage()

        def back() -> None:
            page = self.stack.currentWidget()
            self.stack.setCurrentIndex(self.stack.count() - 2)  # vuelve a la página del juego
            self.stack.removeWidget(page)
            page.deleteLater()

        plugin = self.game_plugins.get(appid, (None, None))[1]

        def make_engine(proj: pj.Project) -> Engine:
            return Engine(proj, game.path, self._cache(), MODELS_DIR, plugin=plugin, transcriber_factory=transcribe.Whisper)

        def get_key(name: str) -> str | None:
            try:
                return keys.get(name)
            except Exception:  # backend de keyring no disponible
                return None

        page = TranslationPage(
            project,
            file,
            self.prices,
            lambda: _save_json(PRICES_FILE, self.prices),
            back,
            provider=next((p for p in self.providers if p.name == provider), None),
            get_key=get_key,
            make_engine=make_engine,
            tts=self._tts(),
            dub_dir=PROJECTS_DIR / (re.sub(r"[^\w.-]", "_", appid) + "_voz"),
            game_path=game.path,
            backup_dir=DATA_DIR / "backups" / re.sub(r"[^\w.-]", "_", appid),
        )
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def _remember_game_prefs(self, appid: str, provider: str, language: str) -> None:
        """Guarda la IA y el idioma elegidos para este juego (vale para todas sus versiones)."""
        self.game_prefs[self.group_of[appid]] = {"provider": provider, "language": language}
        _save_json(GAME_PREFS_FILE, self.game_prefs)

    def _tts(self) -> dict:
        if getattr(self, "_tts_providers", None) is None:
            self._tts_providers = {
                p.name: p for p in (tts.WindowsVoices(), tts.Piper(DATA_DIR / "piper"), tts.ElevenLabsVoices())
            }
        return self._tts_providers

    def _cache(self) -> Cache:
        if getattr(self, "_engine_cache", None) is None:
            self._engine_cache = Cache(ENGINE_CACHE)
        return self._engine_cache

    def _rescan(self, appid: str) -> None:
        """Vuelve a analizar un juego (ej. después de extraer audios) y reabre su página al terminar."""
        self.open_when_ready = appid
        self._queue_scan(self.games[appid])
        self.statusBar().showMessage(tr("Volviendo a analizar el juego…"))

    def _switch_version(self, appid: str) -> None:
        self.preferred[self.group_of[appid]] = appid  # recordar la versión elegida
        _save_json(PREFS_FILE, self.preferred)
        if appid not in self.cache or self.status[appid] in ("pending", "scanning"):
            self.statusBar().showMessage(tr("Esa versión todavía se está analizando; se abre sola cuando termine"))
            self.open_when_ready = appid
            return
        self._back()
        self._open_game(appid)

    def _add_version(self, appid: str) -> None:
        """Agrega otra carpeta del mismo juego, solo si de verdad es el mismo juego."""
        key = self.group_of[appid]
        reference = next((v for v in self.groups[key] if v.store != "Manual"), self.groups[key][0])
        folder = QFileDialog.getExistingDirectory(self, tr("Elegí la carpeta de {name}", name=reference.name))
        if not folder:
            return
        score = stores.same_game_score(reference.path, Path(folder))
        if score < stores.SAME_GAME_MIN:
            QMessageBox.warning(
                self,
                "Revoz",
                tr("Esa carpeta no parece ser {name}.", name=reference.name) + "\n\n"
                + tr("Solo coincide el {score} de los archivos con la versión de {store}.", score=f"{score:.0%}", store=store_name(reference.store))
                + " " + tr("Elegí la carpeta principal del juego (la que tiene el .exe)."),
            )
            return
        if any(Path(folder).resolve() == v.path.resolve() for v in self.games.values()):
            QMessageBox.information(self, "Revoz", tr("Esa carpeta ya está en la biblioteca."))
            return
        try:
            game = stores.add_manual(MANUAL_FILE, Path(folder), reference.name)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Revoz", tr("No se pudo agregar la carpeta:") + "\n\n" + tr(str(e)))
            return
        self._add_item(game)
        self._update_header()
        self._switch_version(game.appid)  # queda elegida; se abre cuando termine de analizarse
        self._back()
        self._open_game(appid)

    def _back(self) -> None:
        """Cierra la página de arriba de la biblioteca (traducción -> juego -> grilla)."""
        if self.stack.count() <= 1:
            return
        page = self.stack.widget(self.stack.count() - 1)
        self.stack.removeWidget(page)
        page.deleteLater()
        self.stack.setCurrentIndex(self.stack.count() - 1)
