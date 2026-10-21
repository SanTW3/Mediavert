"""Связь с мини-приложением Telegram без своего сервера.

Основной канал — публичный MQTT-брокер (mqttlink.py): без лимита сообщений, статус подписан.
Запасной — ntfy (если брокер недоступен), про него ниже.

Два топика с секретным именем (config["remote_secret"]):
  gpo-<secret>-status — ПК публикует сюда JSON со статусом, мини-приложение его слушает;
  gpo-<secret>-cmd    — мини-приложение шлёт сюда команды (start, stop, screen_on, …), ПК их слушает.
Кто знает секрет, тот видит статус и управляет макросом, поэтому он длинный и случайный
и передаётся только в ссылке на мини-приложение в вашем личном чате с ботом.
"""
import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request

from .mqttlink import pack_status, unpack_command
from .i18n import tr

COMMANDS = {"start", "stop", "screen_on", "screen_off", "shot", "alarm_off", "status", "catches", "reset_catches"}
# У бесплатного ntfy.sh лимит ~250 сообщений в сутки с одного IP. Раньше статус уходил каждые 10–30 с —
# за час-полтора лимит кончался (HTTP 429), панель показывала «ПК не в сети», а заодно переставали
# доходить тревоги ntfy. Теперь статус шлём, только когда панель открыта (она присылает «ping»),
# и при важных событиях: старт/стоп, тревога, экран.
WATCH_FOR = 90.0        # после «ping» панель считаем открытой столько секунд
WATCH_INTERVAL = 15.0   # пока панель открыта — не чаще раза в 15 с
MIN_INTERVAL = 10.0     # важные события — не чаще раза в 10 с
COMMAND_MAX_AGE = 60    # команды старше минуты (пришли, пока ПК был выключен) игнорируем
IMPORTANT = ("running", "alarm", "blackout", "fish_log", "lang")
MQTT_MIN_INTERVAL = 2.0  # MQTT: статус при изменениях, но не чаще раза в 2 с
MQTT_EVERY = 30.0        # и раз в 30 с — «я жив»


def topics(secret):
    """Темы ntfy — хэш секрета: сам секрет по сети и в журналы сервера ntfy не попадает."""
    h = hashlib.sha256(("gpo-ntfy:" + secret).encode()).hexdigest()[:32]
    return f"gpo-{h}-s", f"gpo-{h}-c"


def webapp_link(cfg):
    """Ссылка на мини-приложение. Секрет и сервер — во фрагменте (#), он не уходит на GitHub."""
    base = str(cfg.get("webapp_url") or "").strip()
    frag = urllib.parse.urlencode({"t": cfg["remote_secret"], "s": cfg.get("ntfy_server") or "https://ntfy.sh",
                                   "l": cfg.get("language") or "en",
                                   "m": cfg.get("mqtt_url") or ""})
    if base:
        # Версия в адресе: Telegram кэширует страницу, а с новым адресом откроет свежую
        from . import __version__
        base += ("&" if "?" in base else "?") + "v=" + __version__
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
        self._watch_until = 0.0
        self._ping = False
        self._failing = False
        self.last_ok = None       # time.time() последней удачной отправки статуса
        self.last_error = None
        self._mq_last = None
        self._mq_time = -MQTT_EVERY
        self._mq_force = False
        self._seen = {}           # одноразовые номера команд — одна команда не выполнится дважды
        self._started = time.time()
        self.mqtt = None          # подключаемся к брокерам, только когда панель включена (см. _sync_mqtt)
        threading.Thread(target=self._publish_loop, daemon=True).start()
        threading.Thread(target=self._command_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._kick.set()
        if self.mqtt:
            self.mqtt.stop()

    def _sync_mqtt(self):
        """Брокеры — только когда панель нужна: без настроенного Telegram открыть её всё равно нечем,
        и тогда программа вообще не ходит на публичные серверы."""
        want = self._enabled() and bool(self.cfg.get("mqtt_url"))
        if want and self.mqtt is None:
            from .mqttlink import MqttGroup
            self.mqtt = MqttGroup(self.cfg, self._mqtt_command, self.log, seen=self._seen,
                                  not_before=self._started)
        elif not want and self.mqtt is not None:
            self.mqtt.stop()
            self.mqtt = None

    def _mqtt_command(self, cmd):
        if cmd in ("_connected", "ping"):
            # Подключились к брокеру или панель открыли — сразу свежий статус
            self._mq_force = True
            self._kick.set()
        elif cmd in COMMANDS and self._enabled():
            self.log(tr("Мини-приложение: {cmd}", cmd=cmd))
            self.on_command(cmd)

    def poke(self):
        """Статус изменился — опубликовать поскорее."""
        self._kick.set()

    def _server(self):
        return str(self.cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")

    def _enabled(self):
        telegram = str(self.cfg.get("telegram_token") or "").strip() and str(self.cfg.get("telegram_chat_id") or "").strip()
        return bool(self.cfg.get("remote_enabled") and self.cfg.get("remote_secret") and telegram)

    def diagnostics(self):
        """Строка для статуса в Telegram: дошёл ли статус до панели."""
        if not self._enabled():
            return tr("Панель: выключена")
        if self.mqtt and self.mqtt.connected:
            return tr("Панель: на связи через ") + ", ".join(self.mqtt.hosts())
        if self.mqtt and self.mqtt.error:
            return tr("Панель: MQTT-брокер недоступен ({error}), запасной канал ntfy", error=self.mqtt.error)
        if self._failing and self.last_error:
            return tr("Панель: статус не отправляется ({last_error})", last_error=self.last_error)
        if self.last_ok:
            return tr("Панель: статус отправлен {v0} с назад", v0=int(time.time() - self.last_ok))
        return tr("Панель: ещё не открывали")

    # ---------- статус: ПК → телефон ----------
    def _publish_loop(self):
        while not self._stop.is_set():
            self._kick.wait(2.0)
            self._kick.clear()
            self._sync_mqtt()
            if not self._enabled():
                continue
            try:
                status = self.get_status()
            except Exception:
                continue
            now = time.monotonic()
            # Время работы панель досчитывает сама — его изменение не повод слать статус
            key = {k: v for k, v in status.items() if k != "runtime"}
            mq = self.mqtt is not None and self.mqtt.connected
            if mq:
                since_mq = now - self._mq_time
                if (self._mq_force or since_mq >= MQTT_EVERY
                        or (key != self._mq_last and since_mq >= MQTT_MIN_INTERVAL)):
                    self._mq_force = False
                    try:
                        if self.mqtt.publish(dict(status, ts=int(time.time()))):
                            self._mq_last = key
                            self._mq_time = now
                            self.last_ok = time.time()
                    except Exception as exc:
                        self.last_error = f"MQTT: {exc}"
                # Брокер на связи — ntfy (с его лимитом) только если панель сама попросила через него
                if not self._ping:
                    continue
            last = self._last_sent or {}
            since = now - self._last_time
            watching = now < self._watch_until
            important = any(key.get(k) != last.get(k) for k in IMPORTANT) and since >= MIN_INTERVAL
            if self._ping and since >= WATCH_INTERVAL:
                due = True                      # панель только что открыли или она жива
            elif watching and key != last and since >= WATCH_INTERVAL:
                due = True
            else:
                due = important
            if not due:
                continue
            self._ping = False
            body = dict(status, ts=int(time.time()))
            secret = self.cfg["remote_secret"]
            topic = topics(secret)[0]
            try:
                req = urllib.request.Request(
                    f"{self._server()}/{topic}", data=pack_status(secret, body),
                    headers={"Cache": "yes"}, method="POST")
                urllib.request.urlopen(req, timeout=15).read()
                self._last_sent = key
                self._last_time = now
                self.last_ok = time.time()
                if self._failing:
                    self.log(tr("Мини-приложение: статус снова отправляется"))
                    self._failing = False
            except Exception as exc:
                self._last_time = now  # нет сети — следующая попытка не раньше MIN_INTERVAL
                self._last_sent = key  # не повторять то же событие в цикле
                code = getattr(exc, "code", None)
                self.last_error = (tr("лимит сообщений ntfy.sh на сегодня исчерпан") if code == 429 else str(exc))
                if not self._failing:   # пишем один раз, а не каждые 10 секунд
                    self.log(tr("Мини-приложение: не удалось отправить статус в ntfy ({last_error}) — на телефоне будет «ПК не в сети»", last_error=self.last_error))
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
        # Команда подписана секретом (как в MQTT): чужую не примем, перехваченную не повторим
        cmd = unpack_command(self.cfg.get("remote_secret") or "", str(msg.get("message", "")), self._seen,
                             not_before=self._started)
        if not cmd:
            return
        if cmd == "ping":
            # Панель открыта: прислать ей свежий статус и держать его свежим, пока она пингует
            self._watch_until = time.monotonic() + WATCH_FOR
            self._ping = True
            self._kick.set()
            return
        if cmd in COMMANDS:
            self.log(tr("Мини-приложение: {cmd}", cmd=cmd))
            self.on_command(cmd)
