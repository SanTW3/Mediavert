"""Точка входа: python gpo_macro.py"""
from macro import winput

winput.set_dpi_aware()  # до создания окна, иначе координаты поедут при масштабе > 100%

from macro.gui import App  # noqa: E402

if __name__ == "__main__":
    App().run()
