"""Ventanas de la traducción automática: opciones de la pasada y glosario/personajes."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from revoz.core import project as pj
from revoz.core import transcribe
from revoz.core.engine import Options
from revoz.core.providers import Provider, ProviderError
from revoz.ui.i18n import tr


def default_export_path(project: pj.Project) -> Path:
    """Documentos\\Revoz\\<juego> <fecha>"""
    safe = "".join(c for c in project.game if c not in '\\/:*?"<>|').strip() or "Revoz"
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home() / "Documents")
    return Path(docs) / "Revoz" / f"{safe} {datetime.now():%Y-%m-%d %H%M}"


class _ModelsBus(QObject):
    loaded = Signal(list, str)  # modelos, error


class AutoDialog(QDialog):
    """Opciones antes de lanzar la traducción automática."""

    def __init__(self, parent, project: pj.Project, provider: Provider | None, key: str, usd_per_mchar: float, can_voice: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Traducción automática"))
        self.setMinimumWidth(560)
        self.project, self.provider, self.key, self.price = project, provider, key, usd_per_mchar

        # --- transcribir ---
        can_listen = transcribe.available()
        self.do_transcribe = QCheckBox(tr("Transcribir las voces (Whisper, en tu PC y gratis)"))
        self.do_transcribe.setChecked(can_listen)
        self.do_transcribe.setEnabled(can_listen)
        self.whisper = QComboBox()
        for name, mb in transcribe.MODELS.items():
            self.whisper.addItem(tr("{name} (~{mb} MB)", name=name, mb=mb), name)
        self.whisper.setCurrentIndex(max(0, self.whisper.findData(project.whisper_model)))
        self.whisper.setToolTip(tr("Más grande: entiende mejor, pero tarda más. La primera vez se descarga."))
        self.do_transcribe.toggled.connect(self.whisper.setEnabled)

        # --- traducir ---
        can_translate = bool(provider and provider.can_translate)
        name = provider.name if provider else "?"
        self.do_translate = QCheckBox(tr("Traducir el texto con {name}", name=name))
        self.do_translate.setChecked(can_translate)
        self.do_translate.setEnabled(can_translate)
        self.model = QComboBox()
        self.model.setEditable(True)  # se puede escribir un modelo aunque no aparezca en la lista
        self.model.setMinimumWidth(260)
        self.model_state = QLabel()
        self.model_state.setObjectName("hint")
        if project.model:
            self.model.addItem(project.model)
        self.needs_model = can_translate and callable(getattr(provider, "complete", None))  # modelos de lenguaje
        if self.needs_model:
            self._load_models()
        else:
            self.model.setEnabled(False)
        self.do_translate.toggled.connect(lambda on: self.model.setEnabled(on and self.needs_model))

        what = QGroupBox(tr("Qué hacer"))
        what_form = QFormLayout(what)
        what_form.addRow(self.do_transcribe)
        what_form.addRow(tr("Modelo de voz:"), self.whisper)
        what_form.addRow(self.do_translate)
        what_form.addRow(tr("Modelo de la IA:"), self.model)
        what_form.addRow("", self.model_state)
        self.do_voice = QCheckBox(tr("Generar la voz traducida"))
        self.do_voice.setEnabled(can_voice)
        voice_name = project.default_voice.partition(":")[2] or tr("automática (una voz de Windows en tu idioma)")
        self.do_voice.setChecked(can_voice)
        what_form.addRow(self.do_voice)
        what_form.addRow(tr("Voz por defecto:"), QLabel(voice_name))
        if not can_listen:
            what_form.addRow(QLabel(tr("La transcripción no está instalada en esta copia de Revoz.")))
        if not can_translate:
            what_form.addRow(QLabel(tr("{name} no traduce texto. Elegí otra IA en la página del juego.", name=name)))

        # --- cuántas líneas ---
        self.test_mode = QRadioButton(tr("Prueba: solo las primeras"))
        self.test_lines = QSpinBox(minimum=1, maximum=1000, value=20)
        self.all_mode = QRadioButton(tr("Todas las que faltan"))
        self.test_mode.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.test_mode)
        group.addButton(self.all_mode)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_mode)
        test_row.addWidget(self.test_lines)
        test_row.addWidget(QLabel(tr("líneas")))
        test_row.addStretch()
        self.skip_noise = QCheckBox(tr("No traducir los audios sin voz (efectos, música)"))
        self.skip_noise.setChecked(True)
        how_many = QGroupBox(tr("Cuántas líneas"))
        how_lay = QVBoxLayout(how_many)
        how_lay.addLayout(test_row)
        how_lay.addWidget(self.all_mode)
        how_lay.addWidget(self.skip_noise)
        hint = QLabel(tr("Probá primero con pocas líneas: ves la calidad y el costo real antes de hacer todo."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        how_lay.addWidget(hint)

        # --- gasto ---
        self.limit = QDoubleSpinBox(decimals=2, maximum=100000, prefix="US$ ", value=project.limit)
        self.limit.setSpecialValueText(tr("Sin límite"))
        money = QGroupBox(tr("Gasto"))
        money_form = QFormLayout(money)
        money_form.addRow(tr("Límite para este proyecto:"), self.limit)
        money_form.addRow(tr("Gastado hasta ahora:"), QLabel(f"US$ {project.spent:,.4f}"))
        if not usd_per_mchar and provider and provider.needs_key:
            warn = QLabel(tr("No cargaste el precio por millón de caracteres: el gasto no se va a poder controlar."))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #d29922;")
            money_form.addRow(warn)

        # --- exportar al terminar ---
        out = QGroupBox(tr("Resultado"))
        out_lay = QVBoxLayout(out)
        self.do_export_audio = QCheckBox(tr("Guardar el audio traducido (WAV) en la carpeta"))
        self.do_export_audio.setChecked(can_voice)
        self.audio_dir = QLineEdit(project.export_audio_dir or str(default_export_path(project)))
        choose_dir = QPushButton(tr("Elegir…"))
        choose_dir.clicked.connect(self._choose_audio_dir)
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.audio_dir, 1)
        audio_row.addWidget(choose_dir)
        for w in (self.audio_dir, choose_dir):
            w.setEnabled(self.do_export_audio.isChecked())
            self.do_export_audio.toggled.connect(w.setEnabled)
        out_lay.addWidget(self.do_export_audio)
        out_lay.addLayout(audio_row)
        where = QLabel(tr("Los audios se guardan con las mismas carpetas que en el juego. El texto queda en la tabla de esta pantalla."))
        where.setObjectName("hint")
        where.setWordWrap(True)
        out_lay.addWidget(where)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("Empezar"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancelar"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        for w in (what, how_many, money, out):
            lay.addWidget(w)
        lay.addWidget(buttons)

    def _load_models(self) -> None:
        self.model_state.setText(tr("Buscando los modelos disponibles…"))
        self.bus = _ModelsBus()
        self.bus.loaded.connect(self._on_models)
        provider, key, bus = self.provider, self.key, self.bus

        def work() -> None:
            try:
                models, error = provider.models(key), ""
            except ProviderError as e:
                models, error = [], str(e)
            except Exception:
                models, error = [], "?"
            try:
                bus.loaded.emit(models, error)
            except RuntimeError:  # la ventana se cerró
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_models(self, models: list, error: str) -> None:
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        chosen = current or self.provider.default_model(models)
        if chosen:
            self.model.setCurrentText(chosen)
        if error:
            self.model_state.setText(tr("No se pudieron listar los modelos: {error}", error=tr(error)))
        else:
            self.model_state.setText(tr("Modelos disponibles: {n}. Se eligió uno barato por defecto.", n=len(models)))

    def _choose_audio_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta para el audio traducido"), self.audio_dir.text())
        if folder:
            self.audio_dir.setText(folder)

    def export_audio_dir(self) -> Path | None:
        if not self.do_export_audio.isChecked() or not self.audio_dir.text().strip():
            return None
        return Path(self.audio_dir.text().strip())

    def _accept(self) -> None:
        if not (self.do_transcribe.isChecked() or self.do_translate.isChecked() or self.do_voice.isChecked()):
            return
        if self.do_translate.isChecked() and self.needs_model and not self.model.currentText().strip():
            self.model_state.setText(tr("Elegí o escribí un modelo."))
            return
        self.accept()

    def options(self) -> Options:
        self.project.whisper_model = self.whisper.currentData()
        self.project.model = self.model.currentText().strip()
        self.project.limit = self.limit.value()
        audio_dir = self.export_audio_dir()
        self.project.export_audio_dir = str(audio_dir) if audio_dir else ""
        return Options(
            transcribe=self.do_transcribe.isChecked(),
            translate=self.do_translate.isChecked(),
            max_lines=self.test_lines.value() if self.test_mode.isChecked() else 0,
            skip_no_voice=self.skip_noise.isChecked(),
            whisper_model=self.project.whisper_model,
            provider=self.provider,
            key=self.key,
            model=self.project.model,
            target=self.project.target,
            usd_per_million_chars=self.price,
            limit=self.project.limit,
            voice=self.do_voice.isChecked(),
        )


def _table(headers: list[str], rows: list[tuple[str, str]]) -> QTableWidget:
    table = QTableWidget(len(rows) + 1, 2)  # siempre una fila vacía al final para agregar
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.verticalHeader().hide()
    for r, (a, b) in enumerate(rows):
        table.setItem(r, 0, QTableWidgetItem(a))
        table.setItem(r, 1, QTableWidgetItem(b))

    def grow(item) -> None:  # al escribir en la última fila, aparece otra vacía
        if item.row() == table.rowCount() - 1 and item.text().strip():
            table.setRowCount(table.rowCount() + 1)

    table.itemChanged.connect(grow)
    return table


def _rows(table: QTableWidget) -> dict[str, str]:
    out = {}
    for r in range(table.rowCount()):
        a, b = table.item(r, 0), table.item(r, 1)
        key = a.text().strip() if a else ""
        if key:
            out[key] = b.text().strip() if b else ""
    return out


class GlossaryDialog(QDialog):
    """Glosario, descripción de los personajes y contexto del juego, para que la IA traduzca mejor."""

    def __init__(self, parent, project: pj.Project) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Glosario y personajes"))
        self.resize(720, 640)
        self.project = project

        self.context = QPlainTextEdit(project.context)
        self.context.setPlaceholderText(tr("Ej.: shooter de ciencia ficción de 1998, tono serio con humor de laboratorio."))
        self.context.setMaximumHeight(70)

        self.glossary = _table([tr("Término"), tr("Traducción (vacío = no traducir)")], sorted(project.glossary.items()))
        found = sorted({line.character for line in project.lines if line.character})
        chars = [(c, project.characters.get(c, "")) for c in found]
        chars += [(c, d) for c, d in sorted(project.characters.items()) if c not in found]
        self.characters = _table([tr("Personaje"), tr("Cómo es (edad, forma de hablar…)")], chars)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr("Guardar"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancelar"))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        def hint(text: str) -> QLabel:
            label = QLabel(text)
            label.setObjectName("hint")
            label.setWordWrap(True)
            return label

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>" + tr("De qué trata el juego") + "</b>"))
        lay.addWidget(self.context)
        lay.addWidget(QLabel("<b>" + tr("Glosario") + "</b>"))
        lay.addWidget(hint(tr("Nombres y términos con traducción fija. Dejá la traducción vacía para que no se traduzcan (ej. Gordon, Black Mesa).")))
        lay.addWidget(self.glossary, 1)
        lay.addWidget(QLabel("<b>" + tr("Personajes") + "</b>"))
        lay.addWidget(hint(tr("Revoz los detecta por las carpetas del juego. Describirlos ayuda a que cada uno hable como corresponde.")))
        lay.addWidget(self.characters, 1)
        lay.addWidget(buttons)

    def _save(self) -> None:
        self.project.context = self.context.toPlainText().strip()
        self.project.glossary = _rows(self.glossary)
        self.project.characters = {k: v for k, v in _rows(self.characters).items() if v}
        self.accept()
