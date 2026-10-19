"""Управление макросом из Telegram: кнопки «включить экран», «скриншот», «статус», «стоп».

Бот опрашивается long polling'ом (getUpdates) в фоновом потоке. Команды принимаются
только из чата telegram_chat_id — чужие сообщения игнорируются.
Команды не выполняются здесь, а кладутся в очередь окна (on_command), как горячие клавиши.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import alarm

BUTTONS = [
    [{"text": "🔆 Включить экран", "callback_data": "screen_on"},
     {"text": "🌑 Выключить экран", "callback_data": "screen_off"}],
    [{"text": "📸 Скриншот", "callback_data": "shot"},
     {"text": "📊 Статус", "callback_data": "status"}],
    [{"text": "🐟 Улов", "callback_data": "catches"},
     {"text": "⏹ Остановить макрос", "callback_data": "stop"}],
]
COMMANDS = {"/menu": "menu", "/start": "menu", "/panel": "panel", "/screen_on": "screen_on", "/screen_off": "screen_off",
            "/shot": "shot", "/status": "status", "/stop": "stop", "/catches": "catches",
            "/reset_catches": "reset_catches"}


API = "https://api.telegram.org"


TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")


def clean_token(value):
    """Токен из того, что вставили: с пробелами, переносом строки, «bot» в начале или целой ссылкой."""
    text = str(value or "").strip()
    m = TOKEN_RE.search(text)
    return m.group(0) if m else text


def clean_chat(value):
    return re.sub(r"[^\d-]", "", str(value or ""))


class TelegramError(Exception):
    def __init__(self, code, description):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def _call(token, method, params=None, timeout=15):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Telegram объясняет ошибку в теле ответа — достаём её
        try:
            body = json.loads(exc.read())
            raise TelegramError(body.get("error_code", exc.code), body.get("description", "")) from None
        except (ValueError, AttributeError):
            raise TelegramError(exc.code, str(exc.reason)) from None


def explain(exc):
    """Понятная причина ошибки Telegram и что сделать."""
    if isinstance(exc, urllib.error.HTTPError):    # из alarm._post: тело ответа ещё не прочитано
        try:
            body = json.loads(exc.read())
            exc = TelegramError(body.get("error_code", exc.code), body.get("description", ""))
        except Exception:
            exc = TelegramError(exc.code, str(exc.reason))
    if isinstance(exc, TelegramError):
        d = exc.description.lower()
        if exc.code == 401 or "unauthorized" in d:
            return "неверный токен бота — скопируйте его заново у @BotFather (целиком, вида 123456:ABC…)"
        if exc.code == 409:
            return ("этот бот уже запущен в другой программе или на другом ПК (у каждого должен быть "
                    "свой бот из @BotFather)")
        if "chat not found" in d:
            return ("chat not found — неверный chat id или вы ещё ни разу не писали боту: откройте бота, "
                    "нажмите «Start» и затем «Узнать chat id»")
        if exc.code == 403 or "blocked" in d or "can't initiate" in d:
            return "бот не может написать вам: откройте бота в Telegram и нажмите «Start» (или разблокируйте его)"
        if exc.code == 400:
            return f"Telegram отклонил запрос: {exc.description}"
        return str(exc)
    text = str(exc)
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return (f"нет связи с api.telegram.org ({text}) — проверьте интернет; если Telegram у провайдера "
                "заблокирован, включите VPN на ПК")
    return text


def find_chats(token):
    """Чаты, которые писали боту (для «Узнать chat id»): [(chat_id, имя)]."""
    try:
        _call(token, "deleteWebhook")    # с вебхуком getUpdates не работает
    except Exception:
        pass
    updates = _call(token, "getUpdates", {"timeout": 0}).get("result") or []
    chats = {}
    for upd in updates:
        msg = upd.get("message") or (upd.get("callback_query") or {}).get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id"):
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username")
            chats[str(chat["id"])] = name or str(chat["id"])
    return list(chats.items())


def diagnose(cfg):
    """Проверка Telegram по шагам. Возвращает (всё ок, [строки отчёта])."""
    token = clean_token(cfg.get("telegram_token"))
    chat = clean_chat(cfg.get("telegram_chat_id"))
    out = []
    if not token:
        return False, ["Токен бота не заполнен: создайте бота у @BotFather и вставьте токен"]
    if not TOKEN_RE.fullmatch(token):
        out.append("Токен не похож на токен бота (должен быть вида 123456789:AAE…), проверяю всё равно")
    try:
        me = _call(token, "getMe").get("result") or {}
        out.append(f"✓ Токен верный, бот @{me.get('username')}")
    except Exception as exc:
        return False, out + ["✗ " + explain(exc)]
    if not chat:
        return False, out + ["✗ Chat id не заполнен: напишите боту /start и нажмите «Узнать chat id»"]
    if chat == token.split(":")[0]:
        return False, out + ["✗ В chat id вписан id самого бота. Нужен ваш id: напишите боту /start "
                             "и нажмите «Узнать chat id»"]
    try:
        _call(token, "sendMessage", {"chat_id": chat, "text": "✅ Проверка: макрос может писать вам в Telegram"})
        out.append(f"✓ Сообщение отправлено в чат {chat}")
    except Exception as exc:
        return False, out + ["✗ " + explain(exc)]
    if not cfg.get("tg_control"):
        out.append("Кнопки управления выключены («Кнопки управления в Telegram») — тревоги придут, "
                   "но бот не будет отвечать на кнопки")
    return True, out


class TelegramControl:
    def __init__(self, cfg, on_command, log, webapp_link=None):
        self.cfg = cfg
        self.on_command = on_command
        self.log = log
        self.webapp_link = webapp_link  # функция → ссылка на мини-приложение или None
        self._menu_link = None
        self._offset = None
        self._token = None
        self._stop = threading.Event()
        self._failing = None      # текст последней ошибки — пишем в лог один раз
        self.seen_chats = {}      # кто писал боту, пока работает опрос: для «Узнать chat id»
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _creds(self):
        token = clean_token(self.cfg.get("telegram_token"))
        chat = clean_chat(self.cfg.get("telegram_chat_id"))
        if not (self.cfg.get("tg_control") and token and chat):
            return None, None
        return token, chat

    def send(self, text, buttons=True):
        """Не блокирует: отправка идёт в отдельном потоке (вызывается из окна)."""
        if self._creds()[0]:
            threading.Thread(target=self._send, args=(text, buttons), daemon=True).start()

    def _send(self, text, buttons):
        token, chat = self._creds()
        if not token:
            return
        params = {"chat_id": chat, "text": text}
        if buttons:
            rows = list(BUTTONS)
            link = self._link()
            if link:
                rows = [[{"text": "📱 Открыть панель", "web_app": {"url": link}}]] + rows
            params["reply_markup"] = json.dumps({"inline_keyboard": rows})
        try:
            _call(token, "sendMessage", params)
        except Exception as exc:
            self.log(f"Telegram: не удалось отправить сообщение: {explain(exc)}")

    def _link(self):
        try:
            return self.webapp_link() if self.webapp_link else None
        except Exception:
            return None

    def refresh_menu_button(self):
        """Сбросить кнопку «Панель» у поля ввода (например, после смены секретной ссылки)."""
        self._menu_link = None

    def _update_menu_button(self, token, chat):
        """Постоянная кнопка «Панель» слева от поля ввода в чате с ботом."""
        link = self._link()
        if link == self._menu_link:
            return
        button = ({"type": "web_app", "text": "Панель", "web_app": {"url": link}} if link
                  else {"type": "commands"})
        _call(token, "setChatMenuButton", {"chat_id": chat, "menu_button": json.dumps(button)})
        self._menu_link = link

    def send_photo(self, png, caption):
        token, chat = self._creds()
        if token:
            try:
                alarm.send_telegram(token, chat, caption, png)
            except Exception as exc:
                self.log(f"Telegram: не удалось отправить скриншот: {explain(exc)}")

    def send_media(self, data, kind, caption):
        """Картинка (png) или анимация (gif) с подписью."""
        token, chat = self._creds()
        if not token:
            return
        if kind != "gif":
            self.send_photo(data, caption)
            return
        try:
            body, ctype = alarm._multipart({"chat_id": chat, "caption": caption[:1000]},
                                           {"animation": ("catch.gif", data, "image/gif")})
            alarm._post(f"{API}/bot{token}/sendAnimation", body, {"Content-Type": ctype})
        except Exception as exc:
            self.log(f"Telegram: не удалось отправить анимацию: {explain(exc)}")

    def _loop(self):
        while not self._stop.is_set():
            token, chat = self._creds()
            if not token:
                self._token = None
                self._stop.wait(3)
                continue
            try:
                if token != self._token:
                    # Новый токен: пропускаем всё, что пришло боту до запуска макроса
                    old = _call(token, "getUpdates", {"offset": -1, "timeout": 0})
                    results = old.get("result") or []
                    self._offset = results[-1]["update_id"] + 1 if results else None
                    self._token = token
                    self._menu_link = None
                self._update_menu_button(token, chat)
                params = {"timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])}
                if self._offset is not None:
                    params["offset"] = self._offset
                updates = _call(token, "getUpdates", params, timeout=35).get("result") or []
                for upd in updates:
                    self._offset = upd["update_id"] + 1
                    self._handle(token, chat, upd)
                if self._failing:
                    self.log("Telegram: бот снова на связи")
                    self._failing = None
            except Exception as exc:
                reason = explain(exc)
                if reason != self._failing:   # раньше ошибки бота терялись молча
                    self.log(f"Telegram: бот не работает — {reason}")
                    self._failing = reason
                self._stop.wait(5)  # нет сети или неверный токен — пробуем позже

    @property
    def polling(self):
        """Опрос getUpdates уже идёт: второй параллельный запрос Telegram отклонит (409)."""
        return self._token is not None

    def _handle(self, token, chat, upd):
        src = (upd.get("message") or (upd.get("callback_query") or {}).get("message") or {}).get("chat") or {}
        if src.get("type") == "private" and src.get("id"):
            name = " ".join(x for x in (src.get("first_name"), src.get("last_name")) if x)
            self.seen_chats[str(src["id"])] = name or src.get("username") or str(src["id"])
        cb = upd.get("callback_query")
        if cb:
            if str(cb.get("message", {}).get("chat", {}).get("id")) != chat:
                return
            try:
                _call(token, "answerCallbackQuery", {"callback_query_id": cb["id"]})
            except Exception:
                pass
            self.on_command(cb.get("data"))
            return
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != chat:
            return
        text = (msg.get("text") or "").split("@")[0].strip().lower()
        command = COMMANDS.get(text)
        if command:
            self.on_command(command)


def status_text(engine, blackout_active):
    s = engine.stats
    started = s.get("started")
    runtime = int(time.time() - started) if started and engine.running else 0
    h, rem = divmod(runtime, 3600)
    state = "работает" if engine.running else "остановлен"
    screen = "затемнён" if blackout_active else "включён"
    return (f"Макрос {state}, экран {screen}\n"
            f"Рыбы: {s.get('catches', 0)}, забросов: {s.get('casts', 0)}\n"
            f"Время: {h}:{rem // 60:02d}:{rem % 60:02d}")
