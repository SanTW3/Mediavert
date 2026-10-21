"""Лог-файл и сбор диагностики.

У оконного exe нет консоли: без файла ошибки в потоках и в обработчиках окна пропадали бесследно.
Лог — %APPDATA%\\FishPilot\\logs\\fishpilot.log (до 3 файлов по 1 МБ). Секреты в лог не пишутся,
а в диагностике дополнительно вычищаются.
"""
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading

from . import __version__, config

log = logging.getLogger("fishpilot")
_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")


def log_dir():
    path = os.path.join(config.data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def log_path():
    return os.path.join(log_dir(), "fishpilot.log")


def setup():
    """Лог в файл и перехват необработанных ошибок (главный поток и фоновые потоки)."""
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
        return
    try:
        handler = logging.handlers.RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2,
                                                       encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info("FishPilot %s starting (%s, Python %s)", __version__, platform.platform(), platform.python_version())

    def excepthook(exc_type, exc, tb):
        log.error("Unhandled error", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args):
        log.error("Unhandled error in thread %s", getattr(args.thread, "name", "?"),
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def redact(text, cfg=None):
    """Убрать из текста токены и секреты (на случай, если они попали в сообщения об ошибках)."""
    text = _TOKEN_RE.sub("<token>", text)
    for key in config.SECRET_KEYS:
        value = str((cfg or {}).get(key) or "")
        if len(value) >= 6:
            text = text.replace(value, f"<{key}>")
    return text


def diagnostics(cfg, extra=""):
    """Текст для сообщения об ошибке: версия, система, экран, главные настройки и конец лога. Без секретов."""
    lines = [
        f"FishPilot {__version__}",
        f"Windows: {platform.platform()}  Python {platform.python_version()}  frozen={getattr(sys, 'frozen', False)}",
        f"language={cfg.get('language')} area={cfg.get('area')} water_point={bool(cfg.get('water_point'))} "
        f"catch_area={cfg.get('catch_area')} bar_mode={cfg.get('bar_mode')} hud={cfg.get('hud_enabled')} "
        f"blackout={cfg.get('blackout_enabled')} telegram={'yes' if cfg.get('telegram_token') else 'no'} "
        f"remote={cfg.get('remote_enabled')}",
    ]
    if extra:
        lines.append(extra)
    try:
        with open(log_path(), encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-150:]
        lines.append("--- log ---")
        lines.append("".join(tail))
    except OSError:
        pass
    return redact("\n".join(lines), cfg)
