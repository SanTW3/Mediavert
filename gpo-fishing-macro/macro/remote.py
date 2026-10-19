"""Связь с мини-приложением Telegram через ntfy, без своего сервера.

Два топика с секретным именем (config["remote_secret"]):
  gpo-<secret>-status — ПК публикует сюда JSON со статусом, мини-приложение его слушает;
  gpo-<secret>-cmd    — мини-приложение шлёт сюда команды (start, stop, screen_on, …), ПК их слушает.
Кто знает секрет, тот видит статус и управляет макросом, поэтому он длинный и случайный
и передаётся только в ссылке на мини-приложение в вашем личном чате с ботом.
"""
import json
import threading
import time
import urllib.parse
import urllib.request

COMMANDS = {"start", "stop", "screen_on", "screen_off", "shot", "alarm_off", "status", "catches"}
STATUS_EVERY = 30.0     # даже без изменений — «я жив» раз в 30 с
MIN_INTERVAL = 10.0     # не чаще раза в 10 с: у ntfy.sh лимит ~12 запросов в минуту, при превышении — HTTP 429
COMMAND_MAX_AGE = 60    # команды старше минуты (пришли, пока ПК был выключен) игнорируем


def topics(secret):
    return f"gpo-{secret}-status", f"gpo-{secret}-cmd"


def webapp_link(cfg):
    """Ссылка на мини-приложение. Секрет и сервер — во фрагменте (#), он не уходит на GitHub."""
    base = str(cfg.get("webapp_url") or "").strip()
    frag = urllib.parse.urlencode({"t": cfg["remote_secret"], "s": cfg.get("ntfy_server") or "https://ntfy.sh"})
    return f"{base}#{frag}"


class Remote:
    def __init__(self, cfg, get_status, on_command, log):
        self.cfg = cfg
        self.get_status = get_status
        self.on_command = on_command
        self.log = log
        self._stop = threading.Event()
        self._kick = threading.Event()
        self._last_sent = None
        self._last_time = -MIN_INTERVAL
        self._failing = False
        threading.Thread(target=self._publish_loop, daemon=True).start()
        threading.Thread(target=self._command_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._kick.set()

    def poke(self):
        """Статус изменился — опубликовать поскорее."""
        self._kick.set()

    def _server(self):
        return str(self.cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")

    def _enabled(self):
        return bool(self.cfg.get("remote_enabled") and self.cfg.get("remote_secret"))

    # ---------- статус: ПК → телефон ----------
    def _publish_loop(self):
        while not self._stop.is_set():
            self._kick.wait(2.0)
            self._kick.clear()
            if not self._enabled():
                continue
            try:
                status = self.get_status()
            except Exception:
                continue
            now = time.monotonic()
            # Время работы панель досчитывает сама — его изменение не повод слать статус
            key = {k: v for k, v in status.items() if k != "runtime"}
            changed = key != self._last_sent
            due = now - self._last_time >= STATUS_EVERY
            if not (changed or due) or now - self._last_time < MIN_INTERVAL:
                continue
            body = dict(status, ts=int(time.time()))
            topic = topics(self.cfg["remote_secret"])[0]
            try:
                req = urllib.request.Request(
                    f"{self._server()}/{topic}", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={"Cache": "yes"}, method="POST")
                urllib.request.urlopen(req, timeout=15).read()
                self._last_sent = key
                self._last_time = now
                if self._failing:
                    self.log("Мини-приложение: статус снова отправляется")
                    self._failing = False
            except Exception as exc:
                self._last_time = now  # нет сети — следующая попытка не раньше MIN_INTERVAL
                if not self._failing:   # пишем один раз, а не каждые 10 секунд
                    self.log(f"Мини-приложение: не удалось отправить статус в ntfy ({exc}) — "
                             "на телефоне будет «ПК не в сети»")
                    self._failing = True

    # ---------- команды: телефон → ПК ----------
    def _command_loop(self):
        while not self._stop.is_set():
            if not self._enabled():
                self._stop.wait(3)
                continue
            secret = self.cfg["remote_secret"]
            topic = topics(secret)[1]
            url = f"{self._server()}/{topic}/json?since={int(time.time())}"
            try:
                # Поток JSON-строк; ntfy шлёт keepalive раз в ~45 с, так что 90 с тишины = обрыв
                with urllib.request.urlopen(url, timeout=90) as resp:
                    for line in resp:
                        if self._stop.is_set() or self.cfg.get("remote_secret") != secret:
                            break  # секрет сменили — переподключаемся к новому топику
                        self._handle_line(line)
            except Exception:
                self._stop.wait(5)

    def _handle_line(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if msg.get("event") != "message":
            return
        if time.time() - float(msg.get("time", 0)) > COMMAND_MAX_AGE:
            return
        cmd = str(msg.get("message", "")).strip()
        if cmd in COMMANDS:
            self.log(f"Мини-приложение: {cmd}")
            self.on_command(cmd)
