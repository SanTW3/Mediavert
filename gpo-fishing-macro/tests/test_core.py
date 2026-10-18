import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macro import alarm, config  # noqa: E402
from macro.controller import PDController  # noqa: E402
from macro.detector import detect  # noqa: E402
from macro.watchdog import Watchdog  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "minigame.png")


def synthetic(zone=(120, 200), fish=60, h=300, w=80):
    img = np.full((h, w, 3), 24, np.uint8)
    img[:, 30:42] = (24, 24, 24)
    img[zone[0]:zone[1], 30:42] = (85, 170, 255)
    if fish is not None:
        img[fish:fish + 2, 28:44] = (232, 232, 232)
    img[150:250, 60:66] = (173, 254, 128)  # зелёная полоса прогресса — должна игнорироваться
    return img


def test_detect_real_screenshot():
    rgb = np.asarray(Image.open(FIXTURE).convert("RGB"))
    det = detect(rgb)
    assert det is not None
    assert abs(det.zone_top - 84) <= 3 and abs(det.zone_bottom - 259) <= 3
    assert det.fish_y is not None and abs(det.fish_y - 30.5) <= 2


def test_detect_synthetic_and_fish_inside_zone():
    det = detect(synthetic())
    assert (det.zone_top, det.zone_bottom) == (120, 199)
    assert det.fish_y == 60.5
    det = detect(synthetic(zone=(100, 200), fish=150))  # белая черта рвёт зону — склеиваем
    assert (det.zone_top, det.zone_bottom) == (100, 199)
    assert det.fish_y == 150.5


def test_detect_nothing():
    assert detect(np.full((200, 200, 3), 30, np.uint8)) is None
    det = detect(synthetic(fish=None))
    assert det is not None and det.fish_y is None


def test_pd_holds_when_fish_above():
    ctrl = PDController(0.9, 0.3, 1.0)
    holds = sum(ctrl.update(250, 50, 300, 1 / 60) for _ in range(60))
    assert holds > 45
    ctrl.reset()
    holds = sum(ctrl.update(50, 250, 300, 1 / 60) for _ in range(60))
    assert holds < 15


def test_pd_balanced_when_centered():
    ctrl = PDController(0.9, 0.3, 1.0)
    holds = sum(ctrl.update(150, 150, 300, 1 / 60) for _ in range(100))
    assert 45 <= holds <= 55


def test_watchdog():
    now = [0.0]
    cfg = dict(config.DEFAULTS, wd_failed_casts=3, wd_no_catch_min=1, wd_window_grace_sec=10)
    wd = Watchdog(cfg, clock=lambda: now[0])
    assert wd.check() is None
    wd.on_cast_failed(); wd.on_cast_failed()
    assert wd.check() is None
    wd.on_cast_failed()
    assert "забросов" in wd.check()
    wd.on_catch()
    assert wd.check() is None
    now[0] = 61
    assert "Ни одной рыбы" in wd.check()
    wd.on_catch()
    wd.on_window_state(False)
    now[0] = 65
    assert wd.check() is None
    now[0] = 72
    assert "Roblox" in wd.check()
    wd.on_window_state(True)
    assert wd.check() is None


def test_config_roundtrip(tmp_path):
    path = str(tmp_path / "config.json")
    cfg = config.load(path)
    assert cfg["kp"] == 0.9
    cfg["kp"] = 1.5
    cfg["area"] = [1, 2, 3, 4]
    config.save(cfg, path)
    loaded = config.load(path)
    assert loaded["kp"] == 1.5 and loaded["area"] == [1, 2, 3, 4]


def test_alarm_ntfy_payload(monkeypatch):
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    cfg = dict(config.DEFAULTS, ntfy_server=f"http://127.0.0.1:{srv.server_port}", ntfy_topic="my-fish")
    logs = []
    assert alarm.send_alarm(cfg, "Тест", log=logs.append) == 1
    assert got["body"]["topic"] == "my-fish" and got["body"]["priority"] == 5
    assert "Тест" in got["body"]["message"]


def test_alarm_no_channels():
    logs = []
    assert alarm.send_alarm(dict(config.DEFAULTS), "x", log=logs.append) == 0
    assert "не настроен" in logs[-1]
