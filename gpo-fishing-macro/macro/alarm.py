"""Будильник на телефон: ntfy, Telegram, Pushover.

* ntfy (приложение ntfy для Android/iOS) — приоритет 5 (max). В приложении для такого
  приоритета можно включить «звук будильника» и обход режима «Не беспокоить».
* Telegram — сообщение от вашего бота (+ скриншот экрана).
* Pushover — приоритет 2 (emergency): телефон звонит каждые 30 секунд,
  пока вы не нажмёте «Acknowledge» — настоящий будильник.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .i18n import tr

TIMEOUT = 15


def _post(url, data, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content, ctype) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send_ntfy(server, topic, title, message):
    # JSON-публикация: заголовки HTTP не умеют в кириллицу, а JSON — да
    body = json.dumps({
        "topic": topic, "title": title, "message": message,
        "priority": 5, "tags": ["rotating_light", "fishing_pole_and_fish"],
    }).encode("utf-8")
    _post(server.rstrip("/"), body, {"Content-Type": "application/json"})


def send_telegram(token, chat_id, text, screenshot_png=None, reply_markup=None):
    base = f"https://api.telegram.org/bot{token}"
    if screenshot_png:
        fields = {"chat_id": chat_id, "caption": text[:1000]}
        if reply_markup:
            fields["reply_markup"] = reply_markup
        jpeg = screenshot_png[:2] == b"\xff\xd8"
        photo = ("screen.jpg", screenshot_png, "image/jpeg") if jpeg else ("screen.png", screenshot_png, "image/png")
        body, ctype = _multipart(fields, {"photo": photo})
        try:
            _post(base + "/sendPhoto", body, {"Content-Type": ctype})
            return
        except urllib.error.URLError:
            pass      # картинку не приняли (размер и т.п.) — пусть дойдёт хотя бы текст тревоги
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    _post(base + "/sendMessage", data)


def send_pushover(user, token, title, message):
    data = urllib.parse.urlencode({
        "token": token, "user": user, "title": title, "message": message,
        "priority": 2, "retry": 30, "expire": 3600, "sound": "persistent",
    }).encode()
    _post("https://api.pushover.net/1/messages.json", data)


def channels(cfg):
    """Список настроенных каналов (для подсказки в интерфейсе)."""
    result = []
    if cfg.get("ntfy_topic"):
        result.append("ntfy")
    if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        result.append("Telegram")
    if cfg.get("pushover_user") and cfg.get("pushover_token"):
        result.append("Pushover")
    return result


def send_alarm(cfg, reason, screenshot_png=None, log=print):
    """Шлёт тревогу во все настроенные каналы. Возвращает число успешных отправок."""
    title = tr("FishPilot: что-то пошло не так!")
    message = f"{reason}\n{time.strftime('%H:%M:%S')}"
    sent = 0
    jobs = []
    if cfg.get("ntfy_topic"):
        jobs.append(("ntfy", lambda: send_ntfy(cfg["ntfy_server"] or "https://ntfy.sh",
                                               cfg["ntfy_topic"], title, message)))
    if cfg.get("telegram_token") and cfg.get("telegram_chat_id"):
        from .tgcontrol import clean_chat, clean_token
        jobs.append(("Telegram", lambda: send_telegram(
            clean_token(cfg["telegram_token"]), clean_chat(cfg["telegram_chat_id"]), f"🚨 {title}\n{message}",
            screenshot_png if cfg.get("alarm_screenshot") else None)))
    if cfg.get("pushover_user") and cfg.get("pushover_token"):
        jobs.append(("Pushover", lambda: send_pushover(cfg["pushover_user"], cfg["pushover_token"],
                                                       title, message)))
    for name, job in jobs:
        try:
            job()
            sent += 1
            log(tr("Тревога отправлена: {name}", name=name))
        except Exception as exc:  # сеть, неверный токен и т.п. — не роняем макрос
            if name == "Telegram":
                from .tgcontrol import explain
                exc = explain(exc)
            log(tr("Не удалось отправить в {name}: {exc}", name=name, exc=exc))
    if not jobs:
        log(tr("Тревога не отправлена: не настроен ни один канал"))
    return sent
