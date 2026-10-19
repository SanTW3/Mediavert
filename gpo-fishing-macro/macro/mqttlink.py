"""Канал мини-приложения через публичный MQTT-брокер (по умолчанию broker.emqx.io).

У ntfy.sh лимит ~250 сообщений в сутки, и у некоторых провайдеров он недоступен, а у публичного
MQTT-брокера лимита на сообщения нет, и последний статус хранится на брокере (retained), так что
панель видит его сразу после открытия.

Публичный брокер видят все: любой может подписаться на «#». Поэтому:
  * имя топика — хэш секрета, сам секрет по сети не передаётся;
  * статус и команды подписаны HMAC-SHA256 с секретом: чужая команда без секрета не пройдёт;
  * у команды есть время и случайный nonce — перехваченную команду нельзя повторить.
"""
import hashlib
import hmac
import json
import os
import ssl
import threading
import time
import urllib.parse

DEFAULT_URL = "wss://broker.emqx.io:8084/mqtt"
# Два брокера сразу: если сеть телефона или ПК не пускает к одному, панель работает через другой
DEFAULT_URLS = "wss://broker.emqx.io:8084/mqtt wss://broker.hivemq.com:8884/mqtt"
COMMAND_MAX_AGE = 60     # с, расхождение часов ПК и телефона + задержка доставки


def base_topic(secret):
    return "gpo/" + hashlib.sha256(("gpo-topic:" + secret).encode()).hexdigest()[:40]


def sign(secret, text):
    return hmac.new(secret.encode(), text.encode("utf-8"), hashlib.sha256).hexdigest()


def pack_status(secret, status):
    data = json.dumps(status, ensure_ascii=False, separators=(",", ":"))
    return json.dumps({"d": data, "h": sign(secret, data)}, ensure_ascii=False).encode("utf-8")


def unpack_command(secret, payload, seen, now=None):
    """Команда из подписанного сообщения панели или None (подделка, повтор, старая)."""
    try:
        msg = json.loads(payload)
        cmd, t, nonce, h = str(msg["c"]), int(msg["t"]), str(msg["n"]), str(msg["h"])
    except (ValueError, KeyError, TypeError):
        return None
    if not hmac.compare_digest(sign(secret, f"{cmd}|{t}|{nonce}"), h):
        return None
    now = time.time() if now is None else now
    if abs(now - t / 1000) > COMMAND_MAX_AGE or nonce in seen:
        return None
    seen[nonce] = now
    for k in [k for k, v in seen.items() if now - v > 2 * COMMAND_MAX_AGE]:
        del seen[k]
    return cmd


def parse_url(url):
    """wss://host:port/path → (host, port, transport, tls, path)."""
    u = urllib.parse.urlparse(url or DEFAULT_URL)
    tls = u.scheme in ("wss", "mqtts", "ssl")
    transport = "websockets" if u.scheme in ("ws", "wss") else "tcp"
    default_port = {"wss": 443, "ws": 80, "mqtts": 8883, "ssl": 8883}.get(u.scheme, 1883)
    return u.hostname, u.port or default_port, transport, tls, u.path or "/mqtt"


class MqttLink:
    """Подключение к брокеру: публикует статус, принимает команды. Переподключается сам."""

    def __init__(self, cfg, on_command, log, url=None, seen=None):
        self.cfg = cfg
        self.on_command = on_command
        self.log = log
        self.url = url or DEFAULT_URL
        self.host = parse_url(self.url)[0]
        self.connected = False
        self.error = None
        self._seen = seen if seen is not None else {}
        self._topic = None
        self._client = None
        self._lock = threading.Lock()
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            self.error = f"нет paho-mqtt ({exc})"
            return
        host, port, transport, tls, path = parse_url(self.url)
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gpo-pc-" + os.urandom(6).hex(),
                        transport=transport)
        if transport == "websockets":
            c.ws_set_options(path=path)
        if tls:
            c.tls_set_context(ssl.create_default_context())
        c.reconnect_delay_set(2, 60)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self._client = c
        try:
            c.connect_async(host, port, keepalive=50)
            c.loop_start()
        except Exception as exc:
            self.error = str(exc)

    # ---------- события paho ----------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            self.error = f"брокер отказал: {reason_code}"
            return
        self.connected = True
        self.error = None
        with self._lock:
            self._topic = None
        self.resubscribe()
        self.on_command("_connected")

    def _on_disconnect(self, client, userdata, flags, reason_code=None, properties=None):
        if self.connected:
            self.log(f"Мини-приложение: связь с {self.host} потеряна, переподключаюсь…")
        self.connected = False

    def _on_message(self, client, userdata, msg):
        secret = self.cfg.get("remote_secret") or ""
        cmd = unpack_command(secret, msg.payload, self._seen)   # _seen общий у всех брокеров
        if cmd:
            self.on_command(cmd)

    # ---------- для Remote ----------
    def resubscribe(self):
        """Подписаться на команды текущего секрета (после подключения или смены секрета)."""
        secret = self.cfg.get("remote_secret") or ""
        topic = base_topic(secret) + "/c" if secret else None
        with self._lock:
            if not self.connected or topic == self._topic:
                return
            if self._topic:
                self._client.unsubscribe(self._topic)
            if topic:
                self._client.subscribe(topic, qos=1)
            self._topic = topic

    def publish(self, status):
        if not self.connected:
            return False
        self.resubscribe()
        secret = self.cfg.get("remote_secret") or ""
        info = self._client.publish(base_topic(secret) + "/s", pack_status(secret, status), qos=1, retain=True)
        return info.rc == 0

    def stop(self):
        if self._client is not None:
            self.connected = False      # остановка — не «потеря связи»
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:
                pass


def split_urls(value):
    return [u for u in str(value or "").replace(",", " ").split() if u]


class MqttGroup:
    """Несколько брокеров сразу: статус уходит во все, команда принимается из любого (один раз)."""

    def __init__(self, cfg, on_command, log):
        seen = {}
        self.links = [MqttLink(cfg, on_command, log, url, seen) for url in split_urls(cfg.get("mqtt_url"))]

    @property
    def connected(self):
        return any(link.connected for link in self.links)

    @property
    def error(self):
        errors = [f"{link.host}: {link.error}" for link in self.links if link.error]
        return "; ".join(errors) or None

    def hosts(self):
        return [link.host for link in self.links if link.connected]

    def publish(self, status):
        ok = False
        for link in self.links:
            try:
                ok = link.publish(status) or ok
            except Exception:
                pass
        return ok

    def stop(self):
        for link in self.links:
            link.stop()
