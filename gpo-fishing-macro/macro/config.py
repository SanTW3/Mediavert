"""Настройки макроса: значения по умолчанию, загрузка и сохранение в config.json."""
import base64
import copy
import ctypes
import json
import math
import os
import re
import shutil
import sys

CONFIG_VERSION = 7

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "guide_shown": False,       # гайд при первом запуске
    "language": "en",           # язык интерфейса: en, ru, es, pt (выбирается на первой странице гайда)

    # General
    "hotkey_start_stop": "f1",
    "hotkey_change_area": "f2",
    "hotkey_exit": "f3",
    "hotkey_blackout": "f4",
    "hotkey_hud": "f5",
    "hud_enabled": True,
    "auto_update": True,        # проверять новую версию exe при запуске (GitHub Releases)
    "last_version": "",         # версия при прошлом запуске — чтобы сказать «обновлено до …»        # HUD поверх игры: рамка области F2 и показатели
    "always_on_top": False,     # поверх игры окно попадало в захват экрана и под клики заброса
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
    "webapp_url": "https://santw3.github.io/FishPilot/",
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
    "alarm_screenshot": False,  # снимок окна Roblox к тревоге; уходит на серверы Telegram — включают сами

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


def data_dir():
    """Где хранятся настройки, улов и логи: %APPDATA%\\FishPilot. Рядом с exe нельзя: в Program Files
    нет прав на запись, а при обновлении папка программы целиком заменяется."""
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, "FishPilot")
    os.makedirs(path, exist_ok=True)
    return path


def migrate_old_location():
    """Раньше config.json и fish_learned лежали рядом с exe — копируем их в data_dir() один раз.
    Старые файлы не удаляем: можно вернуться на прежнюю версию."""
    old, new = app_dir(), data_dir()
    if os.path.normcase(os.path.abspath(old)) == os.path.normcase(os.path.abspath(new)):
        return False
    moved = False
    if os.path.exists(os.path.join(old, "config.json")) and not os.path.exists(os.path.join(new, "config.json")):
        shutil.copyfile(os.path.join(old, "config.json"), os.path.join(new, "config.json"))
        moved = True
    if os.path.isdir(os.path.join(old, "fish_learned")) and not os.path.exists(os.path.join(new, "fish_learned")):
        shutil.copytree(os.path.join(old, "fish_learned"), os.path.join(new, "fish_learned"))
        moved = True
    return moved


CONFIG_PATH = os.path.join(data_dir(), "config.json")
# Токены и секреты — отдельно и зашифрованы для текущего пользователя Windows (DPAPI): config.json
# можно показать или отдать другу, не раздав свой бот и управление своим макросом
SECRET_KEYS = ("telegram_token", "telegram_chat_id", "pushover_user", "pushover_token", "remote_secret", "ntfy_topic")
# Адрес мини-приложения из чужих настроек мог бы увести панель на чужой сайт — разрешаем только GitHub Pages
WEBAPP_RE = re.compile(r"^https://[A-Za-z0-9-]+\.github\.io/[A-Za-z0-9_./-]*$")


# Допустимые значения чисел: вне диапазона, NaN, бесконечность — ошибка ввода (поле подсвечивается красным)
LIMITS = {
    "cast_hold": (0.05, 10), "recast_timeout": (1, 600), "kp": (0, 20), "kd": (0, 20), "pd_clamp": (0.01, 10),
    "fish_end_delay": (0, 30), "loops_per_purchase": (1, 10000), "zone_tolerance": (0, 255),
    "fish_white_min": (0, 255), "min_zone_pixels": (1, 100000), "scan_fps": (5, 240),
    "focus_delay": (0, 60), "post_focus_delay": (0, 60), "buy_e_delay": (0, 60), "buy_click_delay": (0, 60),
    "buy_type_delay": (0, 60), "micro_delay": (0, 1), "fruit_hotkey_delay": (0, 60), "fruit_store_delay": (0, 60),
    "fruit_shift_delay": (0, 60), "fruit_backspace_delay": (0, 60), "bait_click_delay": (0, 60),
    "black_threshold": (0, 1), "black_level": (0, 255), "black_spam_delay": (0.01, 10), "black_max_sec": (1, 3600),
    "rod_select_delay": (0, 60), "cursor_delay": (0, 5), "scan_loop_delay": (0.005, 5),
    "approach_damping": (0, 20), "chase_damping": (0, 20), "gap_mult": (0, 20), "resend_interval": (0, 10),
    "dark_max": (0, 255), "catch_capture_delay": (0, 10), "catch_window": (0.2, 10), "blackout_delay": (0, 600),
    "alarm_repeat_sec": (0, 3600), "wd_failed_casts": (0, 1000), "wd_no_catch_min": (0, 1440),
    "wd_window_grace_sec": (1, 3600),
}
LAST_LOAD_ERROR = None    # текст ошибки, если config.json был испорчен (окно покажет его в журнале)


def parse_number(key, raw, kind=float):
    """Число из поля ввода или None, если ввод неверный. Принимает и запятую: «0,5»."""
    try:
        value = float(str(raw).strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    lo, hi = LIMITS.get(key, (0, 1e9))
    if not lo <= value <= hi:
        return None
    if kind is int:
        if value != int(value):
            return None
        return int(value)
    return value


def _clean(key, value):
    """Значение из файла настроек того же типа, что и по умолчанию; иначе — значение по умолчанию."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, (int, float)):
        number = parse_number(key, value, int if isinstance(default, int) else float)
        return default if number is None else number
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, dict):
        return value if isinstance(value, dict) else default
    if isinstance(default, list):     # цвета: [r, g, b]
        ok = (isinstance(value, list) and len(value) == len(default)
              and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))
        return [int(v) for v in value] if ok else default
    # None по умолчанию — точки [x, y] и области [x, y, w, h]
    ok = (isinstance(value, list) and len(value) in (2, 4)
          and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value))
    return [int(v) for v in value] if ok else None


def load(path=CONFIG_PATH):
    global LAST_LOAD_ERROR
    cfg = copy.deepcopy(DEFAULTS)
    version = CONFIG_VERSION   # нет файла — настройки новые, переносить нечего
    LAST_LOAD_ERROR = None
    data = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        data = None
    except Exception as exc:
        # Испорченный файл не затираем молча: копия рядом, программа стартует с настройками по умолчанию
        LAST_LOAD_ERROR = f"{exc}"
        try:
            shutil.copyfile(path, path + ".bad")
        except OSError:
            pass
        data = None
    if data is not None:
        try:
            version = int(data.get("config_version") or 1)
        except (TypeError, ValueError):
            version = 1
        for key, value in data.items():
            if key in cfg:
                cfg[key] = _clean(key, value)
    for key, value in load_secrets(path).items():
        if key in SECRET_KEYS and isinstance(value, str):
            cfg[key] = value
    if not WEBAPP_RE.match(str(cfg.get("webapp_url") or "")):
        cfg["webapp_url"] = DEFAULTS["webapp_url"]
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
    if version < 6:
        # v6: появились языки, по умолчанию английский. Кто уже пользовался программой — тот видел её
        # по-русски, оставляем русский (сменить можно в «Программа → Язык»)
        cfg["language"] = "ru"
    if version < 7 and str(cfg.get("webapp_url") or "").rstrip("/").lower() == "https://santw3.github.io/gpo-macro":
        # v7: репозиторий мини-приложения переименован в FishPilot — старый адрес Pages больше не открывается
        cfg["webapp_url"] = DEFAULTS["webapp_url"]
    # Пустые «свои названия» скрывали название рыбы в каталоге
    cfg["fish_names"] = {k: v for k, v in dict(cfg.get("fish_names") or {}).items() if str(v or "").strip()}
    cfg["config_version"] = CONFIG_VERSION


def new_secret():
    """Секретная часть имени топиков ntfy: кто её знает, тот видит статус и управляет макросом."""
    import secrets
    return secrets.token_urlsafe(18)


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def save(cfg, path=CONFIG_PATH):
    public = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
    _write(path, json.dumps(public, indent=2, ensure_ascii=False).encode("utf-8"))
    secrets = {k: cfg.get(k, "") for k in SECRET_KEYS}
    _write(secrets_path(path), protect(json.dumps(secrets).encode("utf-8")))


def export_settings(cfg, path):
    """Настройки без токенов и секретов — чтобы поделиться с другом."""
    public = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
    _write(path, json.dumps(public, indent=2, ensure_ascii=False).encode("utf-8"))


def import_settings(cfg, path):
    """Чужие настройки: берём только обычные значения (секреты, адреса и версию — нет). Возвращает число ключей."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    skip = set(SECRET_KEYS) | {"config_version", "webapp_url", "mqtt_url", "ntfy_server", "guide_shown",
                               "last_version"}
    n = 0
    for key, value in data.items():
        if key in DEFAULTS and key not in skip:
            cfg[key] = _clean(key, value)
            n += 1
    return n


def secrets_path(path=CONFIG_PATH):
    return os.path.join(os.path.dirname(path), "secrets.bin")


def load_secrets(path=CONFIG_PATH):
    try:
        with open(secrets_path(path), "rb") as f:
            return json.loads(unprotect(f.read()).decode("utf-8"))
    except Exception:
        return {}     # нет файла (старая версия держала секреты в config.json) или его нельзя расшифровать


# ---------- DPAPI: шифрование данными входа текущего пользователя Windows ----------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, encrypt):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):  # UI_FORBIDDEN
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect(data):
    if sys.platform == "win32":
        return b"DPAPI1" + _dpapi(data, True)
    return b"PLAIN1" + base64.b64encode(data)   # не Windows (разработка, тесты)


def unprotect(blob):
    if blob.startswith(b"DPAPI1"):
        return _dpapi(blob[6:], False)
    if blob.startswith(b"PLAIN1"):
        return base64.b64decode(blob[6:])
    raise ValueError("unknown secrets format")
