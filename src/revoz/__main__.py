import json
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from revoz.version import VERSION
from revoz.ui import wheel
from revoz.ui.main_window import RESTART_FLAG, MainWindow

ICON = Path(__file__).parent / "assets" / "revoz.png"


def selftest(out: Path, audio: str = "") -> None:
    """Revoz.exe --selftest archivo.json [audio]: comprueba que el .exe trae todo lo necesario (sin abrir la ventana)."""
    result = {}
    for name in ("faster_whisper", "ctranslate2", "av", "onnxruntime", "PySide6.QtMultimedia", "keyring"):
        try:
            __import__(name)
            result[name] = "ok"
        except Exception as e:  # se informa cualquier falla de empaquetado
            result[name] = f"{type(e).__name__}: {e}"
    try:
        import faster_whisper

        vad = Path(faster_whisper.__file__).parent / "assets"
        result["vad_assets"] = sorted(p.name for p in vad.glob("*")) or "faltan"
    except Exception as e:
        result["vad_assets"] = str(e)
    if audio:  # opcional: transcribir un archivo de verdad con el modelo tiny
        try:
            from revoz.core import transcribe
            from revoz.ui.main_window import MODELS_DIR

            t = transcribe.Whisper("tiny", MODELS_DIR).transcribe(Path(audio))
            result["transcript"] = t.text
        except Exception as e:
            result["transcript"] = f"{type(e).__name__}: {e}"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), "utf-8")


def main() -> None:
    if "--selftest" in sys.argv:
        args = sys.argv[sys.argv.index("--selftest") + 1 :]
        selftest(Path(args[0]), args[1] if len(args) > 1 else "")
        return
    app = QApplication(sys.argv)
    app.setApplicationName("Revoz")
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(QIcon(str(ICON)))
    wheel.install(app)  # la rueda del mouse no cambia desplegables sin querer
    win = MainWindow(section=2 if RESTART_FLAG in sys.argv else 0)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
