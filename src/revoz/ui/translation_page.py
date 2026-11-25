"""Pantalla de traducción: progreso, costo estimado y revisión línea por línea."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from revoz.core import install, valve
from revoz.core import project as pj
from revoz.core.providers import Provider
from revoz.ui.auto_dialog import AutoDialog, GlossaryDialog
from revoz.ui.i18n import N_, tr
from revoz.ui.player import Player
from revoz.ui.voices_dialog import VoicesDialog

COLUMNS = ["", "", N_("Archivo"), N_("Personaje"), N_("Duración"), N_("Desde"), N_("Hasta"), N_("Texto original"), N_("Traducción"), N_("Voz"), N_("Estado")]
CHECK, PLAY, FILE, CHARACTER, DURATION, FROM, TO, ORIGINAL, TRANSLATION, VOICE, STATUS = range(len(COLUMNS))
# Los estados se guardan en español (en el proyecto y en el CSV); se traducen solo al mostrarlos.
STATUS_NAMES = {pj.PENDING: N_("pendiente"), pj.TRANSCRIBED: N_("transcrita"), pj.TRANSLATED: N_("traducida"), pj.REVIEWED: N_("revisada")}
EDITABLE = {CHARACTER: "character", FROM: "clip_start", TO: "clip_end", ORIGINAL: "original", TRANSLATION: "translation", STATUS: "status"}
STATUS_COLOR = {pj.PENDING: "#6e7681", pj.TRANSCRIBED: "#d29922", pj.TRANSLATED: "#58a6ff", pj.REVIEWED: "#3fb950"}


class LinesModel(QAbstractTableModel):
    def __init__(self, lines: list[pj.Line], on_change, on_error=None, on_checked=None, confirm_clip=None) -> None:
        super().__init__()
        self.lines = lines
        self.on_change = on_change
        self.on_error = on_error or (lambda msg: None)
        self.on_checked = on_checked or (lambda: None)
        self.confirm_clip = confirm_clip or (lambda line, start, end: True)  # antes de borrar texto al cambiar el tramo
        self.playing = -1  # fila que está sonando
        self.playing_dub = -1  # fila cuya voz traducida está sonando

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.lines)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return tr(COLUMNS[section]) if COLUMNS[section] else ""
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole:
            return {CHECK: tr("Elegí qué líneas traducir"), PLAY: tr("Escuchar"),
                    FROM: tr("Traducir desde este segundo (vacío = desde el principio)"),
                    TO: tr("Traducir hasta este segundo (vacío = hasta el final)")}.get(section)  # fmt: skip
        return None

    def data(self, index, role=Qt.DisplayRole):
        line = self.lines[index.row()]
        col = index.column()
        if col == CHECK:
            return (Qt.Checked if line.selected else Qt.Unchecked) if role == Qt.CheckStateRole else None
        if col == PLAY:
            if role == Qt.DisplayRole:
                return "■" if index.row() == self.playing else "▶"
            if role == Qt.TextAlignmentRole:
                return Qt.AlignCenter
            if role == Qt.ForegroundRole:
                return QColor("#3fb950") if index.row() == self.playing else None
            return None
        if col == VOICE:
            has = bool(line.dub_path) and Path(line.dub_path).is_file()
            if role == Qt.DisplayRole:
                return ("■ " if index.row() == self.playing_dub else "▶ ") + (tr("no entra") if line.dub_fits is False else tr("escuchar")) if has else ""
            if role == Qt.ForegroundRole and has:
                return QColor("#f85149" if line.dub_fits is False else "#3fb950")
            if role == Qt.ToolTipRole and has:
                if line.dub_fits is False:
                    return tr("La voz traducida dura más que el original aunque se aceleró: conviene acortar la traducción")
                return tr("Escuchar la voz traducida")
            return None
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == FILE:
                return line.id
            if col == DURATION:
                return "" if line.duration is None else f"{line.duration:.1f} s"
            if col in (FROM, TO):
                return pj.format_time(line.clip_start if col == FROM else line.clip_end)
            if col == STATUS and role == Qt.DisplayRole:
                if line.no_voice:
                    return tr("sin voz")
                text = tr(STATUS_NAMES.get(line.status, line.status))
                if line.error:
                    return "⚠ " + text
                return text + (" · " + tr("dudosa") if line.doubtful else "")
            return getattr(line, EDITABLE[col])
        if role == Qt.ForegroundRole and col == STATUS:
            if line.no_voice:
                return QColor("#6e7681")
            if line.error:
                return QColor("#f85149")
            if line.doubtful:
                return QColor("#d29922")
            return QColor(STATUS_COLOR.get(line.status, "#e6e6e6"))
        if role == Qt.ToolTipRole and col == STATUS:
            if line.error:
                return tr(line.error)
            if line.doubtful:
                return tr("Whisper no está seguro de lo que escuchó: conviene revisarla")
            return None
        if role == Qt.ToolTipRole and col in (FILE, ORIGINAL, TRANSLATION):
            return self.data(index)
        return None

    def flags(self, index):
        base = super().flags(index)
        if index.column() == CHECK:
            return base | Qt.ItemIsUserCheckable
        return base | Qt.ItemIsEditable if index.column() in EDITABLE else base

    def set_checked(self, rows: list[int], checked: bool) -> None:
        for r in rows:
            self.lines[r].selected = checked
        if rows:
            self.dataChanged.emit(self.index(min(rows), CHECK), self.index(max(rows), CHECK))
        self.on_checked()

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if index.column() == CHECK and role == Qt.CheckStateRole:
            self.set_checked([index.row()], Qt.CheckState(value) == Qt.Checked)
            self.on_change()
            return True
        if role != Qt.EditRole or index.column() not in EDITABLE:
            return False
        line = self.lines[index.row()]
        attr = EDITABLE[index.column()]
        if attr in ("clip_start", "clip_end"):
            try:
                value = pj.parse_time(str(value))
                start, end = (value, line.clip_end) if attr == "clip_start" else (line.clip_start, value)
                if not self.confirm_clip(line, start, end):
                    return False
                pj.set_clip(line, start, end)
            except ValueError as e:
                self.on_error(tr(str(e)) if "'" in str(e) else tr("Escribí el tiempo en segundos (83.5) o minutos:segundos (1:23.5)."))
                return False
        elif attr == "status":
            if value not in pj.STATUSES:
                return False
            line.status = value
        else:
            pj.update(line, attr, str(value))
        # el estado puede haber cambiado solo: refrescar toda la fila
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
        self.on_change()
        return True

    def reset(self) -> None:
        self.beginResetModel()
        self.endResetModel()


class StatusDelegate(QStyledItemDelegate):
    """Editor de la columna Estado: un desplegable con los estados válidos."""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        for s in pj.STATUSES:
            combo.addItem(tr(STATUS_NAMES[s]), s)
        return combo

    def setEditorData(self, editor, index):
        editor.setCurrentIndex(max(0, editor.findData(index.data(Qt.EditRole))))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData())


class LinesFilter(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.text = ""
        self.status = ""

    def set_filter(self, text: str, status: str) -> None:
        self.text, self.status = text.lower(), status
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        line = self.sourceModel().lines[row]
        if self.status == "doubtful":
            if not line.doubtful:
                return False
        elif self.status == "no_voice":
            if not line.no_voice:
                return False
        elif self.status == "error":
            if not line.error:
                return False
        elif self.status and line.status != self.status:
            return False
        if not self.text:
            return True
        return any(self.text in s.lower() for s in (line.id, line.character, line.original, line.translation))


class _JobBus(QObject):
    """Lleva el avance del motor (que corre en otro hilo) a la pantalla."""

    progress = Signal(int, int, str, int)  # hechas, total, mensaje, índice de línea
    done = Signal(object)  # Summary o el error
    installed = Signal(str, object)  # "install" / "restore", Result o el error
    game_changed = Signal(list)  # audios que una actualización del juego volvió al original


class TranslationPage(QWidget):
    def __init__(
        self,
        project: pj.Project,
        project_file: Path,
        prices: dict,
        on_prices_changed,
        on_back,
        provider: Provider | None = None,
        get_key=None,  # proveedor -> API key guardada
        make_engine=None,  # (proyecto) -> Engine listo para correr
        tts: dict | None = None,  # voces disponibles: nombre -> TTSProvider
        dub_dir: Path | None = None,  # dónde se guardan los audios traducidos del proyecto
        game_path: Path | None = None,  # carpeta del juego (para instalar la voz traducida)
        backup_dir: Path | None = None,  # copias de los audios originales que se reemplazan
    ) -> None:
        super().__init__()
        self.tts, self.dub_dir = tts, dub_dir
        self.game_path, self.backup_dir = game_path, backup_dir
        self.provider, self.get_key, self.make_engine = provider, get_key, make_engine
        self.engine = None
        self.job_bus = _JobBus()
        self.job_bus.progress.connect(self._on_job_progress)
        self.job_bus.done.connect(self._on_job_done)
        self.job_bus.installed.connect(self._on_installed)
        self.job_bus.game_changed.connect(self._on_game_changed)
        self.stats_timer = QTimer(self, interval=700)  # refresca barras sin trabarse con miles de líneas
        self.stats_timer.timeout.connect(self._refresh_stats)
        self.project = project
        self.project_file = project_file
        self.prices = prices  # proveedor -> [USD por minuto, USD por millón de caracteres]
        self.on_prices_changed = on_prices_changed

        # Guardado con demora: muchas ediciones seguidas = una sola escritura a disco.
        self.save_timer = QTimer(self, singleShot=True, interval=800)
        self.save_timer.timeout.connect(self._save)

        back = QPushButton(tr("← Volver al juego"))
        back.clicked.connect(lambda: (self.stop(), self._save(), on_back()))
        title = QLabel(tr("Traducción") + f" - {project.game}")
        title.setStyleSheet("font-size: 16pt; font-weight: bold;")
        subtitle = QLabel(
            tr("Idioma: {lang}", lang=project.language) + "   ·   " + tr("IA: {name}", name=project.provider)
            + "   ·   " + tr("{n} líneas de voz", n=len(project.lines))
        )

        # --- progreso ---
        self.bars: dict[str, QProgressBar] = {}
        prog_form = QFormLayout()
        for status, label in ((pj.TRANSCRIBED, tr("Transcritas")), (pj.TRANSLATED, tr("Traducidas")), (pj.REVIEWED, tr("Revisadas"))):
            bar = QProgressBar()
            bar.setMaximum(max(1, len(project.lines)))
            bar.setFormat(tr("%v de {total} (%p%)", total=len(project.lines)))
            self.bars[status] = bar
            prog_form.addRow(label + ":", bar)
        self.auto = QPushButton(tr("Traducir automáticamente con IA"))
        self.auto.setObjectName("primary")
        self.auto.setToolTip(tr("Transcribe las voces y traduce el texto. Podés pausar cuando quieras."))
        self.auto.clicked.connect(self._start_auto)
        self.auto.setEnabled(make_engine is not None)
        glossary = QPushButton(tr("Glosario y personajes…"))
        glossary.setToolTip(tr("Nombres que no se traducen, cómo es cada personaje y de qué trata el juego"))
        glossary.clicked.connect(self._open_glossary)
        voices = QPushButton(tr("Voces…"))
        voices.setToolTip(tr("Qué voz usa cada personaje y cómo se ajusta la duración"))
        voices.clicked.connect(self._open_voices)
        self.pause = QPushButton(tr("Pausar"))
        self.pause.clicked.connect(self._pause)
        self.pause.hide()
        auto_row = QHBoxLayout()
        auto_row.addWidget(self.auto, 1)
        auto_row.addWidget(self.pause)
        auto_row.addWidget(glossary)
        auto_row.addWidget(voices)
        prog_form.addRow(auto_row)
        self.job_bar = QProgressBar()
        self.job_bar.hide()
        self.job_label = QLabel()
        self.job_label.setObjectName("hint")
        self.job_label.setWordWrap(True)
        prog_form.addRow(self.job_bar)
        prog_form.addRow(self.job_label)
        # --- instalar en el juego ---
        self.install_btn = QPushButton(tr("Instalar en el juego"))
        self.install_btn.setToolTip(tr("Reemplaza los audios del juego por la voz traducida. Antes guarda una copia de cada original."))
        self.install_btn.clicked.connect(self._install)
        self.restore_btn = QPushButton(tr("Restaurar original"))
        self.restore_btn.setToolTip(tr("Vuelve a poner los audios originales del juego."))
        self.restore_btn.clicked.connect(self._restore)
        self.install_state = QLabel()
        self.install_state.setObjectName("hint")
        self.install_state.setWordWrap(True)
        game_row = QHBoxLayout()
        game_row.addWidget(self.install_btn)
        game_row.addWidget(self.restore_btn)
        game_row.addWidget(self.install_state, 1)
        prog_form.addRow(game_row)
        self._update_install_state()
        self._check_game_changed()
        prog_box = QGroupBox(tr("Progreso"))
        prog_box.setLayout(prog_form)

        # --- costo estimado ---
        saved = prices.get(project.provider, [0.0, 0.0])
        self.per_minute = QDoubleSpinBox(decimals=3, maximum=1000, prefix="US$ ", value=saved[0])
        self.per_mchar = QDoubleSpinBox(decimals=2, maximum=100000, prefix="US$ ", value=saved[1])
        self.per_minute.valueChanged.connect(self._prices_changed)
        self.per_mchar.valueChanged.connect(self._prices_changed)
        self.cost_label = QLabel()
        self.cost_label.setWordWrap(True)
        cost_form = QFormLayout()
        cost_form.addRow(tr("Precio por minuto de audio:"), self.per_minute)
        cost_form.addRow(tr("Precio por millón de caracteres:"), self.per_mchar)
        cost_form.addRow(self.cost_label)
        self.spent_label = QLabel()
        cost_form.addRow(self.spent_label)
        hint = QLabel(tr("Poné los precios que figuran en la página de tu proveedor. Revoz no los conoce ni los inventa."))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        cost_form.addRow(hint)
        cost_box = QGroupBox(tr("Costo estimado ({name})", name=project.provider))
        cost_box.setLayout(cost_form)

        top = QHBoxLayout()
        top.addWidget(prog_box, 1)
        top.addWidget(cost_box, 1)

        # --- revisión ---
        self.model = LinesModel(project.lines, self._changed, self._show_error, self._update_checked, self._confirm_clip)
        self.proxy = LinesFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.setItemDelegateForColumn(STATUS, StatusDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate((30, 34, 240, 95, 65, 60, 60, 260, 260, 90, 120)):
            self.table.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        self.table.clicked.connect(self._on_cell_clicked)
        self.table.doubleClicked.connect(self._on_cell_double_clicked)

        self.search = QLineEdit(placeholderText=tr("Buscar por archivo, personaje o texto…"))
        self.status_filter = QComboBox()
        self.status_filter.addItem(tr("Todos los estados"), "")
        for s in pj.STATUSES:
            self.status_filter.addItem(tr(STATUS_NAMES[s]).capitalize(), s)
        self.status_filter.addItem(tr("Dudosas"), "doubtful")
        self.status_filter.addItem(tr("Sin voz"), "no_voice")
        self.status_filter.addItem(tr("Con error"), "error")
        self.search.textChanged.connect(self._apply_filter)
        self.status_filter.currentIndexChanged.connect(self._apply_filter)
        self.shown_label = QLabel()

        self.select_all = QPushButton(tr("Seleccionar todo"))
        self.select_all.setToolTip(tr("Elige todas las líneas que muestra el filtro. Si ya están todas elegidas, las quita."))
        self.select_all.clicked.connect(self._toggle_all)
        self.checked_label = QLabel()
        mark = QPushButton(tr("Marcar como revisadas"))
        mark.setToolTip(tr("Marca las líneas seleccionadas que ya tienen traducción"))
        mark.clicked.connect(self._mark_reviewed)
        export = QPushButton(tr("Exportar CSV…"))
        export.setToolTip(tr("Para revisar en una planilla o repartir el trabajo (exporta lo que se ve con el filtro)"))
        export.clicked.connect(self._export)
        imp = QPushButton(tr("Importar CSV…"))
        imp.setToolTip(tr("Suma el trabajo de otra persona; las celdas vacías no borran nada"))
        imp.clicked.connect(self._import)

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.status_filter)
        filters.addWidget(self.shown_label)
        actions = QHBoxLayout()
        actions.addWidget(self.select_all)
        actions.addWidget(self.checked_label)
        actions.addSpacing(12)
        for b in (mark, export, imp):
            actions.addWidget(b)
        actions.addStretch()

        review_lay = QVBoxLayout()
        review_lay.addLayout(filters)
        review_lay.addLayout(actions)
        self.player = Player()
        self.player.previous.connect(lambda: self._step(-1))
        self.player.next.connect(lambda: self._step(1))
        self.player.clip_chosen.connect(self._clip_from_wave)
        # Divisor entre la tabla y el reproductor: se ajusta arrastrando
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.player)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        review_lay.addWidget(split, 1)
        review_box = QGroupBox(tr("Revisión de líneas (doble clic en una celda para editarla)"))
        review_box.setLayout(review_lay)

        root = QVBoxLayout(self)
        root.addWidget(back, alignment=Qt.AlignLeft)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(top)
        root.addWidget(review_box, 1)

        self._refresh_stats()
        self._apply_filter()

    # --- datos ---

    def _changed(self) -> None:
        self._refresh_stats()
        self.save_timer.start()

    def _save(self) -> None:
        self.save_timer.stop()
        try:
            pj.save(self.project_file, self.project)
        except OSError as e:
            QMessageBox.critical(self, "Revoz", tr("No se pudo guardar el proyecto:") + f"\n\n{e}")

    def _refresh_stats(self) -> None:
        for status, count in pj.progress(self.project.lines).items():
            self.bars[status].setValue(count)
        e = pj.estimate(self.project.lines, self.per_minute.value(), self.per_mchar.value())
        missing = "\n" + tr("{n} archivos sin duración conocida (no suman).", n=e.without_duration) if e.without_duration else ""
        self.cost_label.setText(
            tr("Audio: {minutes} min", minutes=f"{e.minutes:,.1f}") + "   ·   "
            + tr("Texto: ~{chars} caracteres", chars=f"{e.chars:,}") + missing + "\n"
            + tr("Costo estimado: ≈ US$ {cost}", cost=f"{e.cost:,.2f}")
        )
        limit = tr("sin límite") if not self.project.limit else f"US$ {self.project.limit:,.2f}"
        self.spent_label.setText(tr("Gastado: US$ {spent} (límite: {limit})", spent=f"{self.project.spent:,.4f}", limit=limit))

    def _prices_changed(self) -> None:
        self.prices[self.project.provider] = [self.per_minute.value(), self.per_mchar.value()]
        self.on_prices_changed()
        self._refresh_stats()

    def _apply_filter(self) -> None:
        self.proxy.set_filter(self.search.text(), self.status_filter.currentData())
        self.shown_label.setText(tr("{shown} de {total}", shown=self.proxy.rowCount(), total=len(self.project.lines)))
        self._update_checked()

    def _selected_lines(self) -> list[pj.Line]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.project.lines[r] for r in sorted(rows)]

    # --- acciones ---

    def _mark_reviewed(self) -> None:
        chosen = [line for line in self.project.lines if line.selected] or self._selected_lines()
        lines = [line for line in chosen if line.translation.strip()]
        if not lines:
            QMessageBox.information(self, "Revoz", tr("Elegí líneas que ya tengan traducción."))
            return
        for line in lines:
            line.status = pj.REVIEWED
        self.model.reset()
        self._apply_filter()
        self._changed()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar CSV"), f"{self.project.game}.csv", "CSV (*.csv)")
        if not path:
            return
        visible = [self.project.lines[self.proxy.mapToSource(self.proxy.index(r, 0)).row()] for r in range(self.proxy.rowCount())]
        try:
            pj.export_csv(Path(path), visible)
        except OSError as e:
            QMessageBox.warning(self, "Revoz", tr("No se pudo exportar:") + f"\n\n{e}")
            return
        QMessageBox.information(self, "Revoz", tr("Se exportaron {n} líneas.", n=len(visible)))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Importar CSV"), "", "CSV (*.csv)")
        if not path:
            return
        try:
            changed = pj.import_csv(Path(path), self.project.lines)
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.warning(self, "Revoz", tr("No se pudo importar:") + f"\n\n{e}")
            return
        self.model.reset()
        self._apply_filter()
        self._changed()
        QMessageBox.information(self, "Revoz", tr("Se actualizaron {n} líneas.", n=changed))

    # --- elegir líneas y escuchar ---

    def _visible_rows(self) -> list[int]:
        return [self.proxy.mapToSource(self.proxy.index(r, 0)).row() for r in range(self.proxy.rowCount())]

    def _toggle_all(self) -> None:
        """Si todas las que se ven están elegidas, las quita; si falta alguna, las elige todas."""
        rows = self._visible_rows()
        all_checked = bool(rows) and all(self.project.lines[r].selected for r in rows)
        self.model.set_checked(rows, not all_checked)
        self.save_timer.start()

    def _update_checked(self) -> None:
        rows = self._visible_rows()
        total = sum(line.selected for line in self.project.lines)
        all_checked = bool(rows) and all(self.project.lines[r].selected for r in rows)
        self.select_all.setText(tr("Quitar selección") if all_checked else tr("Seleccionar todo"))
        self.checked_label.setText(tr("{n} elegidas", n=total) if total else tr("Ninguna elegida: se usan las que muestra la tabla"))

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Revoz", message)

    def _on_cell_clicked(self, index) -> None:
        if index.column() == PLAY:
            self._play_row(self.proxy.mapToSource(index).row())
        elif index.column() == VOICE:
            self._play_dub(self.proxy.mapToSource(index).row())

    def _play_dub(self, row: int) -> None:
        line = self.project.lines[row]
        if not line.dub_path or not Path(line.dub_path).is_file():
            return
        old, self.model.playing_dub = self.model.playing_dub, row
        for r in (old, row):
            if r >= 0:
                self.model.dataChanged.emit(self.model.index(r, VOICE), self.model.index(r, VOICE))
        self.player.load_file(Path(line.dub_path), tr("Voz traducida") + f": {line.id}")

    def _on_cell_double_clicked(self, index) -> None:
        if index.column() == FILE:
            self._play_row(self.proxy.mapToSource(index).row())

    def _play_row(self, row: int) -> None:
        old, self.model.playing = self.model.playing, row
        for r in (old, row):
            if r >= 0:
                self.model.dataChanged.emit(self.model.index(r, PLAY), self.model.index(r, PLAY))
        self.player.load(self.project.lines[row])
        self.table.selectRow(self.proxy.mapFromSource(self.model.index(row, 0)).row())

    def _step(self, delta: int) -> None:
        """Anterior / siguiente según el orden en que se ve la tabla."""
        rows = self._visible_rows()
        if not rows:
            return
        i = rows.index(self.model.playing) + delta if self.model.playing in rows else 0
        self._play_row(rows[max(0, min(len(rows) - 1, i))])

    def _confirm_clip(self, line: pj.Line, start, end) -> bool:
        """Cambiar el tramo borra el texto de la línea: si tiene, se pregunta antes."""
        if not (line.original or line.translation) or (start, end) == (line.clip_start, line.clip_end):
            return True
        answer = QMessageBox.question(
            self, "Revoz", tr("Esta línea ya tiene texto. Si cambiás el tramo, se borra y se vuelve a transcribir. ¿Seguir?")
        )
        return answer == QMessageBox.Yes

    def _clip_from_wave(self, start, end) -> None:
        row = self.model.playing
        if row < 0:
            return
        line = self.project.lines[row]
        if not self._confirm_clip(line, start, end):
            return
        try:
            pj.set_clip(line, start, end)
        except ValueError as e:
            self._show_error(tr(str(e)))
            return
        self.player.set_clip(start, end)
        self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, len(COLUMNS) - 1))
        self._changed()

    def _chosen_rows(self) -> list[int] | None:
        """Las líneas elegidas con la casilla (primero en el orden de la tabla). None si no hay ninguna elegida."""
        visible = self._visible_rows()
        chosen = [r for r in visible if self.project.lines[r].selected]
        seen = set(chosen)
        chosen += [i for i, line in enumerate(self.project.lines) if line.selected and i not in seen]
        return chosen or None

    # --- traducción automática ---

    def _start_auto(self) -> None:
        provider = self.provider
        key = ""
        if provider and provider.needs_key:
            key = (self.get_key(provider.name) if self.get_key else "") or ""
            if not key and provider.can_translate:
                QMessageBox.information(
                    self, "Revoz", tr("Guardá la API key de {name} en la página del juego antes de traducir.", name=provider.name)
                )
                return
        dialog = AutoDialog(self, self.project, provider, key, self.per_mchar.value(), can_voice=bool(self.tts))
        if dialog.exec() != AutoDialog.Accepted:
            return
        opts = dialog.options()
        self._export_audio_to = dialog.export_audio_dir()
        opts.tts, opts.dub_dir = self.tts or {}, self.dub_dir
        opts.tts_key = (self.get_key("ElevenLabs") if self.get_key else "") or ""
        # Las elegidas con la casilla; si no hay ninguna, las que muestra el filtro, en el orden de la tabla
        opts.only = self._chosen_rows() or self._visible_rows()
        self._last_rows = list(opts.only)
        self._save()
        self.engine = self.make_engine(self.project)
        self.engine.on_progress = lambda *a: self._emit(self.job_bus.progress, *a)
        self.engine.on_save = lambda: pj.save(self.project_file, self.project)
        self._set_running(True)
        engine, bus = self.engine, self.job_bus

        def work() -> None:
            try:
                result = engine.run(opts)
            except Exception as e:  # cualquier falla inesperada se muestra, no cierra la app
                result = e
            self._emit(bus.done, result)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:  # la pantalla se cerró mientras trabajaba
            pass

    def _set_running(self, running: bool) -> None:
        self.auto.setEnabled(not running)
        for btn in (self.install_btn, self.restore_btn):  # no instalar mientras se generan las voces
            btn.setEnabled(not running)
        if not running:
            self._update_install_state()
        self.pause.setVisible(running)
        self.pause.setEnabled(True)
        self.job_bar.setVisible(running)
        # mientras trabaja no se edita la tabla, para no pisar lo que escribe el motor
        triggers = QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers if running else triggers)
        if running:
            self.job_bar.setRange(0, 0)
            self.job_label.setText(tr("Preparando…"))
            self.stats_timer.start()
        else:
            self.stats_timer.stop()

    def _on_job_progress(self, done: int, total: int, message: str, row: int) -> None:
        if total >= 0:
            self.job_bar.setRange(0, max(1, total))
            self.job_bar.setValue(done)
            self.job_bar.setFormat(tr("%v de {total} pasos", total=total))
        if message:
            self.job_label.setText(tr(message) if total < 0 else message)
        if row >= 0:
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, len(COLUMNS) - 1))

    def _on_job_done(self, result) -> None:
        self._set_running(False)
        self.engine = None
        self.model.reset()
        self._apply_filter()
        self._refresh_stats()
        if isinstance(result, Exception):
            self.job_label.setText("")
            QMessageBox.warning(self, "Revoz", tr("La traducción automática se detuvo por un error:") + f"\n\n{type(result).__name__}: {result}")
            return
        s = result
        lines = [
            tr("Transcritas: {n}", n=s.transcribed),
            tr("Traducidas: {n}", n=s.translated),
            tr("Sin voz: {n}", n=s.no_voice),
            tr("Dudosas (revisalas): {n}", n=s.doubtful),
            tr("Desde la caché (gratis): {n}", n=s.from_cache),
            tr("Con voz: {n}", n=s.voiced),
            tr("No entran en el tiempo del original: {n}", n=s.too_long),
            tr("Con error: {n}", n=s.errors),
            tr("Gastado en esta pasada: US$ {spent}", spent=f"{s.spent:,.4f}"),
        ]
        if s.stopped == "pausa":
            head = tr("Pausado. Tocá de nuevo el botón para seguir donde quedó.")
        elif s.stopped == "límite":
            head = tr("Se frenó para no pasar el límite de gasto del proyecto.")
        elif s.stopped:
            head = tr("Se detuvo: {reason}", reason=tr(s.stopped))
        else:
            head = tr("Listo.")
        self.job_label.setText(head)
        text = head + "\n\n" + "\n".join(lines + s.messages)
        audio_dir = self._export_audio_result()
        if audio_dir:
            text += "\n\n" + tr("Audio traducido copiado en:") + f"\n{audio_dir}"
        elif getattr(self, "_export_audio_to", None):
            text += "\n\n" + tr("No se guardó audio: ninguna línea tiene voz generada. Marcá \"Generar la voz traducida\".")
        folder = audio_dir  # la carpeta que abre el botón
        box = QMessageBox(QMessageBox.Information, "Revoz", text, parent=self)
        open_btn = box.addButton(tr("Abrir carpeta"), QMessageBox.ActionRole) if folder else None
        box.addButton(QMessageBox.Ok)
        box.exec()
        if folder and box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _export_audio_result(self) -> Path | None:
        folder = getattr(self, "_export_audio_to", None)
        if not folder:
            return None
        lines = [self.project.lines[i] for i in getattr(self, "_last_rows", [])]
        try:
            return folder if pj.export_audio(folder, lines) else None
        except OSError as e:
            QMessageBox.warning(self, "Revoz", tr("No se pudo exportar:") + f"\n\n{e}")
            return None

    def _pause(self) -> None:
        if self.engine:
            self.engine.stop.set()
            self.pause.setEnabled(False)
            self.job_label.setText(tr("Pausando… termina la línea que está haciendo."))

    # --- instalar en el juego ---

    def _update_install_state(self, extra: str = "") -> None:
        can = bool(self.game_path and self.backup_dir)
        n = install.installed(self.backup_dir) if can else 0
        self.install_btn.setEnabled(can and self.engine is None)
        self.restore_btn.setEnabled(can and n > 0 and self.engine is None)
        text = tr("En el juego: {n} audios traducidos instalados.", n=n) if n else tr("En el juego: nada instalado (está el original).")
        self.install_state.setText(text + (f" {extra}" if extra else ""))

    def _check_game_changed(self) -> None:
        """En segundo plano: ¿una actualización del juego pisó audios que había instalado Revoz?"""
        if not (self.game_path and self.backup_dir) or not install.installed(self.backup_dir):
            return
        game, backups, bus = self.game_path, self.backup_dir, self.job_bus
        threading.Thread(target=lambda: self._emit(bus.game_changed, install.changed(game, backups)), daemon=True).start()

    def _on_game_changed(self, changed: list) -> None:
        if changed:
            self._update_install_state(tr("El juego se actualizó y {n} volvieron al original: tocá Instalar para ponerlos de nuevo.", n=len(changed)))

    def _install(self) -> None:
        lines = [line for line in self.project.lines if line.dub_path and Path(line.dub_path).is_file()]
        if not lines:
            QMessageBox.information(self, "Revoz", tr("Todavía no hay voz traducida. Usá \"Traducir automáticamente\" con \"Generar la voz traducida\" marcado."))
            return
        answer = QMessageBox.question(
            self, "Revoz",
            tr("Se van a reemplazar {n} audios del juego por la voz traducida.", n=len(lines)) + "\n\n"
            + tr("Antes se guarda una copia de cada original en:") + f"\n{self.backup_dir}\n\n"
            + tr("Si el juego tiene modo online con anticheat, usá la traducción solo para jugar offline.") + "\n\n"
            + tr("Cerrá el juego antes de seguir. ¿Instalar?"),
        )  # fmt: skip
        if answer != QMessageBox.Yes:
            return
        targets = valve.targets(self.game_path)  # audios extraídos de paquetes: dónde se instala cada uno
        self._run_install("install", lambda progress: install.install(lines, self.game_path, self.backup_dir, progress, targets))

    def _restore(self) -> None:
        n = install.installed(self.backup_dir)
        answer = QMessageBox.question(self, "Revoz", tr("Se van a volver a poner los {n} audios originales del juego. ¿Seguir?", n=n))
        if answer == QMessageBox.Yes:
            self._run_install("restore", lambda progress: install.restore(self.game_path, self.backup_dir))

    def _run_install(self, what: str, job) -> None:
        self.install_btn.setEnabled(False)
        self.restore_btn.setEnabled(False)
        self.auto.setEnabled(False)
        self.job_bar.show()
        self.job_bar.setRange(0, 0)
        self.job_label.setText(tr("Instalando en el juego…") if what == "install" else tr("Restaurando los originales…"))
        bus = self.job_bus

        def progress(done: int, total: int) -> None:
            self._emit(bus.progress, done, total, "", -1)

        def work() -> None:
            try:
                result = job(progress)
            except Exception as e:  # sin permiso, disco lleno, juego abierto…: se muestra, no cierra la app
                result = e
            self._emit(bus.installed, what, result)

        threading.Thread(target=work, daemon=True).start()

    def _on_installed(self, what: str, result) -> None:
        self.job_bar.hide()
        self.job_label.clear()
        self.auto.setEnabled(self.make_engine is not None)
        self._update_install_state()
        if isinstance(result, Exception):
            if isinstance(result, PermissionError):
                detail = tr("Windows no dejó escribir en la carpeta del juego. Cerrá el juego y probá de nuevo.")
            else:
                detail = f"{type(result).__name__}: {result}"
            QMessageBox.warning(self, "Revoz", tr("No se pudo terminar. Lo que se alcanzó a cambiar volvió al original.") + f"\n\n{detail}")
            return
        if what == "install":
            text = tr("Listo: {n} audios traducidos instalados en el juego.", n=result.done)
        else:
            text = tr("Listo: {n} audios originales restaurados.", n=result.done)
        if result.changed:
            text += "\n\n" + tr("{n} no se tocaron porque el juego los actualizó: ya tienen el audio nuevo del juego.", n=len(result.changed))
        if result.skipped:
            reasons = sorted({tr(reason) for _, reason in result.skipped})
            text += "\n\n" + tr("Sin instalar: {n}", n=len(result.skipped)) + "\n" + "\n".join(f"• {r}" for r in reasons[:5])
        QMessageBox.information(self, "Revoz", text)

    def stop(self) -> None:
        """Frena el motor y el audio (al salir de la pantalla o cerrar la app)."""
        if self.engine:
            self.engine.stop.set()
        self.player.stop()

    def _open_voices(self) -> None:
        if VoicesDialog(self, self.project, self.tts or {}, self.get_key or (lambda n: None)).exec() == VoicesDialog.Accepted:
            self._save()

    def _open_glossary(self) -> None:
        if GlossaryDialog(self, self.project).exec() == GlossaryDialog.Accepted:
            self._save()
