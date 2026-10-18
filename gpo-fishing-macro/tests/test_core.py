import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from macro import alarm, config  # noqa: E402
from macro.controller import PDController  # noqa: E402
from macro.detector import detect  # noqa: E402
from macro.watchdog import Watchdog  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "minigame.png")


def synthetic(zone=(120, 200), fish=60, h=300, w=80):
    """Синий трек 10..290, тёмно-серая полоса игрока zone, белая черта рыбы fish."""
    img = np.full((h, w, 3), (60, 30, 20), np.uint8)
    img[10:290, 30:42] = (85, 170, 255)
    img[zone[0]:zone[1], 30:42] = (26, 26, 28)
    if fish is not None:
        img[fish:fish + 2, 28:44] = (232, 232, 232)
    img[150:250, 60:66] = (173, 254, 128)  # зелёная полоса прогресса — должна игнорироваться
    return img


def test_detect_real_screenshot():
    rgb = np.asarray(Image.open(FIXTURE).convert("RGB"))
    det = detect(rgb)
    assert det is not None
    # Тёмно-серая полоса игрока на y 222–283 экрана, рыба (белая черта, y 234) внутри неё
    assert abs(det.zone_top - 18) <= 3 and abs(det.zone_bottom - 79) <= 3
    assert det.fish_y is not None and abs(det.fish_y - 30.5) <= 2
    blue = detect(rgb, bar_mode="blue")
    assert abs(blue.zone_top - 84) <= 3 and abs(blue.zone_bottom - 259) <= 3


def test_detect_synthetic_and_fish_inside_zone():
    det = detect(synthetic())
    assert (det.zone_top, det.zone_bottom) == (120, 199)
    assert det.fish_y == 60.5
    assert (det.bar_top, det.bar_bottom) == (10, 289)
    det = detect(synthetic(zone=(100, 200), fish=150))  # белая черта рвёт полосу — склеиваем
    assert (det.zone_top, det.zone_bottom) == (100, 199)
    assert det.fish_y == 150.5
    # Без склейки (gap_mult=0) разрыв в 2 px всё ещё закрывается минимальным допуском
    det = detect(synthetic(zone=(100, 200), fish=150), gap_mult=0)
    assert (det.zone_top, det.zone_bottom) == (100, 199)


def test_detect_nothing():
    assert detect(np.full((200, 200, 3), 30, np.uint8)) is None
    # Синяя вода на всю область — не мини-игра
    water = np.full((200, 200, 3), (85, 170, 255), np.uint8)
    water[50:90, :] = (26, 26, 28)
    assert detect(water) is None
    # Короткий синий обрывок в углу — тоже нет
    junk = np.full((300, 80, 3), (60, 30, 20), np.uint8)
    junk[10:60, 30:42] = (85, 170, 255)
    junk[20:40, 30:42] = (26, 26, 28)
    assert detect(junk) is None
    # Без белой черты рыбы мини-игру не начинаем, но внутри мини-игры рыбу можно потерять на кадр
    assert detect(synthetic(fish=None)) is None
    det = detect(synthetic(fish=None), require_fish=False)
    assert det is not None and det.fish_y is None


def test_detect_rejects_blue_glow_without_progress_bar():
    # Как на превью из игры: синее свечение у края области, без зелёной полосы прогресса
    img = synthetic()
    img[:, 60:66] = (60, 30, 20)  # убрали зелёную полосу
    assert detect(img) is None
    assert detect(img, require_progress=False) is not None


def test_pd_holds_when_fish_above():
    ctrl = PDController(0.9, 0.3, 1.0)
    holds = sum(ctrl.update(250, 50, 300, 1 / 60) for _ in range(60))
    assert holds > 45
    ctrl.reset()
    holds = sum(ctrl.update(50, 250, 300, 1 / 60) for _ in range(60))
    assert holds < 15


def test_pd_no_click_spam():
    # Рыба стоит выше полосы: кнопка держится непрерывно, без нажатий-отпусканий
    ctrl = PDController(0.9, 0.3, 1.0)
    states = [ctrl.update(250, 100, 300, 1 / 60) for _ in range(60)]
    assert all(states)
    ctrl.reset()
    assert not ctrl.update(150, 150, 300, 1 / 60)  # рыба в центре — отпускаем


def test_chase_damping_scales_kd():
    # Рыба уходит от полосы (ошибка растёт): работает только chase_damping
    off = PDController(0.9, 0.3, 1.0, approach_damping=5.0, chase_damping=0.0)
    no_kd = PDController(0.9, 0.0, 1.0)
    seq = [(250, 200 - i * 2) for i in range(60)]
    assert [off.update(z, f, 300, 1 / 60) for z, f in seq] == [no_kd.update(z, f, 300, 1 / 60) for z, f in seq]


def test_state_resend(monkeypatch):
    from macro import engine as eng
    sent = []
    monkeypatch.setattr(eng.winput, "mouse_down", lambda: sent.append("down"))
    monkeypatch.setattr(eng.winput, "mouse_up", lambda: sent.append("up"))
    now = [100.0]
    monkeypatch.setattr(eng.time, "monotonic", lambda: now[0])
    e = eng.MacroEngine(dict(config.DEFAULTS), log=lambda t: None)
    e._set_mouse(True, 0.5)
    e._set_mouse(True, 0.5)
    assert sent == ["down"]
    now[0] += 0.6
    e._set_mouse(True, 0.5)
    assert sent == ["down", "down"]
    e._set_mouse(False, 0.5)
    now[0] += 0.6
    e._set_mouse(False, 0.5)
    assert sent == ["down", "down", "up", "up"]


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


def test_black_fraction():
    from macro.detector import black_fraction
    img = np.zeros((10, 10, 3), np.uint8)
    img[:3] = 200
    assert black_fraction(img) == 0.7


def test_approach_damping_brakes():
    plain = PDController(0.9, 0.3, 1.0, approach_damping=1.0)
    damped = PDController(0.9, 0.3, 1.0, approach_damping=3.0)
    # Зона быстро догоняет рыбу сверху: ошибка падает — демпфированный жмёт реже
    holds = []
    for ctrl in (plain, damped):
        n = 0
        for i in range(60):
            n += ctrl.update(250 - i * 3, 50, 300, 1 / 60)
        holds.append(n)
    assert holds[1] < holds[0]


def test_engine_full_loop(monkeypatch):
    from macro import engine as eng
    actions = []
    frames = {"n": 0}
    monkeypatch.setattr(eng.winput, "press_key", lambda k: actions.append(("key", k)))
    monkeypatch.setattr(eng.winput, "click", lambda x, y: actions.append(("click", (x, y))))
    def move(x, y):
        actions.append(("move", (x, y)))
        frames["n"] = 0  # новый заброс — мини-игра начнётся заново
    monkeypatch.setattr(eng.winput, "move_to", move)
    monkeypatch.setattr(eng.winput, "mouse_down", lambda: actions.append(("down",)))
    monkeypatch.setattr(eng.winput, "mouse_up", lambda: actions.append(("up",)))
    monkeypatch.setattr(eng.winput, "type_text", lambda t: actions.append(("type", str(t))))
    monkeypatch.setattr(eng.winput, "focus_roblox", lambda: True)
    monkeypatch.setattr(eng.screen, "grab_monitor", lambda: np.full((10, 10, 3), 100, np.uint8))

    def grab(area):
        # После каждого заброса: 30 кадров мини-игры, затем пусто
        frames["n"] += 1
        return synthetic() if frames["n"] < 30 else np.full((300, 80, 3), 24, np.uint8)
    monkeypatch.setattr(eng.screen, "grab", grab)

    fast = {k: 0.0 for k in ("focus_delay", "post_focus_delay", "buy_e_delay", "buy_click_delay",
                             "buy_type_delay", "fruit_hotkey_delay", "fruit_store_delay", "fruit_shift_delay",
                             "fruit_backspace_delay", "bait_click_delay", "rod_select_delay", "cursor_delay",
                             "fish_end_delay", "cast_hold")}
    cfg = dict(config.DEFAULTS, **fast, area=[0, 0, 80, 300], water_point=[500, 400], scan_loop_delay=0.001,
               scan_fps=240, auto_buy_bait=True, buy_left=[1, 1], buy_middle=[2, 2], buy_right=[3, 3],
               loops_per_purchase=5, auto_store_fruit=True, store_point=[9, 9], wd_check_window=False,
               alarm_enabled=False)
    e = eng.MacroEngine(cfg, log=lambda t: None)
    e.start()
    deadline = time.time() + 10
    while e.stats["catches"] < 2 and time.time() < deadline:
        time.sleep(0.02)
    e.stop()
    e._thread.join(2)
    assert e.stats["catches"] >= 2
    keys = [a[1] for a in actions if a[0] == "key"]
    assert keys[:3] == ["2", "e", "2"] and "7" in keys and "backspace" in keys
    assert ("type", "5") in actions and ("click", (9, 9)) in actions
    # Покупка: E → левый → средний → число → левый → правый → средний
    i = actions.index(("key", "e"))
    assert actions[i + 1:i + 7] == [("click", (1, 1)), ("click", (2, 2)), ("type", "5"),
                                    ("click", (1, 1)), ("click", (3, 3)), ("click", (2, 2))]
    assert actions.count(("down",)) == actions.count(("up",))  # мышь никогда не остаётся зажатой


def test_telegram_control_accepts_only_own_chat():
    from urllib.parse import parse_qs
    from macro import tgcontrol
    sent = []
    updates = [
        {"update_id": 5, "callback_query": {"id": "a", "data": "screen_on", "message": {"chat": {"id": 111}}}},
        {"update_id": 6, "callback_query": {"id": "b", "data": "stop", "message": {"chat": {"id": 999}}}},
        {"update_id": 7, "message": {"chat": {"id": 111}, "text": "/shot"}},
        {"update_id": 8, "message": {"chat": {"id": 999}, "text": "/stop"}},
    ]
    state = {"served": False}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            method = self.path.rsplit("/", 1)[-1]
            params = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            if method == "getUpdates":
                if params.get("offset") == ["-1"]:
                    result = [{"update_id": 4}]  # старое сообщение до запуска — пропускаем
                elif not state["served"]:
                    state["served"] = True
                    result = updates
                else:
                    time.sleep(0.2)
                    result = []
            else:
                sent.append((method, params))
                result = True
            body = json.dumps({"ok": True, "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tgcontrol.API = f"http://127.0.0.1:{srv.server_port}"
    got = []
    cfg = dict(config.DEFAULTS, telegram_token="T", telegram_chat_id="111", tg_control=True)
    ctl = tgcontrol.TelegramControl(cfg, got.append, log=lambda t: None)
    deadline = time.time() + 5
    while len(got) < 2 and time.time() < deadline:
        time.sleep(0.05)
    ctl.stop()
    srv.shutdown()
    tgcontrol.API = "https://api.telegram.org"
    assert got == ["screen_on", "shot"]  # команды из чужого чата 999 проигнорированы
    assert [m for m, _ in sent] == ["answerCallbackQuery"]


def test_remote_publishes_status_and_takes_commands():
    from macro import remote
    posted, got = [], []
    now = time.time()
    stream = [
        {"event": "open"},
        {"event": "message", "time": now - 600, "message": "stop"},   # старая — игнор
        {"event": "message", "time": now, "message": "rm -rf"},       # не команда — игнор
        {"event": "message", "time": now, "message": "screen_on"},
    ]

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            posted.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            for m in stream:
                self.wfile.write((json.dumps(m) + "\n").encode())
            self.wfile.flush()
            time.sleep(1)

        def log_message(self, *a):
            pass

    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = dict(config.DEFAULTS, ntfy_server=f"http://127.0.0.1:{srv.server_port}", remote_secret="S3CRET")
    r = remote.Remote(cfg, lambda: {"running": True, "catches": 3}, got.append, log=lambda t: None)
    r.poke()
    deadline = time.time() + 5
    while (not posted or not got) and time.time() < deadline:
        time.sleep(0.05)
    r.stop()
    srv.shutdown()
    assert posted[0][0] == "/gpo-S3CRET-status"
    assert posted[0][1]["catches"] == 3 and posted[0][1]["running"] is True and "ts" in posted[0][1]
    assert got == ["screen_on"]
    link = remote.webapp_link(dict(cfg, webapp_url="https://santw3.github.io/gpo-macro/"))
    assert link.startswith("https://santw3.github.io/gpo-macro/#t=S3CRET&s=http")


def test_clean_name():
    from macro.catches import clean_name
    assert clean_name("You caught a Golden Carp! x1") == "Golden Carp"
    assert clean_name("+1 Salmon") == "Salmon"
    assert clean_name("   ") == ""


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def learned_store(tmp_path):
    """Выученные рыбы — во временную папку, а не в папку проекта."""
    from macro import fishdb
    fishdb.set_store_dir(str(tmp_path / "fish_learned"))
    yield
    fishdb.set_store_dir(None)


def _shot(fid, bg=(20, 30, 70), scale=1.0, text=None):
    from macro import fishdb
    ic = fishdb.icon(fid)
    ic = ic.resize((int(ic.width * scale), int(ic.height * scale)))
    img = Image.new("RGB", (ic.width + 120, ic.height + 20), bg)
    img.paste(ic, (10, 10), ic)
    return np.asarray(img)


def _new_fish_shot(color):
    """Уведомление с рыбой, которой нет в каталоге."""
    img = Image.new("RGB", (220, 70), (25, 30, 45))
    d = ImageDraw.Draw(img)
    d.ellipse([10, 15, 60, 55], fill=color)
    d.polygon([(58, 35), (75, 20), (75, 50)], fill=color)
    d.text((90, 28), "You caught", fill=(255, 255, 255))
    return np.asarray(img)


def test_catch_log_uses_catalog_and_renders():
    from macro.catches import CatchLog
    cfg = dict(config.DEFAULTS, fish_names={"04": "My Legend"})
    log = CatchLog(cfg, log=lambda t: None)
    log.add(_shot("04"), text="")
    log.add(_shot("04", bg=(0, 0, 0), scale=1.3), text="")
    log.add(_shot("07"), text="")
    by = {log.name_of(c): c for c in log.items}
    assert by["My Legend"].count == 2 and by["My Legend"].rarity == "legendary"
    assert by["Ghost Shark"].rarity == "epic"
    text = log.summary_text()
    assert text.splitlines()[2].startswith("🌈 My Legend × 2")      # легендарные — первыми
    data, kind = log.render()
    assert kind == "gif" and data[:3] == b"GIF"                      # радуга переливается
    data, kind = log.render(rows=[by["Ghost Shark"]])
    assert kind == "png" and data[:4] == b"\x89PNG"
    assert log.status()[0]["rarity"] == "legendary" and log.status()[0]["id"] == "04"


def test_names_come_from_game_notification():
    from macro import fishdb
    from macro.catches import CatchLog
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    # Иконка узнаётся, название берётся из уведомления и запоминается
    c = log.add(_shot("06"), text="You caught a Pumpkin King!")
    assert log.name_of(c) == "Pumpkin King" and not c.new
    assert fishdb.learned_name("06") == "Pumpkin King"
    # Дальше рыба узнаётся по названию
    assert log.add(_shot("06", scale=0.8), text="You caught a Pumpkin King!") is c and c.count == 2
    # Похоже на ту же иконку, но игра пишет другое название — это другая, новая рыба
    other = log.add(_shot("06"), text="You caught a Pumpkin Queen!")
    assert other is not c and other.new and log.name_of(other) == "Pumpkin Queen"


def test_unknown_fish_are_added_to_catalog():
    from macro import fishdb
    from macro.catches import CatchLog
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    eel = _new_fish_shot((40, 220, 90))
    c = log.add(eel, text="You caught a Mystery Eel!")
    assert c.new and c.fish_id.startswith("u") and log.name_of(c) == "Mystery Eel"
    assert c.rarity is None                                           # редкость выберет пользователь
    # Повторная поимка — уже из каталога: по названию и даже без него (по иконке)
    assert log.add(eel, text="You caught a Mystery Eel!") is c
    assert log.add(eel, text="") is c and c.count == 3 and not c.new
    # Без названия новая рыба тоже записывается
    blob = log.add(_new_fish_shot((250, 40, 200)), text="")
    assert blob.new and log.name_of(blob).startswith("Unknown Fish")
    # Каталог переживает перезапуск
    fishdb.set_store_dir(fishdb.store_dir())
    assert fishdb.by_id(c.fish_id)["name"] == "Mystery Eel"
    fishdb.set_rarity(c.fish_id, "epic")
    assert fishdb.by_id(c.fish_id)["rarity"] == "epic"
    assert "🟣 Mystery Eel × 3" in log.summary_text()


def test_skip_first_bait_purchase(monkeypatch):
    from macro import engine as eng
    for skip, expect_buy in ((False, True), (True, False)):
        keys = []
        monkeypatch.setattr(eng.winput, "press_key", lambda k: keys.append(k))
        monkeypatch.setattr(eng.winput, "click", lambda x, y: None)
        monkeypatch.setattr(eng.winput, "type_text", lambda t: None)
        monkeypatch.setattr(eng.screen, "grab_monitor", lambda: np.full((10, 10, 3), 100, np.uint8))
        fast = {k: 0.0 for k in ("buy_e_delay", "buy_click_delay", "buy_type_delay", "rod_select_delay")}
        cfg = dict(config.DEFAULTS, **fast, auto_buy_bait=True, buy_left=[1, 1], buy_middle=[2, 2],
                   buy_right=[3, 3], loops_per_purchase=5, buy_skip_first=skip, wd_check_window=False)
        e = eng.MacroEngine(cfg, log=lambda t: None)
        e._pre_cast()
        assert ("e" in keys) == expect_buy
        e.stats["loops"] = 5                                           # через «циклов на покупку» — покупает
        keys.clear()
        e._pre_cast()
        assert "e" in keys


def test_catalog_recognizes_inventory_icons():
    from macro import fishdb
    for f in fishdb.FISH:
        ic = fishdb.icon(f["id"])
        cell = Image.new("RGB", (ic.width + 4, ic.height + 4), (79, 79, 79))   # серая клетка инвентаря
        cell.paste(ic, (2, 2), ic)
        found, score = fishdb.match(np.asarray(cell))
        assert found and found["id"] == f["id"], (f["id"], found, score)


def test_windows_ocr_reads_text():
    """Настоящее распознавание — только на Windows (в CI на windows-latest)."""
    import pytest
    if sys.platform != "win32":
        pytest.skip("Windows OCR есть только на Windows")
    from PIL import ImageDraw, ImageFont
    from macro import ocr
    from macro.catches import clean_name
    if not ocr.available():
        pytest.skip("на этой Windows нет движка OCR")
    img = Image.new("RGB", (420, 60), (20, 30, 60))
    font = ImageFont.truetype("arialbd.ttf", 32)
    ImageDraw.Draw(img).text((10, 10), "You caught a Golden Carp!", font=font, fill=(255, 255, 255))
    text = ocr.read_text(np.asarray(img), raise_errors=True)
    print("OCR:", repr(text))
    assert clean_name(text) == "Golden Carp"
