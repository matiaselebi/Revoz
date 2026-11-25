"""Elegir la voz de cada personaje, probarla y cómo ajustar la duración."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QVBoxLayout,
)

from revoz.core import project as pj
from revoz.core import tts as tts_mod
from revoz.core.providers import ProviderError
from revoz.ui.auto_dialog import _rows, _table
from revoz.ui.i18n import tr

SAMPLE = "Hola, esta es mi voz. ¿Te gusta cómo suena?"


class _Bus(QObject):
    voices = Signal(list, str)  # voces, error
    tested = Signal(str, str)  # archivo, error
    progress = Signal(int, int)
    installed = Signal(str)  # error


def _label(v: tts_mod.Voice) -> str:
    gender = {"Male": tr("hombre"), "Female": tr("mujer"), "male": tr("hombre"), "female": tr("mujer")}.get(v.gender, "")
    extra = ", ".join(x for x in (v.language, gender) if x and x != "multi")
    text = f"{v.provider} · {v.name}" + (f" ({extra})" if extra else "")
    if v.download_mb:
        text += " · " + tr("se descarga ~{mb} MB", mb=v.download_mb)
    return text


class VoicesDialog(QDialog):
    def __init__(self, parent, project: pj.Project, providers: dict, get_key) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Voces"))
        self.resize(760, 760)
        self.project, self.providers, self.get_key = project, providers, get_key
        self.voices: list[tts_mod.Voice] = []
        self.bus = _Bus()
        self.bus.voices.connect(self._on_voices)
        self.bus.tested.connect(self._on_tested)
        self._player = None

        self.state = QLabel(tr("Buscando voces…"))
        self.state.setObjectName("hint")
        self.default = QComboBox()
        self.default.setMinimumWidth(420)
        default_row = QHBoxLayout()
        default_row.addWidget(QLabel(tr("Voz por defecto:")))
        default_row.addWidget(self.default, 1)
        test_default = QPushButton(tr("Probar"))
        test_default.clicked.connect(lambda: self._test(self.default))
        default_row.addWidget(test_default)

        found = sorted({line.character for line in project.lines if line.character})
        self.table = QTableWidget(len(found), 3)
        self.table.setHorizontalHeaderLabels([tr("Personaje"), tr("Voz"), ""])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.combos: dict[str, QComboBox] = {}
        for r, name in enumerate(found):
            self.table.setCellWidget(r, 0, QLabel(" " + name))
            combo = QComboBox()
            self.combos[name] = combo
            self.table.setCellWidget(r, 1, combo)
            btn = QPushButton(tr("Probar"))
            btn.clicked.connect(lambda _=False, c=combo, n=name: self._test(c, n))
            self.table.setCellWidget(r, 2, btn)

        self.fit = QRadioButton(tr("Acelerar si hace falta para que entre en el tiempo del original (recomendado)"))
        self.natural = QRadioButton(tr("Natural: no acelerar, aunque quede más larga"))
        group = QButtonGroup(self)
        group.addButton(self.fit)
        group.addButton(self.natural)
        (self.natural if project.natural_length else self.fit).setChecked(True)
        length = QGroupBox(tr("Duración"))
        length_lay = QVBoxLayout(length)
        length_lay.addWidget(self.fit)
        length_lay.addWidget(self.natural)
        note = QLabel(tr("Acelera sin cambiar el tono, hasta 1.35x. Si ni así entra, la línea queda marcada para acortar la traducción."))
        note.setObjectName("hint")
        note.setWordWrap(True)
        length_lay.addWidget(note)
        self.spread = QPushButton(tr("Repartir voces"))
        self.spread.setToolTip(tr("Le da a cada personaje una voz distinta, alternando hombre y mujer. Después ajustalas a mano."))
        self.spread.clicked.connect(self._spread)

        # --- pronunciación ---
        self.pronunciations = _table([tr("Palabra"), tr("Cómo se dice")], sorted(project.pronunciations.items()))
        self.pronunciations.setMaximumHeight(150)
        pron_box = QGroupBox(tr("Pronunciación"))
        pron_lay = QVBoxLayout(pron_box)
        pron_hint = QLabel(tr("Para nombres o palabras que la voz dice mal. Ej.: Gordon → Górdon. El texto traducido no cambia, solo cómo suena."))
        pron_hint.setObjectName("hint")
        pron_hint.setWordWrap(True)
        pron_lay.addWidget(pron_hint)
        pron_lay.addWidget(self.pronunciations)

        legal = QLabel(tr("Revoz usa voces genéricas. No clones la voz de actores reales sin su permiso."))
        legal.setObjectName("hint")
        legal.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr("Guardar"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancelar"))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(default_row)
        lay.addWidget(self.state)
        chars_title = QHBoxLayout()
        chars_title.addWidget(QLabel("<b>" + tr("Voz de cada personaje (vacío = la voz por defecto)") + "</b>"), 1)
        chars_title.addWidget(self.spread)
        lay.addLayout(chars_title)
        lay.addWidget(self.table, 1)
        lay.addWidget(length)
        lay.addWidget(pron_box)
        lay.addWidget(legal)
        lay.addWidget(buttons)
        self._load()

    # --- voces disponibles ---

    def _load(self) -> None:
        providers, target, get_key, bus = self.providers, self.project.target, self.get_key, self.bus

        def work() -> None:
            found, errors = [], []
            for name, provider in providers.items():
                key = (get_key(name) or "") if provider.needs_key else ""
                if provider.needs_key and not key:
                    continue  # sin clave no se listan (ej. ElevenLabs)
                try:
                    found += [v for v in provider.voices(key) if v.language == "multi" or tts_mod.matches_language(v.language, target)]
                except (ProviderError, OSError, ValueError) as e:
                    errors.append(f"{name}: {e}")
            try:
                bus.voices.emit(found, "\n".join(errors))
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_voices(self, voices: list, error: str) -> None:
        # Primero las que no hay que descargar; dentro de eso, primero la variante del idioma pedido (ej. es-MX para es-419)
        regional = {"es-419": ("es-mx", "es-ar", "es-us"), "pt": ("pt-br",), "en": ("en-us",)}.get(self.project.target, ())
        self.voices = sorted(voices, key=lambda v: (v.download_mb > 0, v.language.lower() not in regional, v.provider, v.name))
        for combo in (self.default, *self.combos.values()):
            combo.clear()
            if combo is not self.default:
                combo.addItem(tr("(voz por defecto)"), "")
            for v in self.voices:
                combo.addItem(_label(v), v.key)
        self._select(self.default, self.project.default_voice or (self.voices[0].key if self.voices else ""))
        for name, combo in self.combos.items():
            self._select(combo, self.project.voices.get(name, ""))
        self.state.setText(tr("Voces disponibles: {n}", n=len(self.voices)) + (f"\n{error}" if error else ""))

    @staticmethod
    def _select(combo: QComboBox, key: str) -> None:
        combo.setCurrentIndex(max(0, combo.findData(key)))

    def _voice(self, key: str) -> tts_mod.Voice | None:
        return next((v for v in self.voices if v.key == key), None)

    # --- probar ---

    def _test(self, combo: QComboBox, character: str = "") -> None:
        key = combo.currentData() or self.default.currentData()
        voice = self._voice(key)
        if not voice:
            return
        if voice.download_mb and not self._prepare([voice]):
            return
        text = next((ln.translation for ln in self.project.lines if ln.character == character and ln.translation), SAMPLE)
        provider = self.providers[voice.provider]
        key_ = (self.get_key(voice.provider) or "") if provider.needs_key else ""
        out = Path(tempfile.gettempdir()) / f"revoz_prueba_{abs(hash((key, text)))}.wav"
        self.state.setText(tr("Generando la prueba…"))
        bus = self.bus

        def work() -> None:
            try:
                provider.synthesize([tts_mod.Job(text, voice.id, out)], key_)
                result = (str(out), "")
            except (ProviderError, OSError) as e:
                result = ("", str(e))
            try:
                bus.tested.emit(*result)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_tested(self, path: str, error: str) -> None:
        if error:
            self.state.setText(tr(error))
            return
        self.state.setText(tr("Voces disponibles: {n}", n=len(self.voices)))
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            return
        if self._player is None:
            self._out = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._out)
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    # --- descargar Piper y sus voces (con permiso) ---

    def _prepare(self, voices: list[tts_mod.Voice]) -> bool:
        piper = self.providers.get("Piper")
        to_get = [v for v in voices if v.provider == "Piper" and v.download_mb]
        if not to_get or piper is None:
            return True
        mb = sum(v.download_mb for v in to_get) + (0 if piper.installed() else 21)
        answer = QMessageBox.question(
            self, "Revoz",
            tr("Para usar estas voces hay que descargar ~{mb} MB (Piper desde github.com y las voces desde huggingface.co). ¿Descargar ahora?", mb=mb),
        )  # fmt: skip
        if answer != QMessageBox.Yes:
            return False
        dialog = QProgressDialog(tr("Descargando…"), tr("Cancelar"), 0, 100, self)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setCancelButton(None)
        dialog.show()
        bus = _Bus()
        bus.progress.connect(lambda done, total: dialog.setValue(int(done * 100 / total)) if total else None)
        result = {}
        bus.installed.connect(lambda err: (result.update(error=err), dialog.close()))

        def work() -> None:
            try:
                if not piper.installed():
                    piper.install(lambda d, t: bus.progress.emit(d, t))
                for v in to_get:
                    piper._ensure_voice(v.id, lambda d, t: bus.progress.emit(d, t))
                bus.installed.emit("")
            except (ProviderError, OSError) as e:
                bus.installed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()
        dialog.exec()
        if result.get("error"):
            QMessageBox.warning(self, "Revoz", tr(result["error"]))
            return False
        for v in to_get:
            v.download_mb = 0
        return True

    def _spread(self) -> None:
        """Una voz distinta para cada personaje, alternando hombre y mujer (solo voces que no hay que descargar)."""
        ready = [v for v in self.voices if not v.download_mb]
        men = [v for v in ready if v.gender.lower() == "male"]
        women = [v for v in ready if v.gender.lower() == "female"]
        others = [v for v in ready if v not in men and v not in women]
        order = [v for pair in zip(men, women) for v in pair] + men[len(women):] + women[len(men):] + others
        if not order:
            return
        for n, combo in enumerate(self.combos.values()):
            self._select(combo, order[n % len(order)].key)

    def _save(self) -> None:
        chosen = [self._voice(c.currentData()) for c in (self.default, *self.combos.values()) if c.currentData()]
        if not self._prepare([v for v in chosen if v]):
            return
        self.project.default_voice = self.default.currentData() or ""
        self.project.voices = {n: c.currentData() for n, c in self.combos.items() if c.currentData()}
        self.project.natural_length = self.natural.isChecked()
        self.project.pronunciations = {k: v for k, v in _rows(self.pronunciations).items() if v}
        self.accept()

