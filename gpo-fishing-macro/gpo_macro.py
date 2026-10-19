"""Точка входа: python gpo_macro.py"""
import sys

from macro import winput

if "--selftest" in sys.argv:
    # Проверка собранного exe в CI: работает ли внутри него Windows OCR
    from macro.selftest import run
    sys.exit(run())

winput.set_dpi_aware()  # до создания окна, иначе координаты поедут при масштабе > 100%

from macro.gui import App  # noqa: E402

if __name__ == "__main__":
    App().run()
