"""Настройки макроса: значения по умолчанию, загрузка и сохранение в config.json."""
import copy
import json
import os
import sys

CONFIG_VERSION = 5

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "guide_shown": False,       # гайд при первом запуске

    # General
    "hotkey_start_stop": "f1",
    "hotkey_change_area": "f2",
    "hotkey_exit": "f3",
    "hotkey_blackout": "f4",
    "hotkey_hud": "f5",
    "hud_enabled": True,
    "auto_update": True,        # проверять новую версию exe при запуске (GitHub Releases)
    "update_repo": "SanTW3/Mediavert",
    "last_version": "",         # версия при прошлом запуске — чтобы сказать «обновлено до …»        # HUD поверх игры: рамка области F2 и показатели
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
    "buy_right": None,
    "loops_per_purchase": 100,
    "buy_type_amount": True,
    "buy_skip_first": False,
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
    "black_threshold": 0.85,    # доля окна Roblox, которая должна быть чёрной (0 — проверка выключена)
    "black_level": 10,          # «чёрный» пиксель: все каналы не ярче этого
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
    "progress_color": [173, 254, 128],
    "require_progress": True,

    # Журнал улова: снимок уведомления о рыбе после мини-игры + Windows OCR
    "catch_log_enabled": True,
    "catch_area": None,         # None — центральная часть окна Roblox, где появляется «New Item <…>»
    "catch_capture_delay": 0.15,
    "catch_window": 2.0,        # сколько секунд после мини-игры снимать уведомление
    "catch_notify_each": False,
    "fish_names": {},          # свои названия рыб из каталога: {"04": "…"}

    # Чёрный экран во время рыбалки
    "blackout_enabled": False,
    "blackout_all_monitors": True,
    "blackout_delay": 3.0,
    "tg_control": True,

    # Мини-приложение Telegram (страница на GitHub Pages, связь через ntfy)
    "remote_enabled": True,
    "remote_secret": "",
    "webapp_url": "https://santw3.github.io/gpo-macro/",
    # Брокеры через пробел; пусто — только ntfy
    "mqtt_url": ("wss://broker.emqx.io:8084/mqtt wss://broker.hivemq.com:8884/mqtt "
                 "wss://mqtt.eclipseprojects.io:443/mqtt wss://public:public@public.cloud.shiftr.io:443/"),

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
    version = CONFIG_VERSION   # нет файла — настройки новые, переносить нечего
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        version = int(data.get("config_version") or 1)
        for key, value in data.items():
            if key in cfg:
                cfg[key] = value
    except (OSError, ValueError):
        pass
    if not cfg["remote_secret"]:
        cfg["remote_secret"] = new_secret()
    migrate(cfg, version)
    return cfg


def migrate(cfg, version):
    """Перенос старых настроек (версии version) на новые значения по умолчанию."""
    if version < 2:
        # v2: журнал улова включён сразу и снимает уведомление с первых мгновений
        cfg["catch_log_enabled"] = True
        if float(cfg.get("catch_capture_delay") or 0) >= 1.0:
            cfg["catch_capture_delay"] = 0.15
    if version < 4 and cfg.get("mqtt_url") in ("wss://broker.emqx.io:8084/mqtt",
                                                "wss://broker.emqx.io:8084/mqtt wss://broker.hivemq.com:8884/mqtt"):
        # v3: второй брокер на случай, если к первому не пускает сеть телефона;
        # v4: ещё два на порту 443 — нестандартные порты часто закрыты
        cfg["mqtt_url"] = DEFAULTS["mqtt_url"]
    if version < 5 and float(cfg.get("black_threshold") or 0) == 0.5:
        # v5: 50% тёмного экрана бывает и ночью в игре — макрос прыгал без чёрного экрана
        cfg["black_threshold"] = DEFAULTS["black_threshold"]
    # Пустые «свои названия» скрывали название рыбы в каталоге
    cfg["fish_names"] = {k: v for k, v in dict(cfg.get("fish_names") or {}).items() if str(v or "").strip()}
    cfg["config_version"] = CONFIG_VERSION


def new_secret():
    """Секретная часть имени топиков ntfy: кто её знает, тот видит статус и управляет макросом."""
    import secrets
    return secrets.token_urlsafe(18)


def save(cfg, path=CONFIG_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
