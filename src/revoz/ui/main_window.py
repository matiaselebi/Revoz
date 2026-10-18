"""Interfaz PySide6. Solo muestra; la lógica vive en revoz.core."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QFile, QObject, QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from revoz.core import keys, plugins, providers, scan, steam
from revoz.core.plugins import GamePlugin, PluginInfo
from revoz.core.providers import Provider, ProviderError
from revoz.core.scan import ScanResult
from revoz.core.steam import Game

# Los plugins viven junto a la app (al lado del .exe, o en la raíz del proyecto al correr desde el código).
# ponytail: si algún día se instala en Program Files (sin permiso de escritura), mover plugins a APPDATA.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
PLUGIN_DIR = APP_DIR / "plugins"
# La lista de plugins aprobados queda en el perfil del usuario, no en la carpeta de la app:
# así un paquete descargado no puede traer plugins "pre-aprobados".
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Revoz"
CACHE_FILE = DATA_DIR / "scan_cache_v2.json"
TRUST_FILE = DATA_DIR / "trusted_plugins.json"

COVER = QSize(150, 225)
STATUS_TEXT = {
    "plugin": "Compatible gracias a un plugin",
    "green": "Audio legible: fácil de traducir",
    "yellow": "Audio empaquetado sin cifrar: necesita un plugin que lo extraiga",
    "locked": "Audio cifrado: necesita un plugin con la clave del juego",
    "red": "No se encontró audio",
    "scanning": "Analizando…",
    "pending": "En cola para analizar",
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
KIND_TEXT = {scan.EASY: "legible", scan.PACK: "empaquetado", scan.LOCKED: "cifrado"}

STYLE = """
QWidget { background: #16181d; color: #e6e6e6; font-size: 10pt; }
QListWidget { border: none; }
QListWidget::item:selected, QListWidget::item:hover { background: #262a33; border-radius: 6px; }
QPushButton { background: #2d333b; border: 1px solid #444c56; border-radius: 5px; padding: 6px 14px; }
QPushButton:hover { background: #373e47; }
QPushButton:disabled { color: #6e7681; }
QLineEdit, QComboBox { background: #0d1117; border: 1px solid #444c56; border-radius: 5px; padding: 5px; }
QGroupBox { border: 1px solid #30363d; border-radius: 6px; margin-top: 14px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #16181d; padding: 8px 18px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { border-bottom: 2px solid #2ea043; }
QTableWidget { border: 1px solid #30363d; gridline-color: #30363d; }
QHeaderView::section { background: #2d333b; border: none; padding: 5px; }
"""


def cover_pixmap(game: Game, status: str | None, size: QSize = COVER) -> QPixmap:
    pm = QPixmap(size)
    pm.fill(QColor("#2a2d34"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    src = QPixmap(str(game.cover)) if game.cover else QPixmap()
    if not src.isNull():
        p.drawPixmap(0, 0, src.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    else:
        p.setPen(QColor("#e6e6e6"))
        p.drawText(pm.rect().adjusted(10, 10, -10, -10), Qt.AlignCenter | Qt.TextWordWrap, game.name)

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


class _Bus(QObject):
    """Lleva resultados de hilos de fondo al hilo de la UI."""

    started = Signal(str)
    scanned = Signal(str, object)
    checked = Signal(bool, str)


class GamePage(QWidget):
    def __init__(
        self,
        game: Game,
        result: ScanResult,
        plugin: PluginInfo | None,
        provider_list: list[Provider],
        on_back,
    ) -> None:
        super().__init__()
        self.providers = {p.name: p for p in provider_list}
        self.bus = _Bus()
        self.bus.checked.connect(self._on_checked)

        back = QPushButton("← Biblioteca")
        back.clicked.connect(on_back)

        status = "plugin" if plugin else result.status
        cover = QLabel()
        cover.setPixmap(cover_pixmap(game, status, QSize(200, 300)))

        title = QLabel(game.name)
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        sizes = ", ".join(
            f"{KIND_TEXT[k]} {b / 1048576:,.0f} MB" for k, b in sorted(result.bytes_by_kind.items(), key=lambda kv: -kv[1])
        )
        plugin_line = f"Plugin: {plugin.name} v{plugin.version} (de {plugin.author})\n\n" if plugin else ""
        info = QLabel(
            plugin_line
            + f"Análisis de archivos: {STATUS_TEXT[result.status]}\n\n"
            + "\n".join(f"• {fmt}: {n} archivos" for fmt, n in sorted(result.counts.items()))
            + (f"\n\nTamaño: {sizes}" if sizes else "")
            + f"\n\nCarpeta: {game.path}\nArchivos analizados: {result.files}"
        )
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # --- IA, clave e idioma ---
        self.provider = QComboBox()
        self.provider.addItems(list(self.providers))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Pegá tu API key")
        self.key_state = QLabel()
        save = QPushButton("Guardar")
        save.clicked.connect(self._save_key)
        delete = QPushButton("Borrar")
        delete.clicked.connect(self._delete_key)
        self.provider.currentTextChanged.connect(self._on_provider_changed)

        self.language = QComboBox()
        for code, name in providers.TARGET_LANGUAGES:
            self.language.addItem(name, code)
        self.language.currentIndexChanged.connect(lambda: self.lang_state.clear())
        self.check = QPushButton("Comprobar idioma")
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
        form.addRow("Proveedor de IA:", self.provider)
        form.addRow("API key:", self.key)
        form.addRow("", self.key_state)
        form.addRow("", key_buttons)
        form.addRow("Traducir al:", lang_row)
        form.addRow("", self.lang_state)
        api_box = QGroupBox("Traducción")
        api_box.setLayout(form)

        translate = QPushButton("Traducir voces")
        translate.setEnabled(False)
        translate.setToolTip("Próximamente")

        right = QVBoxLayout()
        right.addWidget(title)
        right.addWidget(info)
        right.addWidget(api_box)
        right.addWidget(translate, alignment=Qt.AlignLeft)
        right.addStretch()

        body = QHBoxLayout()
        body.addWidget(cover, alignment=Qt.AlignTop)
        body.addSpacing(20)
        body.addLayout(right, 1)

        root = QVBoxLayout(self)
        root.addWidget(back, alignment=Qt.AlignLeft)
        root.addLayout(body, 1)
        self._on_provider_changed()

    def _on_provider_changed(self) -> None:
        self.lang_state.clear()
        try:
            ok = keys.has(self.provider.currentText())
        except Exception:  # backend de keyring no disponible
            ok = False
        self.key_state.setText("✓ Clave guardada" if ok else "Sin clave guardada")

    def _save_key(self) -> None:
        try:
            keys.save(self.provider.currentText(), self.key.text())
        except ValueError as e:
            QMessageBox.warning(self, "Revoz", str(e))
            return
        except Exception:
            QMessageBox.critical(self, "Revoz", "No se pudo guardar en el almacén seguro del sistema.")
            return
        finally:
            self.key.clear()  # no dejar la clave en el widget
        self._on_provider_changed()

    def _delete_key(self) -> None:
        try:
            keys.delete(self.provider.currentText())
        except Exception:
            QMessageBox.critical(self, "Revoz", "No se pudo borrar del almacén seguro del sistema.")
        self._on_provider_changed()

    def _check_language(self) -> None:
        name = self.provider.currentText()
        try:
            key = keys.get(name)
        except Exception:
            key = None
        if not key:
            self.lang_state.setText("Primero guardá la API key de este proveedor.")
            return
        provider, code, lang = self.providers[name], self.language.currentData(), self.language.currentText()
        self.check.setEnabled(False)
        self.lang_state.setText(f"Consultando a {name}…")

        def work() -> None:
            try:
                langs = provider.languages(key)
                if langs is None:
                    ok, msg = True, f"✓ Clave válida. {name} es un modelo de lenguaje: traduce al {lang} sin problema."
                elif providers.supports(langs, code):
                    ok, msg = True, f"✓ {name} soporta {lang}."
                else:
                    ok, msg = False, f"✗ {name} no soporta {lang}."
            except ProviderError as e:
                ok, msg = False, f"✗ {e}"
            except Exception:  # respuesta inesperada o plugin con errores
                ok, msg = False, "✗ Respuesta inesperada del servicio"
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Revoz")
        self.resize(1100, 720)
        self.setStyleSheet(STYLE)

        self.cache = scan.load_cache(CACHE_FILE)
        self.status: dict[str, str] = {}
        self.bus = _Bus()
        self.bus.started.connect(self._on_started)
        self.bus.scanned.connect(self._on_scanned)

        self._load_plugins()

        root = steam.find_steam()
        try:
            self.games = {g.appid: g for g in steam.list_games(root)} if root else {}
        except (OSError, ValueError):
            self.games = {}

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(COVER)
        self.grid.setGridSize(QSize(COVER.width() + 24, COVER.height() + 50))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setWordWrap(True)
        self.grid.itemClicked.connect(self._on_click)

        self.items: dict[str, QListWidgetItem] = {}
        queue = []
        for g in self.games.values():
            item = QListWidgetItem(g.name)
            item.setData(Qt.UserRole, g.appid)
            self.grid.addItem(item)
            self.items[g.appid] = item
            cached = self.cache.get(g.appid)
            if cached and cached["buildid"] == g.buildid:
                self._set_status(g.appid, cached["result"]["status"])
            else:  # nunca analizado o Steam lo actualizó
                self._set_status(g.appid, "pending")
                queue.append(g)

        header = QLabel(f"Biblioteca de Steam — {len(self.games)} juegos" if root else "No se encontró Steam")
        header.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 8px;")
        library = QWidget()
        lay = QVBoxLayout(library)
        lay.addWidget(header)
        lay.addWidget(self.grid)

        self.tabs = QTabWidget()
        self.tabs.addTab(library, "Biblioteca")
        self.tabs.addTab(self._build_plugins_tab(), "Plugins")

        self.stack = QStackedWidget()
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        self.pending = len(queue)
        if queue:
            # Un solo hilo, en orden: no saturar el disco escaneando todo en paralelo.
            threading.Thread(target=self._scan_all, args=(queue,), daemon=True).start()
        self._show_progress()

    # --- plugins ---

    def _load_plugins(self) -> None:
        self.game_plugins: dict[str, tuple[PluginInfo, GamePlugin]] = {}
        self.providers: list[Provider] = list(providers.BUILTIN)
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        found, errors = plugins.discover(PLUGIN_DIR)
        trusted = plugins.load_trusted(TRUST_FILE)
        # filas de la pestaña Plugins: (carpeta, nombre, versión, autor, tipo, estado)
        self.plugin_rows = [(e.split(":")[0], "", "", "", "", "Error:" + e.split(":", 1)[1]) for e in errors]
        for info in found:
            kind = f"Juego ({', '.join(info.appids)})" if info.kind == plugins.GAME else "Servicio de IA"
            row = (info.id, info.name, info.version, info.author, kind)
            if not plugins.is_trusted(trusted, info):
                answer = QMessageBox.question(
                    self,
                    "Revoz: plugin nuevo",
                    f"Se encontró un plugin nuevo o modificado:\n\n"
                    f"{info.name} v{info.version}\nAutor: {info.author}\nCarpeta: {info.path}\n\n"
                    "Los plugins ejecutan código en tu PC. Activalo solo si confiás en quien lo hizo.\n\n"
                    "¿Activar este plugin?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    self.plugin_rows.append((*row, "No aprobado"))
                    continue
                plugins.trust(TRUST_FILE, trusted, info)
            try:
                instance = plugins.load(info, trusted)
            except plugins.PluginError as e:
                errors.append(f"{info.id}: {e}")
                self.plugin_rows.append((*row, f"Error: {e}"))
                continue
            self.plugin_rows.append((*row, "Activo"))
            if info.kind == plugins.GAME:
                for appid in info.appids:
                    self.game_plugins[appid] = (info, instance)
            else:
                instance.name = instance.name or info.name
                self.providers.append(instance)
        self.plugin_rows.sort()
        if errors:
            QMessageBox.warning(self, "Revoz", "Algunos plugins no se cargaron:\n\n" + "\n".join(errors))

    def _build_plugins_tab(self) -> QWidget:
        hint = QLabel(
            f"Los plugins se guardan en: {PLUGIN_DIR}\n"
            "Agregá un plugin desde un .zip o una carpeta. Antes de activarse, Revoz te pide que lo apruebes."
        )
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.plugin_table = QTableWidget(0, 6)
        self.plugin_table.setHorizontalHeaderLabels(["Carpeta", "Nombre", "Versión", "Autor", "Tipo", "Estado"])
        self.plugin_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.plugin_table.horizontalHeader().setStretchLastSection(True)
        self.plugin_table.verticalHeader().hide()
        self.plugin_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.plugin_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.plugin_table.setSelectionMode(QAbstractItemView.SingleSelection)

        buttons = QHBoxLayout()
        for text, slot in (
            ("Agregar desde .zip", self._add_plugin_zip),
            ("Agregar carpeta", self._add_plugin_folder),
            ("Quitar", self._remove_plugin),
            ("Recargar", self._reload_plugins),
            ("Abrir carpeta", self._open_plugin_dir),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addStretch()

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
                cell = QTableWidgetItem(text)
                if c == 5:
                    cell.setForeground(QColor("#3fb950" if text == "Activo" else "#f85149"))
                self.plugin_table.setItem(r, c, cell)

    def _install_plugin(self, source: str) -> None:
        if not source:
            return
        try:
            info = plugins.install(Path(source), PLUGIN_DIR)
        except (plugins.PluginError, OSError) as e:
            QMessageBox.warning(self, "Revoz", f"No se pudo agregar el plugin:\n\n{e}")
            return
        self.statusBar().showMessage(f"Plugin '{info.name}' agregado")
        self._reload_plugins()

    def _add_plugin_zip(self) -> None:
        self._install_plugin(QFileDialog.getOpenFileName(self, "Elegí el plugin", "", "Plugin (*.zip)")[0])

    def _add_plugin_folder(self) -> None:
        self._install_plugin(QFileDialog.getExistingDirectory(self, "Elegí la carpeta del plugin"))

    def _remove_plugin(self) -> None:
        rows = self.plugin_table.selectionModel().selectedRows()
        if not rows:
            self.statusBar().showMessage("Elegí un plugin de la lista para quitarlo")
            return
        folder = self.plugin_rows[rows[0].row()][0]
        path = PLUGIN_DIR / folder
        answer = QMessageBox.question(
            self, "Revoz", f"¿Quitar el plugin '{folder}'?\n\nLa carpeta se manda a la papelera de reciclaje."
        )
        if answer != QMessageBox.Yes:
            return
        if not QFile.moveToTrash(str(path)):
            QMessageBox.warning(self, "Revoz", f"No se pudo mandar a la papelera:\n{path}")
            return
        self._reload_plugins()

    def _reload_plugins(self) -> None:
        self._load_plugins()
        self._fill_plugin_table()
        for appid in self.items:
            self._set_status(appid, self.status[appid])

    def _open_plugin_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(PLUGIN_DIR)))

    # --- análisis ---

    def _scan_all(self, queue: list[Game]) -> None:
        for game in queue:
            self.bus.started.emit(game.appid)
            try:
                result = scan.scan_dir(game.path)
            except Exception:
                result = ScanResult("red", {}, 0)
            self.bus.scanned.emit(game.appid, result)

    def _show_progress(self) -> None:
        if self.pending:
            self.statusBar().showMessage(f"Analizando audio de la biblioteca… faltan {self.pending} juegos")
        else:
            self.statusBar().showMessage("Biblioteca analizada. Hacé clic en un juego para ver el detalle")

    def _set_status(self, appid: str, status: str) -> None:
        self.status[appid] = status
        shown = "plugin" if appid in self.game_plugins and status not in ("pending", "scanning") else status
        item = self.items[appid]
        item.setIcon(QIcon(cover_pixmap(self.games[appid], shown)))
        tip = STATUS_TEXT[shown]
        if shown == "plugin":
            tip += f": {self.game_plugins[appid][0].name}"
        item.setToolTip(tip)

    def _on_click(self, item: QListWidgetItem) -> None:
        appid = item.data(Qt.UserRole)
        st = self.status[appid]
        if st in ("pending", "scanning") or (st == "red" and appid not in self.game_plugins):
            self.statusBar().showMessage(f"{self.games[appid].name}: {STATUS_TEXT[st]}")
        else:
            self._open_game(appid)

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

    # --- navegación ---

    def _open_game(self, appid: str) -> None:
        result = ScanResult(**self.cache[appid]["result"])
        plugin = self.game_plugins.get(appid, (None, None))[0]
        page = GamePage(self.games[appid], result, plugin, self.providers, self._back)
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def _back(self) -> None:
        page = self.stack.currentWidget()
        self.stack.setCurrentIndex(0)
        self.stack.removeWidget(page)
        page.deleteLater()
