"""Настройки макроса: значения по умолчанию, загрузка и сохранение в config.json."""
import copy
import json
import os
import sys

DEFAULTS = {
    # General
    "hotkey_start_stop": "f1",
    "hotkey_change_area": "f2",
    "hotkey_exit": "f3",
    "always_on_top": True,
    "rod_hotkey": "7",
    "other_hotkey": "2",

    # Область мини-игры (x, y, ширина, высота) — выбирается по F2
    "area": None,

    # Casting
    "water_point": None,
    "cast_hold": 1.0,
    "recast_timeout": 20.0,

    # Fishing
    "kp": 0.9,
    "kd": 0.3,
    "pd_clamp": 1.0,
    "fish_end_delay": 2.0,

    # Pre-Cast
    "auto_buy_bait": False,
    "buy_left": None,
    "buy_middle": None,
    "loops_per_purchase": 100,
    "buy_type_amount": True,
    "auto_store_fruit": False,
    "fruit_hotkey": "3",
    "store_point": None,
    "auto_select_bait": False,
    "bait_point": None,

    # Detection
    "zone_color": [85, 170, 255],
    "zone_tolerance": 40,
    "fish_white_min": 200,
    "min_zone_pixels": 30,
    "scan_fps": 60,

    # Literally Everything Else — задержки как в оригинале
    "focus_delay": 0.0,
    "post_focus_delay": 1.0,
    "buy_e_delay": 3.0,
    "buy_click_delay": 3.0,
    "buy_type_delay": 3.0,
    "micro_delay": 0.05,
    "fruit_hotkey_delay": 1.0,
    "fruit_store_delay": 2.0,
    "fruit_shift_delay": 0.5,
    "fruit_backspace_delay": 1.5,
    "bait_click_delay": 0.5,
    "black_threshold": 0.5,
    "black_spam_delay": 0.25,
    "black_spam_key": "space",
    "black_max_sec": 30.0,
    "rod_select_delay": 0.5,
    "cursor_delay": 0.05,
    "scan_loop_delay": 0.1,
    "approach_damping": 2.0,
    "chase_damping": 0.5,
    "gap_mult": 2.0,
    "resend_interval": 0.5,
    "bar_mode": "dark",
    "dark_max": 60,

    # Phone alarm
    "alarm_enabled": True,
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "telegram_token": "",
    "telegram_chat_id": "",
    "pushover_user": "",
    "pushover_token": "",
    "alarm_repeat_sec": 60,
    "alarm_screenshot": True,

    # Watchdog
    "wd_failed_casts": 5,
    "wd_no_catch_min": 10.0,
    "wd_check_window": True,
    "wd_window_grace_sec": 30.0,
    "wd_stop_on_alarm": True,
}


def app_dir():
    """Папка рядом с exe (PyInstaller) или со скриптом."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


CONFIG_PATH = os.path.join(app_dir(), "config.json")


def load(path=CONFIG_PATH):
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, value in data.items():
            if key in cfg:
                cfg[key] = value
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg, path=CONFIG_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
