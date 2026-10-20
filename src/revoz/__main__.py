import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from revoz.ui import wheel
from revoz.ui.main_window import RESTART_FLAG, MainWindow

ICON = Path(__file__).parent / "assets" / "revoz.png"


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Revoz")
    app.setWindowIcon(QIcon(str(ICON)))
    wheel.install(app)  # la rueda del mouse no cambia desplegables sin querer
    win = MainWindow(section=2 if RESTART_FLAG in sys.argv else 0)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
