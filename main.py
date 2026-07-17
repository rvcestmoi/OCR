import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from app.settings import load_settings, get_ui_value


def _background_ocr_requested() -> bool:
    args = {str(a or "").strip().lower() for a in sys.argv[1:]}
    if args.intersection({"--background-ocr", "--ocr-background", "--ocr-bg", "/background-ocr"}):
        return True
    try:
        return bool(get_ui_value(load_settings(), "background_ocr_enabled", False))
    except Exception:
        return False


app = QApplication(sys.argv)
window = MainWindow()

if _background_ocr_requested():
    try:
        settings = load_settings()
        hide_window = bool(get_ui_value(settings, "background_ocr_hide_window", True))
    except Exception:
        hide_window = True

    if hide_window:
        window.hide()
    else:
        window.showMinimized()

    QTimer.singleShot(0, window.start_background_ocr_mode)
else:
    window.showMaximized()

sys.exit(app.exec())
