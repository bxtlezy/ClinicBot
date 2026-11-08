import sys
import traceback
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMessageBox

from gui import ClinicApplication


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Поликлиника")
    app.setStyle("Fusion")
    style = Path(__file__).resolve().parent / "styles.qss"
    app.setStyleSheet(style.read_text(encoding="utf-8"))
    try:
        controller = ClinicApplication()
    except Exception as error:
        traceback.print_exc()
        QMessageBox.critical(None, "Ошибка запуска", f"Не удалось открыть программу: {error}")
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
