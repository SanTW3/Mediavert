"""Полупрозрачные оверлеи поверх экрана: выбор области (F2) и выбор точки."""
import ctypes
import sys
import tkinter as tk


def virtual_screen(root):
    if sys.platform == "win32":
        m = ctypes.windll.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class _Overlay(tk.Toplevel):
    def __init__(self, root, hint):
        super().__init__(root)
        self.vx, self.vy, vw, vh = virtual_screen(root)
        self.overrideredirect(True)
        self.geometry(f"{vw}x{vh}+{self.vx}+{self.vy}")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.35)
        self.configure(bg="black", cursor="crosshair")
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        # Подсказка — по центру основного монитора, а не на стыке двух
        self.canvas.create_text(-self.vx + root.winfo_screenwidth() // 2, 40,
                                text=hint + "   (Esc или правый клик — отмена)",
                                fill="white", font=("Segoe UI", 16, "bold"))
        # Esc ловим и здесь, и глобально в gui.py: окну без рамки Windows часто не даёт фокус
        self.bind("<Escape>", lambda e: self.cancel())
        self.canvas.bind("<Escape>", lambda e: self.cancel())
        self.canvas.bind("<ButtonPress-3>", lambda e: self.cancel())
        self.result = None
        self.closed = False
        self.after(30, self._grab_focus)

    def _grab_focus(self):
        if self.closed:
            return
        self.lift()
        self.focus_force()
        self.canvas.focus_set()

    def cancel(self):
        self.finish(None)

    def finish(self, result):
        if self.closed:
            return
        self.closed = True
        self.result = result
        self.destroy()


class AreaSelector(_Overlay):
    """Тянем мышью прямоугольник вокруг полос мини-игры."""

    def __init__(self, root, current, callback):
        super().__init__(root, "Выделите мышью область с полосами мини-игры")
        self.callback = callback
        self.start = None
        self.rect = None
        if current:
            x, y, w, h = current
            self.canvas.create_rectangle(x - self.vx, y - self.vy, x - self.vx + w, y - self.vy + h,
                                         outline="#00ff00", width=3, dash=(6, 4))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

    def _press(self, e):
        self.start = (e.x, e.y)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#00ff00", width=3)

    def _drag(self, e):
        if self.start:
            self.canvas.coords(self.rect, *self.start, e.x, e.y)

    def _release(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        x, y = min(x0, e.x), min(y0, e.y)
        w, h = abs(e.x - x0), abs(e.y - y0)
        cb = self.callback
        if w < 5 or h < 5:
            self.finish(None)
            return
        area = [x + self.vx, y + self.vy, w, h]
        self.finish(area)
        cb(area)


class PointPicker(_Overlay):
    """Один клик — запоминаем экранные координаты."""

    def __init__(self, root, hint, callback):
        super().__init__(root, hint)
        self.callback = callback
        self.canvas.bind("<ButtonPress-1>", self._click)

    def _click(self, e):
        point = [e.x + self.vx, e.y + self.vy]
        cb = self.callback
        self.finish(point)
        cb(point)
