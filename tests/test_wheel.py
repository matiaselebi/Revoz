import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QScrollArea, QVBoxLayout, QWidget

from revoz.ui import wheel

app = QApplication.instance() or QApplication([])
wheel.install(app)


def _scroll_down(widget) -> None:
    event = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False
    )
    QApplication.sendEvent(widget, event)


def test_wheel_scrolls_page_instead_of_changing_options():
    content = QWidget()
    lay = QVBoxLayout(content)
    combo = QComboBox()
    combo.addItems(["Revoz", "Oscuro", "Claro"])
    spin = QDoubleSpinBox(value=1.0)
    lay.addWidget(combo)
    lay.addWidget(spin)
    lay.addSpacing(3000)  # página larga para que haya scroll
    area = QScrollArea()
    area.setWidget(content)
    area.resize(300, 200)
    area.show()

    _scroll_down(combo)
    _scroll_down(spin)

    assert combo.currentIndex() == 0
    assert spin.value() == 1.0
    assert area.verticalScrollBar().value() > 0  # la página sí bajó
