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


def screenshot_png():
    """Скриншот всех мониторов в PNG (для Telegram при тревоге)."""
    sct = _sct()
    shot = sct.grab(sct.monitors[0])
    return mss.tools.to_png(shot.rgb, shot.size)


def grab_monitor(step=8):
    """Основной монитор, прореженный в step раз — для проверки на чёрный экран."""
    sct = _sct()
    mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
    shot = sct.grab(mon)
    bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    return bgra[::step, ::step, 2::-1]
