"""Управление макросом из Telegram: кнопки «включить экран», «скриншот», «статус», «стоп».

Бот опрашивается long polling'ом (getUpdates) в фоновом потоке. Команды принимаются
только из чата telegram_chat_id — чужие сообщения игнорируются.
Команды не выполняются здесь, а кладутся в очередь окна (on_command), как горячие клавиши.
"""
import json
import threading
import time
import urllib.parse
import urllib.request

from . import alarm

BUTTONS = [
    [{"text": "🔆 Включить экран", "callback_data": "screen_on"},
     {"text": "🌑 Выключить экран", "callback_data": "screen_off"}],
    [{"text": "📸 Скриншот", "callback_data": "shot"},
     {"text": "📊 Статус", "callback_data": "status"}],
    [{"text": "⏹ Остановить макрос", "callback_data": "stop"}],
]
COMMANDS = {"/menu": "menu", "/start": "menu", "/screen_on": "screen_on", "/screen_off": "screen_off",
            "/shot": "shot", "/status": "status", "/stop": "stop"}


API = "https://api.telegram.org"


def _call(token, method, params=None, timeout=15):
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


class TelegramControl:
    def __init__(self, cfg, on_command, log):
        self.cfg = cfg
        self.on_command = on_command
        self.log = log
        self._offset = None
        self._token = None
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _creds(self):
        token = str(self.cfg.get("telegram_token") or "").strip()
        chat = str(self.cfg.get("telegram_chat_id") or "").strip()
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
            params["reply_markup"] = json.dumps({"inline_keyboard": BUTTONS})
        try:
            _call(token, "sendMessage", params)
        except Exception as exc:
            self.log(f"Telegram: не удалось отправить сообщение: {exc}")

    def send_photo(self, png, caption):
        token, chat = self._creds()
        if token:
            try:
                alarm.send_telegram(token, chat, caption, png)
            except Exception as exc:
                self.log(f"Telegram: не удалось отправить скриншот: {exc}")

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
                params = {"timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])}
                if self._offset is not None:
                    params["offset"] = self._offset
                updates = _call(token, "getUpdates", params, timeout=35).get("result") or []
                for upd in updates:
                    self._offset = upd["update_id"] + 1
                    self._handle(token, chat, upd)
            except Exception:
                self._stop.wait(5)  # нет сети или неверный токен — пробуем позже

    def _handle(self, token, chat, upd):
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
