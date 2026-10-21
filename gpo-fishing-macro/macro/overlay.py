"""Полупрозрачные оверлеи поверх экрана: выбор области (F2) и выбор точки."""
import ctypes
import sys
import tkinter as tk
from .i18n import tr


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
                                text=hint + tr("   (Esc или правый клик — отмена)"),
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
    """Рамка области: нарисовать мышью, потом тянуть за углы и края, двигать целиком.

    Enter или двойной клик — сохранить, Esc или правый клик — отмена.
    Клик мимо рамки начинает новую.
    """
    HANDLE = 6       # половина размера квадратика-ручки
    GRAB = 10        # насколько близко к краю считается «за край»
    MIN = 8

    def __init__(self, root, current, callback, hint=None):
        # Подсказка по умолчанию — здесь, а не в аргументе: значение по умолчанию вычисляется один раз
        # при запуске, ещё до загрузки языка из настроек, и было всегда по-русски
        if hint is None:
            hint = tr("Выделите мышью область с полосами мини-игры")
        super().__init__(root, hint + tr(".  Тяните за углы и края, Enter или двойной клик — сохранить"))
        self.callback = callback
        self.box = None           # [x0, y0, x1, y1] в координатах холста
        self.mode = None          # "new" | "move" | сторона ("n", "se", …)
        self.anchor = None
        self.rect = None
        self.handles = []
        self.label = None
        if current:
            x, y, w, h = current
            self.box = [x - self.vx, y - self.vy, x - self.vx + w, y - self.vy + h]
            self._draw()
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", lambda e: self.confirm())
        self.canvas.bind("<Motion>", self._hover)
        self.bind("<Return>", lambda e: self.confirm())
        self.canvas.bind("<Return>", lambda e: self.confirm())

    # ---------- рисование ----------
    def _draw(self):
        c = self.canvas
        for item in [self.rect, self.label] + self.handles:
            if item:
                c.delete(item)
        self.handles = []
        if not self.box:
            self.rect = self.label = None
            return
        x0, y0, x1, y1 = self.box
        self.rect = c.create_rectangle(x0, y0, x1, y1, outline="#00ff00", width=3)
        for hx, hy in self._handle_points():
            self.handles.append(c.create_rectangle(hx - self.HANDLE, hy - self.HANDLE, hx + self.HANDLE,
                                                   hy + self.HANDLE, fill="#00ff00", outline="white"))
        self.label = c.create_text(x0, y0 - 14, anchor="w", fill="#00ff00", font=("Segoe UI", 11, "bold"),
                                   text=tr("{v0}×{v1}   Enter — сохранить", v0=int(x1 - x0), v1=int(y1 - y0)))

    def _handle_points(self):
        x0, y0, x1, y1 = self.box
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return [(x0, y0), (mx, y0), (x1, y0), (x1, my), (x1, y1), (mx, y1), (x0, y1), (x0, my)]

    # ---------- что под мышью ----------
    def _hit(self, x, y):
        if not self.box:
            return None
        x0, y0, x1, y1 = self.box
        g = self.GRAB
        near_l, near_r = abs(x - x0) <= g, abs(x - x1) <= g
        near_t, near_b = abs(y - y0) <= g, abs(y - y1) <= g
        inside_x, inside_y = x0 - g <= x <= x1 + g, y0 - g <= y <= y1 + g
        side = ""
        if near_t and inside_x:
            side += "n"
        elif near_b and inside_x:
            side += "s"
        if near_l and inside_y:
            side += "w"
        elif near_r and inside_y:
            side += "e"
        if side:
            return side
        if x0 < x < x1 and y0 < y < y1:
            return "move"
        return None

    CURSORS = {"n": "sb_v_double_arrow", "s": "sb_v_double_arrow", "e": "sb_h_double_arrow",
               "w": "sb_h_double_arrow", "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw",
               "sw": "size_ne_sw", "move": "fleur"}

    def _hover(self, e):
        if self.mode:
            return
        try:
            self.canvas.configure(cursor=self.CURSORS.get(self._hit(e.x, e.y), "crosshair"))
        except tk.TclError:
            self.canvas.configure(cursor="crosshair")  # не все курсоры есть на всех системах

    # ---------- мышь ----------
    def _press(self, e):
        hit = self._hit(e.x, e.y)
        if hit:
            self.mode = hit
            self.anchor = (e.x, e.y, list(self.box))
        else:
            self.mode = "new"
            self.anchor = (e.x, e.y)
            self.box = [e.x, e.y, e.x, e.y]
            self._draw()

    def _drag(self, e):
        if not self.mode:
            return
        if self.mode == "new":
            ax, ay = self.anchor
            self.box = [min(ax, e.x), min(ay, e.y), max(ax, e.x), max(ay, e.y)]
        else:
            sx, sy, (x0, y0, x1, y1) = self.anchor
            dx, dy = e.x - sx, e.y - sy
            if self.mode == "move":
                x0, x1, y0, y1 = x0 + dx, x1 + dx, y0 + dy, y1 + dy
            else:
                if "w" in self.mode:
                    x0 = min(x0 + dx, x1 - self.MIN)
                if "e" in self.mode:
                    x1 = max(x1 + dx, x0 + self.MIN)
                if "n" in self.mode:
                    y0 = min(y0 + dy, y1 - self.MIN)
                if "s" in self.mode:
                    y1 = max(y1 + dy, y0 + self.MIN)
            self.box = [x0, y0, x1, y1]
        self._draw()

    def _release(self, e):
        if self.mode == "new" and self.box and (self.box[2] - self.box[0] < 5 or self.box[3] - self.box[1] < 5):
            self.box = None          # случайный клик — рамки нет
            self._draw()
        self.mode = None

    # ---------- сохранить ----------
    def confirm(self):
        if not self.box:
            return
        x0, y0, x1, y1 = (int(round(v)) for v in self.box)
        area = [x0 + self.vx, y0 + self.vy, x1 - x0, y1 - y0]
        cb = self.callback
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
