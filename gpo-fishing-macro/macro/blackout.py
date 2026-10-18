"""Чёрный экран во время рыбалки.

Обычное чёрное окно поверх игры закрыло бы её и от макроса: он смотрит на экран через
захват (mss). Поэтому окно:
  * исключено из захвата экрана (SetWindowDisplayAffinity + WDA_EXCLUDEFROMCAPTURE,
    Windows 10 2004+) — человек видит чёрное, а макрос и скриншоты видят игру;
  * прозрачно для мыши (WS_EX_TRANSPARENT) — клики макроса уходят в Roblox;
  * не забирает фокус (WS_EX_NOACTIVATE) — клавиши тоже уходят в Roblox.
"""
import ctypes
import sys
import tkinter as tk

from . import winput
from .overlay import virtual_screen

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 0x2
WDA_EXCLUDEFROMCAPTURE = 0x11
MONITOR_DEFAULTTONEAREST = 2


def _roblox_monitor():
    """Прямоугольник монитора, на котором окно Roblox, или None."""
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MONITORINFO))
    hwnd = user32.FindWindowW(None, "Roblox")
    if not hwnd:
        return None
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info)):
        return None
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _make_invisible_to_capture(win):
    """Win32-стили окна. Возвращает False, если Windows не умеет исключать окна из захвата."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = (wintypes.HWND,)
    user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    user32.SetLayeredWindowAttributes.argtypes = (wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD)
    user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
    # winfo_id — внутреннее окно Tk, настоящий верхний HWND — его родитель
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(style).value)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


class Blackout:
    def __init__(self, root, log):
        self.root = root
        self.log = log
        self.win = None

    @property
    def active(self):
        return self.win is not None

    def show(self, all_monitors=True):
        if self.win is not None:
            return True
        rect = None if all_monitors or sys.platform != "win32" else _roblox_monitor()
        x, y, w, h = rect or virtual_screen(self.root)
        win = tk.Toplevel(self.root, bg="black")
        win.overrideredirect(True)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.attributes("-topmost", True)
        win.configure(cursor="none")
        # Еле заметная подсказка (тёмно-серым, чтобы экран оставался тёмным)
        tk.Label(win, text="Рыбалка идёт…  нажмите любую клавишу или кнопку мыши, чтобы включить экран",
                 fg="#2a2a2a", bg="black", font=("Segoe UI", 12)).place(relx=0.5, rely=0.5, anchor="center")
        win.update_idletasks()
        if sys.platform == "win32":
            try:
                ok = _make_invisible_to_capture(win)
            except Exception:
                ok = False
            if not ok:
                win.destroy()
                self.log("Чёрный экран недоступен: нужна Windows 10 версии 2004 или новее "
                         "(иначе макрос сам перестанет видеть игру)")
                return False
            # Показ окна мог увести фокус — возвращаем его игре
            self.root.after(100, winput.focus_roblox)
        self.win = win
        self.log("Экран затемнён — любая клавиша или кнопка мыши вернёт его")
        return True

    def hide(self):
        if self.win is None:
            return
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.win = None
        self.log("Экран включён")
