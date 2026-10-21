"""Захват экрана через mss (быстрый, ~1–3 мс на небольшую область)."""
import threading

import numpy as np

try:
    import mss
    import mss.tools
except ImportError:  # для тестов без mss
    mss = None

_local = threading.local()


def _sct():
    # mss нельзя делить между потоками — у каждого свой экземпляр
    if getattr(_local, "sct", None) is None:
        _local.sct = getattr(mss, "MSS", mss.mss)()
    return _local.sct


def grab(area):
    """area = (x, y, w, h) → RGB массив HxWx3."""
    x, y, w, h = area
    shot = _sct().grab({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
    bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return bgra[:, :, 2::-1].copy()


def game_screenshot(max_side=1600):
    """Снимок для Telegram (тревога, кнопка «Скриншот»): только окно Roblox, уменьшенный, JPEG.
    Раньше снимались все мониторы — в чат уходили Discord, почта и всё, что открыто рядом, а огромная
    картинка с трёх мониторов не проходила по лимитам Telegram."""
    import io

    from PIL import Image

    from . import winput
    sct = _sct()
    rect = winput.roblox_rect()
    if rect and rect[2] > 100 and rect[3] > 100 and rect[0] > -10000:
        region = {"left": int(rect[0]), "top": int(rect[1]), "width": int(rect[2]), "height": int(rect[3])}
    else:
        region = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    shot = sct.grab(region)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def primary_monitor():
    sct = _sct()
    return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]


def grab_monitor(step=8):
    """Основной монитор, прореженный в step раз — для проверки на чёрный экран."""
    sct = _sct()
    mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    shot = sct.grab(mon)
    bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return bgra[::step, ::step, 2::-1]
