"""HUD поверх игры: рамка области мини-игры (F2) и панель с показателями — видны всё время.

Окно прозрачное (фон вырезан цветом-ключом), не ловит мышь и не забирает фокус, а на Windows 10 2004+
ещё и исключено из захвата экрана — макрос и его скриншоты видят игру без HUD. Рамка, шкала и панель
рисуются снаружи области мини-игры, так что даже без исключения из захвата они не мешают распознаванию.
"""
import ctypes
import sys
import time
import tkinter as tk

KEY = "#010203"            # цвет-ключ: всё, что им залито, прозрачно
PANEL_W, PANEL_H = 250, 178
GAP = 14                   # от области до панели
GAUGE_W = 10               # шкала «полоса/рыба» слева от области
STATUS_COLORS = {"red": "#ff5d73", "green": "#4ade80", "blue": "#5ecbff", "orange": "#ffb347"}


def _click_through(win):
    """Прозрачно для мыши, без фокуса, вне захвата. False — Windows не умеет исключать из захвата."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = (wintypes.HWND,)
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    style = user32.GetWindowLongW(hwnd, -20)   # GWL_EXSTYLE
    # WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    style |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000
    user32.SetWindowLongW(hwnd, -20, ctypes.c_long(style).value)
    return bool(user32.SetWindowDisplayAffinity(hwnd, 0x11))   # WDA_EXCLUDEFROMCAPTURE


def fmt_time(sec):
    sec = max(int(sec), 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def layout(area, screen_w, screen_h):
    """Где окно HUD и что внутри него: (x, y, w, h), рамка области, панель — в координатах окна.
    Панель справа от области, а если не влезает — слева; без области — в левом верхнем углу экрана."""
    if not area:
        return (20, 20, PANEL_W, PANEL_H), None, (0, 0)
    ax, ay, aw, ah = (int(v) for v in area)
    left = ax - GAUGE_W - 8
    px = ax + aw + GAP
    if px + PANEL_W > screen_w and left - GAP - PANEL_W >= 0:
        px = left - GAP - PANEL_W
    x0 = min(left, px) - 4
    y0 = ay - 6
    x1 = max(ax + aw + 6, px + PANEL_W)
    y1 = max(ay + ah + 6, ay + PANEL_H)
    win = (x0, y0, x1 - x0, y1 - y0)
    frame = (ax - x0 - 3, ay - y0 - 3, ax - x0 + aw + 2, ay - y0 + ah + 2)
    return win, frame, (px - x0, 0)


class Hud:
    def __init__(self, root, log, font="Segoe UI"):
        self.root = root
        self.log = log
        self.font = font
        self.win = None
        self.canvas = None
        self._geom = None
        self._warned = False

    @property
    def visible(self):
        return self.win is not None

    def show(self):
        if self.win is not None:
            return
        win = tk.Toplevel(self.root, bg=KEY)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", KEY)   # только Windows
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(win, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.win = win
        self._geom = None
        if sys.platform == "win32":
            win.update_idletasks()
            try:
                excluded = _click_through(win)
            except Exception:
                excluded = False
            if not excluded and not self._warned:
                self._warned = True
                self.log("HUD: эта Windows не умеет прятать окна от захвата экрана — HUD виден на скриншотах "
                         "(распознаванию он не мешает: рисуется вокруг области)")

    def hide(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = self.canvas = None

    def update(self, data):
        """data: area, status, color, running, runtime, casts, catches, last_fish, live (рыба/полоса/ЛКМ)."""
        if self.win is None:
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        (x, y, w, h), frame, (px, py) = layout(data.get("area"), sw, sh)
        geom = f"{w}x{h}+{x}+{y}"
        if geom != self._geom:
            self.win.geometry(geom)
            self._geom = geom
        c = self.canvas
        c.delete("all")
        live = data.get("live") or {}
        fresh = live and time.monotonic() - live.get("t", 0) < 0.5
        if frame:
            fx0, fy0, fx1, fy1 = frame
            color = "#4ade80" if fresh else "#2f9e5a"
            c.create_rectangle(fx0, fy0, fx1, fy1, outline=color, width=2)
            # Шкала слева от области: трек, полоса игрока, рыба — видно, как PD держит полосу на рыбе
            gx0, gx1 = fx0 - GAUGE_W - 5, fx0 - 5
            c.create_rectangle(gx0, fy0, gx1, fy1, outline="#24476b", fill="#0b1830")
            if fresh and live.get("bar_h"):
                top, hgt = live["bar_top"], live["bar_h"]

                def to_y(v):
                    return fy0 + 2 + (v - top) / hgt * (fy1 - fy0 - 4)
                c.create_rectangle(gx0 + 1, to_y(live["zone_top"]), gx1 - 1, to_y(live["zone_bottom"]),
                                   fill="#c084fc", outline="")
                if live.get("fish") is not None:
                    fy = to_y(live["fish"])
                    c.create_line(gx0 - 4, fy, gx1 + 4, fy, fill="#ff5d73", width=3)
        self._panel(c, px, py, data, live if fresh else None)

    def _panel(self, c, x, y, data, live):
        font = self.font
        c.create_rectangle(x, y, x + PANEL_W, y + PANEL_H, fill="#06122b", outline="#5ecbff", width=1)
        c.create_rectangle(x, y, x + PANEL_W, y + 4, fill="#5ecbff", outline="")
        c.create_text(x + 12, y + 18, anchor="w", text="FISHPILOT", fill="#5ecbff", font=(font, 11, "bold"))
        status = str(data.get("status") or "").upper()
        c.create_text(x + PANEL_W - 10, y + 18, anchor="e", text=status[:22],
                      fill=STATUS_COLORS.get(data.get("color"), "#e6f1ff"), font=(font, 10, "bold"))
        runtime = data.get("runtime") or 0
        catches = data.get("catches") or 0
        rate = f"{catches * 3600 / runtime:.0f}" if runtime >= 60 else "–"
        rows = [
            ("Время", fmt_time(runtime)), ("Рыб в час", rate),
            ("Поймано", str(catches)), ("Забросов", str(data.get("casts") or 0)),
        ]
        for i, (k, v) in enumerate(rows):
            cx = x + 12 + (i % 2) * 120
            cy = y + 44 + (i // 2) * 34
            c.create_text(cx, cy, anchor="w", text=v, fill="#e6f1ff", font=(font, 14, "bold"))
            c.create_text(cx, cy + 15, anchor="w", text=k.upper(), fill="#7f9cc0", font=(font, 8))
        last = data.get("last_fish")
        c.create_text(x + 12, y + 122, anchor="w", text=f"Последняя: {last}" if last else "Рыбы ещё не было",
                      fill="#e6f1ff" if last else "#7f9cc0", font=(font, 10))
        if live and live.get("bar_h"):
            h = live["bar_h"]
            fish = "–" if live.get("fish") is None else f"{100 - (live['fish'] - live['bar_top']) / h * 100:.0f}%"
            zone = f"{100 - ((live['zone_top'] + live['zone_bottom']) / 2 - live['bar_top']) / h * 100:.0f}%"
            line = f"Рыба {fish} · Полоса {zone} · {live.get('fps', 0):.0f} к/с"
            c.create_text(x + 12, y + 146, anchor="w", text=line, fill="#e6f1ff", font=(font, 10))
            hold = live.get("hold")
            c.create_text(x + 12, y + 165, anchor="w", text="ЛКМ ● зажата" if hold else "ЛКМ ○ отпущена",
                          fill="#4ade80" if hold else "#7f9cc0", font=(font, 10, "bold"))
        else:
            key = str(data.get("area_key") or "F2").upper()
            hint = "Мини-игры нет" if data.get("area") else f"Область не выбрана — нажмите {key}"
            c.create_text(x + 12, y + 150, anchor="w", text=hint, fill="#7f9cc0", font=(font, 10))
