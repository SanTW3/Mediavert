"""Сторож: замечает, что макрос перестал ловить рыбу, и решает, когда будить хозяина."""
import time
from .i18n import tr


class Watchdog:
    def __init__(self, cfg, clock=time.monotonic):
        self.cfg = cfg
        self.clock = clock
        self.reset()

    def reset(self):
        now = self.clock()
        self.failed_casts = 0
        self.last_catch = now
        self.window_lost_since = None

    def on_cast_failed(self):
        self.failed_casts += 1

    def on_catch(self):
        self.failed_casts = 0
        self.last_catch = self.clock()

    def on_window_state(self, ok):
        if ok:
            self.window_lost_since = None
        elif self.window_lost_since is None:
            self.window_lost_since = self.clock()

    def check(self):
        """Причина тревоги или None."""
        cfg = self.cfg
        now = self.clock()
        limit = int(cfg.get("wd_failed_casts") or 0)
        if limit > 0 and self.failed_casts >= limit:
            return tr("{failed_casts} забросов подряд без поклёвки — возможно, кончилась наживка, дисконнект или персонажа сдвинули", failed_casts=self.failed_casts)
        minutes = float(cfg.get("wd_no_catch_min") or 0)
        if minutes > 0 and now - self.last_catch >= minutes * 60:
            return tr("Ни одной рыбы за {minutes:g} мин", minutes=minutes)
        grace = float(cfg.get("wd_window_grace_sec") or 0)
        if (cfg.get("wd_check_window") and self.window_lost_since is not None
                and now - self.window_lost_since >= grace):
            return tr("Окно Roblox закрыто или не активно — игра вылетела или окно свернули")
        return None
