"""Ввод мыши и клавиатуры через WinAPI SendInput.

Roblox игнорирует часть «программных» событий (например, PostMessage),
поэтому шлём настоящие аппаратные события со скан-кодами.
На не-Windows системах модуль импортируется, но ничего не делает (для тестов).
"""
import ctypes
import sys
import time
from .i18n import tr

IS_WINDOWS = sys.platform == "win32"

# «Anti-Detect Micro Delay»: пауза между абсолютным перемещением и подёргиванием курсора
micro_delay = 0.05

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

# Виртуальные коды для клавиш, которые нам нужны
VK = {str(d): 0x30 + d for d in range(10)}
VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
VK.update({"enter": 0x0D, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "tab": 0x09,
           "shift": 0x10, "alt": 0x12})


if IS_WINDOWS:
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)

    def _send(*inputs):
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    def _mouse(flags, dx=0, dy=0):
        inp = INPUT(type=INPUT_MOUSE)
        inp.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        return inp

    def _key(vk, up=False):
        scan = user32.MapVirtualKeyW(vk, 0)
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
        return inp


def set_dpi_aware():
    """Чтобы координаты мыши совпадали с пикселями скриншота при масштабе 125%/150%."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def move_to(x, y):
    if not IS_WINDOWS:
        return
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = int((x - vx) * 65535 / max(vw - 1, 1))
    ny = int((y - vy) * 65535 / max(vh - 1, 1))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    _send(_mouse(flags, nx, ny))
    # Roblox обновляет позицию курсора только после относительного движения
    time.sleep(micro_delay)
    _send(_mouse(MOUSEEVENTF_MOVE, 1, 0))
    _send(_mouse(MOUSEEVENTF_MOVE, -1, 0))


def mouse_down():
    if IS_WINDOWS:
        _send(_mouse(MOUSEEVENTF_LEFTDOWN))


def mouse_up():
    if IS_WINDOWS:
        _send(_mouse(MOUSEEVENTF_LEFTUP))


def click(x=None, y=None, hold=0.05):
    if x is not None and y is not None:
        move_to(x, y)
        time.sleep(micro_delay)
    mouse_down()
    time.sleep(hold)
    mouse_up()


def press_key(name, hold=0.05):
    if not IS_WINDOWS:
        return
    vk = VK.get(str(name).lower())
    if vk is None:
        raise ValueError(tr("Неизвестная клавиша: {name}", name=name))
    _send(_key(vk))
    time.sleep(hold)
    _send(_key(vk, up=True))


def type_text(text):
    for ch in str(text):
        press_key(ch)
        time.sleep(0.03)


def foreground_title():
    """Заголовок активного окна ('' вне Windows)."""
    if not IS_WINDOWS:
        return ""
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def roblox_rect():
    """(x, y, w, h) окна Roblox на экране или None."""
    if not IS_WINDOWS:
        return None
    hwnd = user32.FindWindowW(None, "Roblox")
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def roblox_window_exists():
    if not IS_WINDOWS:
        return True
    return bool(user32.FindWindowW(None, "Roblox"))


def focus_roblox():
    """Выводит окно Roblox на передний план. True, если окно найдено."""
    if not IS_WINDOWS:
        return True
    hwnd = user32.FindWindowW(None, "Roblox")
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Windows не даёт фоновому процессу забрать фокус, пока не было ввода — нажимаем Alt
    _send(_key(VK["alt"]))
    _send(_key(VK["alt"], up=True))
    user32.SetForegroundWindow(hwnd)
    return True
