"""Evita cambiar opciones sin querer con la rueda del mouse.

Por defecto Qt cambia el valor de un desplegable o de un campo numérico si la rueda pasa por encima,
aunque la persona solo quería bajar por la página. Este filtro manda ese giro a la página que lo contiene.
Para cambiar una opción hay que hacer clic y elegirla (la lista abierta sí se puede recorrer con la rueda).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox


class WheelGuard(QObject):
    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox)):
            parent = obj.parentWidget()
            while parent is not None and not isinstance(parent, QAbstractScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                QApplication.sendEvent(parent.viewport(), event)  # scrollea la página
            return True  # el desplegable no cambia
        return False


def install(app: QApplication) -> None:
    guard = WheelGuard(app)
    app.installEventFilter(guard)
