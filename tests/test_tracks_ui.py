"""Pantalla de pistas: elegir líneas con la casilla, Seleccionar todo, Desde/Hasta."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from revoz.core import project as pj
from revoz.ui import translation_page as tp

app = QApplication.instance() or QApplication([])


@pytest.fixture
def page(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    lines = [pj.Line(f"sound/{who}/{n}.wav", duration=5.0) for n, who in enumerate(["barney", "barney", "barney", "scientist"])]
    project = pj.Project("1", "Juego", lines=lines)
    return tp.TranslationPage(project, tmp_path / "p.json", {}, lambda: None, lambda: None)


def _selected(page):
    return [line.selected for line in page.project.lines]


def test_select_all_toggles_like_described(page):
    page._toggle_all()  # nada elegido -> elige todas
    assert _selected(page) == [True, True, True, True]
    assert page.select_all.text() == "Quitar selección"
    page._toggle_all()  # todas elegidas -> las quita
    assert _selected(page) == [False] * 4
    page._toggle_all()
    page.model.setData(page.model.index(1, tp.CHECK), Qt.Unchecked.value, Qt.CheckStateRole)  # quito una a mano
    assert page.select_all.text() == "Seleccionar todo"
    page._toggle_all()  # faltaba una -> vuelve a elegir todas
    assert _selected(page) == [True] * 4


def test_select_all_works_on_what_the_filter_shows(page):
    page.search.setText("barney")
    page._toggle_all()
    assert _selected(page) == [True, True, True, False]
    assert page._chosen_rows() == [0, 1, 2]


def test_chosen_rows_fall_back_to_visible(page):
    assert page._chosen_rows() is None  # sin casillas: la traducción usa lo que muestra la tabla


def test_typing_from_to(page):
    model = page.model
    assert model.setData(model.index(0, tp.FROM), "1:00.5"[2:], Qt.EditRole)  # "00.5"
    assert model.setData(model.index(0, tp.TO), "2.5", Qt.EditRole)
    line = page.project.lines[0]
    assert (line.clip_start, line.clip_end) == (0.5, 2.5)
    assert model.data(model.index(0, tp.FROM)) == "0:00.5"
    assert not model.setData(model.index(0, tp.TO), "0.2", Qt.EditRole)  # Hasta antes que Desde: no se acepta
    assert not model.setData(model.index(0, tp.FROM), "abc", Qt.EditRole)
    assert model.setData(model.index(0, tp.TO), "", Qt.EditRole)  # vacío = hasta el final
    assert line.clip_end is None


def test_changing_from_to_asks_before_erasing_text(page, monkeypatch):
    line = page.project.lines[0]
    line.original, line.translation = "Okay, let's go", "Okay, vamos"
    answers = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: answers.pop(0)))
    model = page.model
    answers.append(QMessageBox.No)
    assert not model.setData(model.index(0, tp.FROM), "0.7", Qt.EditRole)
    assert (line.clip_start, line.translation) == (None, "Okay, vamos")  # dijo que no: no se borra nada
    answers.append(QMessageBox.Yes)
    assert model.setData(model.index(0, tp.FROM), "0.7", Qt.EditRole)
    assert (line.clip_start, line.translation) == (0.7, "")
