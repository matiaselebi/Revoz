"""Pantalla de traducción: progreso, costo estimado y revisión línea por línea."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl
from PySide6.QtGui import QColor
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
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from revoz.core import project as pj
from revoz.ui.i18n import N_, tr

COLUMNS = [N_("Archivo"), N_("Personaje"), N_("Duración"), N_("Texto original"), N_("Traducción"), N_("Estado")]
# Los estados se guardan en español (en el proyecto y en el CSV); se traducen solo al mostrarlos.
STATUS_NAMES = {pj.PENDING: N_("pendiente"), pj.TRANSCRIBED: N_("transcrita"), pj.TRANSLATED: N_("traducida"), pj.REVIEWED: N_("revisada")}
EDITABLE = {1: "character", 3: "original", 4: "translation", 5: "status"}
STATUS_COLOR = {pj.PENDING: "#6e7681", pj.TRANSCRIBED: "#d29922", pj.TRANSLATED: "#58a6ff", pj.REVIEWED: "#3fb950"}


class LinesModel(QAbstractTableModel):
    def __init__(self, lines: list[pj.Line], on_change) -> None:
        super().__init__()
        self.lines = lines
        self.on_change = on_change

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.lines)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return tr(COLUMNS[section])
        return None

    def data(self, index, role=Qt.DisplayRole):
        line = self.lines[index.row()]
        col = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0:
                return line.id
            if col == 2:
                return "" if line.duration is None else f"{line.duration:.1f} s"
            if col == 5 and role == Qt.DisplayRole:
                return tr(STATUS_NAMES.get(line.status, line.status))
            return getattr(line, EDITABLE[col])
        if role == Qt.ForegroundRole and col == 5:
            return QColor(STATUS_COLOR.get(line.status, "#e6e6e6"))
        if role == Qt.ToolTipRole and col in (0, 3, 4):
            return self.data(index)
        return None

    def flags(self, index):
        base = super().flags(index)
        return base | Qt.ItemIsEditable if index.column() in EDITABLE else base

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if role != Qt.EditRole or index.column() not in EDITABLE:
            return False
        line = self.lines[index.row()]
        attr = EDITABLE[index.column()]
        if attr == "status":
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
        if self.status and line.status != self.status:
            return False
        if not self.text:
            return True
        return any(self.text in s.lower() for s in (line.id, line.character, line.original, line.translation))


class TranslationPage(QWidget):
    def __init__(self, project: pj.Project, project_file: Path, prices: dict, on_prices_changed, on_back) -> None:
        super().__init__()
        self.project = project
        self.project_file = project_file
        self.prices = prices  # proveedor -> [USD por minuto, USD por millón de caracteres]
        self.on_prices_changed = on_prices_changed

        # Guardado con demora: muchas ediciones seguidas = una sola escritura a disco.
        self.save_timer = QTimer(self, singleShot=True, interval=800)
        self.save_timer.timeout.connect(self._save)

        back = QPushButton(tr("← Volver al juego"))
        back.clicked.connect(lambda: (self._save(), on_back()))
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
        auto = QPushButton(tr("Traducir automáticamente con IA"))
        auto.setEnabled(False)
        auto.setToolTip(
            tr("El motor de transcripción y traducción todavía no está implementado.") + "\n"
            + tr("Por ahora podés cargar y revisar el texto a mano o con CSV.")
        )
        prog_form.addRow(auto)
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
        self.model = LinesModel(project.lines, self._changed)
        self.proxy = LinesFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.setItemDelegateForColumn(5, StatusDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate((260, 110, 70, 300, 300, 90)):
            self.table.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        self.table.doubleClicked.connect(self._play_if_audio_column)

        self.search = QLineEdit(placeholderText=tr("Buscar por archivo, personaje o texto…"))
        self.status_filter = QComboBox()
        self.status_filter.addItem(tr("Todos los estados"), "")
        for s in pj.STATUSES:
            self.status_filter.addItem(tr(STATUS_NAMES[s]).capitalize(), s)
        self.search.textChanged.connect(self._apply_filter)
        self.status_filter.currentIndexChanged.connect(self._apply_filter)
        self.shown_label = QLabel()

        play = QPushButton(tr("▶ Escuchar"))
        play.setToolTip(tr("Reproduce el audio original de la línea seleccionada (también: doble clic en Archivo)"))
        play.clicked.connect(self._play_selected)
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
        for b in (play, mark, export, imp):
            actions.addWidget(b)
        actions.addStretch()

        review_lay = QVBoxLayout()
        review_lay.addLayout(filters)
        review_lay.addLayout(actions)
        review_lay.addWidget(self.table, 1)
        review_box = QGroupBox(tr("Revisión de líneas (doble clic en una celda para editarla)"))
        review_box.setLayout(review_lay)

        root = QVBoxLayout(self)
        root.addWidget(back, alignment=Qt.AlignLeft)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(top)
        root.addWidget(review_box, 1)

        self.player = None
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

    def _prices_changed(self) -> None:
        self.prices[self.project.provider] = [self.per_minute.value(), self.per_mchar.value()]
        self.on_prices_changed()
        self._refresh_stats()

    def _apply_filter(self) -> None:
        self.proxy.set_filter(self.search.text(), self.status_filter.currentData())
        self.shown_label.setText(tr("{shown} de {total}", shown=self.proxy.rowCount(), total=len(self.project.lines)))

    def _selected_lines(self) -> list[pj.Line]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.project.lines[r] for r in sorted(rows)]

    # --- acciones ---

    def _mark_reviewed(self) -> None:
        lines = [line for line in self._selected_lines() if line.translation.strip()]
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

    def _play_if_audio_column(self, index) -> None:
        if index.column() == 0:
            self._play_selected()

    def _play_selected(self) -> None:
        lines = self._selected_lines()
        if not lines:
            return
        line = lines[0]
        if not line.path or not Path(line.path).is_file():
            QMessageBox.information(self, "Revoz", tr("Este audio está dentro de un paquete del juego y no se puede escuchar directo."))
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            QMessageBox.warning(self, "Revoz", tr("La reproducción de audio no está disponible en esta instalación."))
            return
        if self.player is None:
            self.audio_out = QAudioOutput(self)
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(self.audio_out)
        self.player.setSource(QUrl.fromLocalFile(line.path))
        self.player.play()
