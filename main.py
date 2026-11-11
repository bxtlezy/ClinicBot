import sys
import traceback
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMessageBox

from gui import ClinicApplication


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Поликлиника")
    app.setStyle("Fusion")
    base_dir = Path(__file__).resolve().parent
    style = base_dir / "styles.qss"
    style_text = style.read_text(encoding="utf-8")
    chevron = (base_dir / "assets" / "chevron-down.svg").as_posix()
    style_text = style_text.replace("__CHEVRON_URL__", chevron)
    app.setStyleSheet(style_text)
    try:
        controller = ClinicApplication()
    except Exception as error:
        traceback.print_exc()
        QMessageBox.critical(None, "Ошибка запуска", f"Не удалось открыть программу: {error}")
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
