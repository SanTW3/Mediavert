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
    cfg = dict(config.DEFAULTS, ntfy_server=f"http://127.0.0.1:{srv.server_port}", remote_secret="S3CRET", mqtt_url="")
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
    from macro import __version__
    assert link.startswith(f"https://santw3.github.io/gpo-macro/?v={__version__}#t=S3CRET&s=http")


def test_remote_saves_ntfy_quota(monkeypatch):
    """Без открытой панели статус уходит только при важных событиях (лимит ntfy.sh ~250 в сутки)."""
    from macro import remote
    monkeypatch.setattr(remote, "MIN_INTERVAL", 0.1)
    monkeypatch.setattr(remote, "WATCH_INTERVAL", 0.1)
    posted = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            posted.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            time.sleep(5)

        def log_message(self, *a):
            pass

    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state = {"running": True, "catches": 0}
    cfg = dict(config.DEFAULTS, ntfy_server=f"http://127.0.0.1:{srv.server_port}", remote_secret="S", mqtt_url="")
    r = remote.Remote(cfg, lambda: dict(state), lambda c: None, log=lambda t: None)

    def settle(n):
        deadline = time.time() + 3
        while len(posted) < n and time.time() < deadline:
            r.poke()
            time.sleep(0.05)
        time.sleep(0.3)

    try:
        settle(1)
        assert len(posted) == 1                # первый статус после запуска
        state["catches"] = 5
        settle(1)
        assert len(posted) == 1                # улов без открытой панели не шлём
        r._handle_line(json.dumps({"event": "message", "time": time.time(), "message": "ping"}))
        settle(2)
        assert len(posted) == 2 and posted[-1]["catches"] == 5
        state["catches"] = 6                   # панель открыта — изменения уходят
        settle(3)
        assert len(posted) == 3
        state["running"] = False
        r._watch_until = 0
        settle(4)
        assert len(posted) == 4 and posted[-1]["running"] is False   # стоп — важное событие
        assert "отправлен" in r.diagnostics()
    finally:
        r.stop()
        srv.shutdown()


def test_clean_name():
    from macro.catches import clean_name
    assert clean_name("You caught a Golden Carp! x1") == "Golden Carp"  # запасной разбор свободного текста
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


def _notice(name, color=(80, 220, 255), bg=(40, 110, 190), width=320):
    """Картинка уведомления «New Item <name>»: белый текст, название цветом редкости, тёмная обводка."""
    from macro.theme import _font
    img = Image.new("RGB", (width, 40), bg)
    d = ImageDraw.Draw(img)
    font = _font(20)
    d.text((8, 8), "New Item", font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    d.text((110, 8), f"<{name}>", font=font, fill=color, stroke_width=2, stroke_fill=(0, 0, 0))
    words = [("New", (8, 8, 40, 24)), ("Item", (52, 8, 50, 24)), (f"<{name}>", (110, 8, width - 115, 24))]
    return np.asarray(img), [{"text": f"New Item <{name}>", "words": words}]


def test_catch_log_uses_catalog_and_renders():
    from macro.catches import CatchLog
    cfg = dict(config.DEFAULTS, fish_names={"04": "My Legend"})
    log = CatchLog(cfg, log=lambda t: None)
    rgb, lines = _notice("My Legend")
    log.add(rgb, lines=lines)
    log.add(rgb, text="New Item <My Legend>")
    log.add(rgb, text="New Item <Skeletal Shark>")
    by = {log.name_of(c): c for c in log.items}
    assert by["My Legend"].count == 2 and by["My Legend"].rarity == "legendary" and by["My Legend"].fish_id == "04"
    assert by["Skeletal Shark"].rarity == "epic"
    assert log.summary_text().splitlines()[2].startswith("🌈 My Legend × 2")   # легендарные — первыми
    data, kind = log.render()
    assert kind == "gif" and data[:3] == b"GIF"                              # радуга переливается
    data, kind = log.render(rows=[by["Skeletal Shark"]])
    assert kind == "png" and data[:4] == b"\x89PNG"
    assert log.status()[0]["rarity"] == "legendary" and log.status()[0]["id"] == "04"


def test_only_new_item_notifications_count():
    from macro.catches import CatchLog, parse_name
    for text in ("SAFE ZONE PROTECTED", "ISLAND OF ZOU 2253 STUDS", "Fisherman", "Common Fish Bait x93", ""):
        assert parse_name(text) is None
    assert parse_name("New Item <Fangfish>") == "Fangfish"
    assert parse_name("New ltem (Fangfish)") == "Fangfish"        # OCR путает скобки и «I»
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    assert log.add(np.zeros((40, 200, 3), np.uint8), text="SAFE ZONE PROTECTED") is None
    assert log.items == []
    # Из нескольких снимков берётся первый с уведомлением
    rgb, lines = _notice("Fangfish")
    frames = [np.zeros((40, 320, 3), np.uint8), rgb]
    texts = iter(["ISLAND OF ZOU", "New Item <Fangfish>"])
    import macro.catches as cm
    orig = cm.ocr.read_lines
    cm.ocr.read_lines = lambda a: [{"text": next(texts), "words": []}]
    try:
        c = log.add_frames(frames)
    finally:
        cm.ocr.read_lines = orig
    assert c is not None and log.name_of(c) == "Fangfish"


def test_rarity_from_text_color():
    from macro.catches import read_notice, text_rarity
    rgb, lines = _notice("Fangfish", color=(80, 220, 255))
    assert read_notice(rgb, lines) == ("Fangfish", "rare")              # голубой, как на скрине
    rgb, lines = _notice("Shadow Eel", color=(190, 110, 255))
    assert read_notice(rgb, lines)[1] == "epic"
    rgb, lines = _notice("Plain Cod", color=(255, 255, 255))
    assert read_notice(rgb, lines)[1] == "common"
    # Голубая вода за белым текстом не делает его «редким»
    assert text_rarity(rgb, lines[0]["words"][0][1]) == "common"
    # Радуга — легендарная
    from macro.theme import _font
    img = Image.new("RGB", (400, 40), (40, 110, 190))
    d = ImageDraw.Draw(img)
    x = 10
    for ch, col in zip("<RAINBOWFISH>", [(255, 60, 60), (255, 160, 40), (250, 240, 60), (60, 230, 90),
                                          (60, 200, 255), (90, 90, 255), (200, 80, 255)] * 2):
        d.text((x, 8), ch, font=_font(20), fill=col, stroke_width=2, stroke_fill=(0, 0, 0))
        x += 22
    assert text_rarity(np.asarray(img), (8, 6, 300, 28)) == "legendary"
    # Скрин из игры: «<Fangfish>» голубая, «New Item» белая
    shot = np.asarray(Image.open(os.path.join(os.path.dirname(__file__), "notice_fangfish.png")).convert("RGB"))
    assert text_rarity(shot, (128, 10, 122, 24)) == "rare"
    assert text_rarity(shot, (32, 10, 95, 24)) == "common"


def test_new_fish_added_and_linked_to_icon(monkeypatch):
    """Рыба из списка игры без иконки (например, добавленная в игру позже) попадает в каталог с силуэтом."""
    from macro import fishdb
    monkeypatch.setattr(fishdb, "KNOWN_NAMES", fishdb.KNOWN_NAMES + ["Moon Eel"])
    from macro.catches import CatchLog
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    rgb, lines = _notice("Moon Eel")
    c = log.add(rgb, lines=lines)
    assert c.new and c.fish_id.startswith("u") and log.name_of(c) == "Moon Eel" and c.rarity == "rare"
    assert c.icon.size == (72, 72)                                     # силуэт цвета редкости
    assert log.add(rgb, lines=lines) is c and c.count == 2 and not c.new
    # Каталог переживает перезапуск
    fishdb.set_store_dir(fishdb.store_dir())
    assert fishdb.by_id(c.fish_id)["name"] == "Moon Eel"
    # Пользователь говорит: это рыба со скрина №00 — название переходит к её иконке
    uid = c.fish_id
    fishdb.link_to_builtin(uid, "00")
    log.remap(uid, "00")
    assert fishdb.by_id(uid) is None and log.name_of(c) == "Moon Eel" and c.fish_id == "00"
    c2 = log.add(rgb, lines=lines)
    assert c2 is c and c.count == 3 and fishdb.find_by_name("Moon Eel")["id"] == "00"
    assert "🔵 Moon Eel × 3" in log.summary_text()


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


def test_migration_turns_catch_log_on(tmp_path):
    path = str(tmp_path / "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"catch_log_enabled": False, "catch_capture_delay": 1.0, "kp": 1.2}, f)
    cfg = config.load(path)
    assert cfg["catch_log_enabled"] is True and cfg["catch_capture_delay"] == 0.15
    assert cfg["kp"] == 1.2 and cfg["config_version"] == config.CONFIG_VERSION
    cfg["catch_log_enabled"] = False                       # после переноса выбор пользователя уважаем
    config.save(cfg, path)
    assert config.load(path)["catch_log_enabled"] is False
    # v3: к одному брокеру из v2 добавляется второй
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"config_version": 2, "mqtt_url": "wss://broker.emqx.io:8084/mqtt"}, f)
    assert "broker.hivemq.com" in config.load(path)["mqtt_url"]
    assert "shiftr.io:443" in config.load(path)["mqtt_url"]


def test_catch_area_defaults_to_center_of_roblox(monkeypatch):
    from macro import engine as eng
    monkeypatch.setattr(eng.winput, "roblox_rect", lambda: (100, 50, 1000, 600))
    assert eng.catch_area(dict(config.DEFAULTS)) == [320, 182, 560, 216]
    assert eng.catch_area(dict(config.DEFAULTS, catch_area=[1, 2, 3, 4])) == [1, 2, 3, 4]


def test_windows_ocr_reads_game_notification():
    """Настоящий снимок области из игры: «New Item <Crimson Polka Puffer>» над водой (только Windows)."""
    if sys.platform != "win32":
        pytest.skip("Windows OCR есть только на Windows")
    from macro import ocr
    from macro.catches import CatchLog, ocr_variants, parse_name
    if not ocr.available():
        pytest.skip("на этой Windows нет движка OCR")
    rgb = np.asarray(Image.open(os.path.join(os.path.dirname(__file__), "catch_area_crimson.png")).convert("RGB"))
    for i, img in enumerate(ocr_variants(rgb)):
        lines = ocr.read_lines(img, raise_errors=True)
        print(f"OCR variant {i} {img.shape[1]}x{img.shape[0]}:", [ln["text"] for ln in lines], "->",
              parse_name("\n".join(ln["text"] for ln in lines)))
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    c = log.add_frames([rgb])
    assert c is not None, log.last_texts
    assert log.name_of(c).lower().replace(" ", "") == "crimsonpolkapuffer"
    assert c.rarity == "rare"


def test_parse_name_drops_stray_letters():
    from macro.catches import best_name, parse_name, weak_names
    assert parse_name("New Item <Crimson Polka x Puffer>") == "Crimson Polka Puffer"
    assert parse_name("Nev ltem <Fangfish>") == "Fangfish"
    # Так Windows OCR читал скрин из игры в разных вариантах подготовки картинки
    assert parse_name("Item <Crimson polkaxpuffer>") == "Crimson Polkaxpuffer"
    assert parse_name("NC\ufffdAtem <Crimson polka puffer>") == "Crimson Polka Puffer"
    # Без «Item» строка не засчитывается, но помогает поправить пробелы в названии
    assert parse_name("'Crimson polka puffer-\ufffd") is None
    assert weak_names("'Crimson polka puffer-\ufffd") == ["Crimson Polka Puffer"]
    assert best_name(["Unknown Fish", "Crimson Polka Puffer", "Unknown Fish"]) == "Crimson Polka Puffer"
    assert best_name(["Unknown Fish"]) == "Unknown Fish" and best_name([]) is None
    for junk in ("ISLAND OF ZOU", "STUDS", "- ONE", "t-QOTVCTED", "ECT D", "It's a nice day",
                 "'Saf Zone Ot Cted", "'SAF OW OT CTED", "(Ot Cted)", "<Safe Zone Protected>",
                 "SAFE ZONE PROTECTED", "System item", "Items"):
        assert parse_name(junk) is None


def test_fish_names_with_ocr_typos():
    """OCR путает 1–2 буквы — название притягивается к известной рыбе из игры."""
    from macro import fishdb
    from macro.catches import CatchLog, parse_name, read_notice
    cfg = dict(config.DEFAULTS)
    for typo, right in (("Crimson Polkaxpuffer", "Crimson Polka Puffer"), ("Fangflsh", "Fangfish"),
                        ("Jack O Blte", "Jack-O'-Bite"), ("Blue Llp Grouper", "Blue-Lip Grouper"),
                        ("Dark Skeletai Shark", "Dark Skeletal Shark"), ("Skeletal Shork", "Skeletal Shark"),
                        ("Tigerfln", "Tigerfin"), ("Exotlc Tigerfin", "Exotic Tigerfin"),
                        ("Golden Ribbon Ange1fish", "Golden Ribbon Angelfish"), ("Swordflsh", "Swordfish")):
        assert fishdb.canonical(typo, cfg) == right, typo
    for other in ("Saf Zone Ot Cted", "Ccrimson", "Golden", "Shark", "Island Of Zou"):
        assert fishdb.canonical(other, cfg) is None, other
    # Похожие рыбы не путаются между собой
    assert fishdb.canonical("Golden Tigerfin", cfg) == "Golden Tigerfin"
    assert fishdb.canonical("Skeletal Shark", cfg) == "Skeletal Shark"
    # Улов с опечаткой записывается на рыбу со скрина
    log = CatchLog(cfg, log=lambda t: None)
    c = log.add(np.zeros((40, 200, 3), np.uint8), text="New ltem <Crimson Polkaxpuffer>")
    assert c.fish_id == "01" and log.name_of(c) == "Crimson Polka Puffer" and not c.new
    # Без «Item» засчитывается только известная рыба, мусор — нет
    lines = [{"text": "<Golden Tigerfln>", "words": []}]
    assert read_notice(np.zeros((40, 200, 3), np.uint8), lines, cfg)[0] == "Golden Tigerfin"
    assert read_notice(np.zeros((40, 200, 3), np.uint8), [{"text": "'Saf Zone Ot Cted", "words": []}], cfg) is None
    assert parse_name("New Item <Swordfish>") == "Swordfish"


def test_tidy_learned_fixes_old_entries():
    from macro import fishdb
    from PIL import Image as Img
    junk = fishdb.add_fish(fishdb.placeholder_icon("rare"), "Saf Zone Ot Cted", rarity="rare")
    typo = fishdb.add_fish(fishdb.placeholder_icon("rare"), "Crimson Polkaxpuffer", rarity="rare")
    sword = fishdb.add_fish(fishdb.placeholder_icon("epic"), "Swordflsh", rarity="epic")
    other = fishdb.add_fish(Img.new("RGBA", (8, 8)), "Moon Eel", rarity="rare")
    moves = dict(fishdb.tidy_learned())
    assert moves == {junk["id"]: None, typo["id"]: "01", sword["id"]: "14", other["id"]: None}  # Moon Eel нет в игре
    assert fishdb.by_id(junk["id"]) is None and fishdb.by_id(typo["id"]) is None
    assert fishdb.by_id(sword["id"]) is None and fishdb.display_name(fishdb.by_id("14")) == "Swordfish"
    assert fishdb.by_id(other["id"]) is None
    assert fishdb.display_name(fishdb.by_id("01")) == "Crimson Polka Puffer"


def test_mqtt_commands_are_signed_and_not_replayable():
    """Брокер публичный: команда без секрета, повтор перехваченной и старая — не проходят."""
    import hashlib
    import hmac as hm
    from macro import mqttlink, remote
    secret = "TopSecret123"
    assert mqttlink.base_topic(secret).startswith("gpo/") and secret not in mqttlink.base_topic(secret)

    def cmd(c, t, n, key=secret):
        h = hm.new(key.encode(), f"{c}|{t}|{n}".encode(), hashlib.sha256).hexdigest()
        return json.dumps({"c": c, "t": t, "n": n, "h": h}).encode()

    now = time.time()
    seen = {}
    assert mqttlink.unpack_command(secret, cmd("stop", int(now * 1000), "a1"), seen, now) == "stop"
    assert mqttlink.unpack_command(secret, cmd("stop", int(now * 1000), "a1"), seen, now) is None   # повтор
    assert mqttlink.unpack_command(secret, cmd("stop", int(now * 1000), "b2", key="guess"), seen, now) is None
    assert mqttlink.unpack_command(secret, cmd("stop", int((now - 600) * 1000), "c3"), seen, now) is None
    assert mqttlink.unpack_command(secret, b"stop", seen, now) is None
    packed = json.loads(mqttlink.pack_status(secret, {"running": True}))
    assert json.loads(packed["d"]) == {"running": True}
    assert packed["h"] == hm.new(secret.encode(), packed["d"].encode(), hashlib.sha256).hexdigest()
    assert mqttlink.parse_url("wss://broker.emqx.io:8084/mqtt") == ("broker.emqx.io", 8084, "websockets", True, "/mqtt")
    shiftr = "wss://public:public@public.cloud.shiftr.io:443/"
    assert mqttlink.parse_url(shiftr) == ("public.cloud.shiftr.io", 443, "websockets", True, "/")
    assert mqttlink.url_auth(shiftr) == ("public", "public") and mqttlink.url_auth("wss://a.b/mqtt") is None
    assert len(mqttlink.split_urls(config.DEFAULTS["mqtt_url"])) == 4
    link = remote.webapp_link(dict(config.DEFAULTS, remote_secret="X" * 10))
    assert "m=wss%3A%2F%2Fbroker.emqx.io" in link


def test_overnight_ocr_reads_resolve_to_fish_list():
    """Что Windows OCR прочитал за ночь рыбалки: опечатки, «<» как буква, обрезанные названия."""
    from macro import fishdb
    reads = {
        "Candy Corn": "Candy Corn Squid", "Canqlerfish": "Anglerfish", "Aack-0": "Jack-O'-Bite",
        "Czebra": "Zebra Ribbon Angelfish", "Swordfish": "Swordfish", "Ansterfish": "Anglerfish",
        "Zketevat Shark": "Skeletal Shark", "Gowen-tiqerfin": "Golden Tigerfin", "Zsvordfis": "Swordfish",
        "Cg0tden Doua Putter": "Golden Polka Puffer", "Gotden Ribbon": "Golden Ribbon Angelfish",
        "Dark Shark": "Dark Skeletal Shark", "Dark Skdetacshstk": "Dark Skeletal Shark",
        "Crimson Snapper ISLAND OF ZOU": "Crimson Snapper",
    }
    for raw, right in reads.items():
        assert fishdb.match_name(raw) == right, raw
    # Обрывок подходит к нескольким рыбам — не угадываем
    for raw in ("Shark", "Gotden", "Crimson", "Serk", "Shstk", "Putter", "Ribbon Angelfish", "Polka Puffer",
                "Saf Zone Ot Cted", "ISLAND OF ZOU", "Common Fish Bait x93"):
        assert fishdb.match_name(raw) is None, raw
    # Все рыбы из списка узнаются сами по себе и не путаются друг с другом
    for name in fishdb.KNOWN_NAMES:
        assert fishdb.match_name(name) == name


def test_item_line_split_by_ocr_and_unknown_fish():
    from macro import fishdb
    from macro.catches import CatchLog, item_tail
    assert item_tail("ISLAND OF ZOU\nNew Item <Crimson\nPolka Puffer>\nSTUDS") == "Crimson Polka Puffer"
    assert item_tail("SAFE ZONE PROTECTED") is None
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    black = np.zeros((40, 200, 3), np.uint8)
    c = log.add(black, lines=[{"text": "New Item <Gotden", "words": []}, {"text": "Ribbon>", "words": []}])
    assert log.name_of(c) == "Golden Ribbon Angelfish"
    u = log.add(black, text="New Item <Shark>")            # не разобрать: улов есть, рыба неизвестна
    assert u.fish["name"] == fishdb.UNKNOWN_NAME and not u.new
    assert log.add(black, text="New Item <Serk>") is u and u.count == 2
    assert len([f for f in fishdb.all_fish() if f["name"] == fishdb.UNKNOWN_NAME]) == 1
    assert log.add(black, text="SAFE ZONE PROTECTED") is None


def test_anglerfish_icon_and_rarity_from_notice():
    from macro import fishdb
    from macro.catches import CatchLog
    f = fishdb.by_id("12")
    assert f["name"] == "Anglerfish" and f["rarity"] is None and fishdb.icon("12").size == (71, 71)
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    rgb, lines = _notice("Anglerfish", color=(190, 110, 255))   # фиолетовое название — эпическая
    c = log.add(rgb, lines=lines)
    assert c.fish_id == "12" and not c.new and c.rarity == "epic"
    fishdb.set_store_dir(fishdb.store_dir())                   # запомнилась после перезапуска
    assert fishdb.by_id("12")["rarity"] == "epic"


def test_new_builtin_icons_and_learned_merge():
    from macro import fishdb
    from macro.catches import CatchLog
    assert fishdb.by_id("13")["name"] == "Golden Ribbon Angelfish" and fishdb.by_id("14")["name"] == "Swordfish"
    assert fishdb.icon("13").size == fishdb.icon("14").size == (71, 71)
    # Старая выученная запись Swordfish (силуэт) переходит на иконку, мусорное имя у встроенной — убирается
    u = fishdb.add_fish(fishdb.placeholder_icon("rare"), "Swordfish", rarity="rare")
    fishdb.learn_name("02", "Shstk")
    moves = dict(fishdb.tidy_learned())
    assert moves[u["id"]] == "14" and fishdb.display_name(fishdb.by_id("02")) == "Blue-Lip Grouper"
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    c = log.add(np.zeros((40, 200, 3), np.uint8), text="New Item <Zsvordfis>")
    assert c.fish_id == "14" and not c.new
    cfg = dict(config.DEFAULTS, fish_names={"02": " ", "04": "My Legend"})
    config.migrate(cfg, config.CONFIG_VERSION)
    assert cfg["fish_names"] == {"04": "My Legend"}


def test_renamed_builtin_still_found_by_game_name():
    from macro.catches import CatchLog
    log = CatchLog(dict(config.DEFAULTS, fish_names={"04": "Моя легенда"}), log=lambda t: None)
    c = log.add(np.zeros((40, 200, 3), np.uint8), text="New Item <Golden Polka Puffer>")
    assert c.fish_id == "04" and log.name_of(c) == "Моя легенда" and not c.new


def test_fangfish_icon_and_old_dark_shark_name_dropped():
    from macro import fishdb
    from macro.catches import CatchLog
    assert fishdb.by_id("08")["name"] == "Fangfish" and fishdb.by_id("08")["rarity"] == "rare"
    assert fishdb.by_id("15")["name"] == "Dark Skeletal Shark" and fishdb.by_id("15")["rarity"] == "legendary"
    fishdb.learn_name("08", "Dark Skeletal Shark")       # так записывали старые версии
    fishdb.tidy_learned()
    assert fishdb.display_name(fishdb.by_id("08")) == "Fangfish"
    log = CatchLog(dict(config.DEFAULTS), log=lambda t: None)
    black = np.zeros((40, 200, 3), np.uint8)
    assert log.add(black, text="New Item <Dark Shark>").fish_id == "15"
    assert log.add(black, text="New Item <Fangflsh>").fish_id == "08"


def test_telegram_diagnose_explains_problems(monkeypatch):
    from macro import tgcontrol as tc
    # Ненастоящий токен, собранный по частям, чтобы сканеры секретов не принимали его за утечку
    good = "123456789" + ":" + "X" * 35
    assert tc.clean_token(f"  bot{good}\n") == good
    assert tc.clean_token(f"https://api.telegram.org/bot{good}/getMe") == good
    assert tc.clean_chat(" 12345 \n") == "12345" and tc.clean_chat("-100 42") == "-10042"
    sent = []

    def fake(errors):
        def call(token, method, params=None, timeout=15):
            if method in errors:
                raise errors[method]
            if method == "getMe":
                return {"result": {"username": "my_fish_bot"}}
            if method == "getUpdates":
                return {"result": [{"update_id": 1, "message": {"chat": {"id": 777, "type": "private",
                                                                          "first_name": "Друг"}}}]}
            sent.append((method, params))
            return {"ok": True}
        return call

    cfg = dict(config.DEFAULTS, telegram_token=good, telegram_chat_id="777")
    monkeypatch.setattr(tc, "_call", fake({}))
    ok, lines = tc.diagnose(cfg)
    assert ok and "@my_fish_bot" in lines[0] and sent[-1][1]["chat_id"] == "777"
    assert tc.find_chats(good) == [("777", "Друг")]
    monkeypatch.setattr(tc, "_call", fake({"getMe": tc.TelegramError(401, "Unauthorized")}))
    ok, lines = tc.diagnose(cfg)
    assert not ok and "неверный токен" in lines[-1]
    monkeypatch.setattr(tc, "_call", fake({"sendMessage": tc.TelegramError(400, "Bad Request: chat not found")}))
    assert "Start" in tc.diagnose(cfg)[1][-1]
    assert "id самого бота" in tc.diagnose(dict(cfg, telegram_chat_id="123456789"))[1][-1]
    assert "Узнать chat id" in tc.diagnose(dict(cfg, telegram_chat_id=""))[1][-1]
    monkeypatch.setattr(tc, "_call", fake({"getMe": tc.urllib.error.URLError("timed out")}))
    assert "VPN" in tc.diagnose(cfg)[1][-1]
    assert "другой программе" in tc.explain(tc.TelegramError(409, "Conflict: terminated by other getUpdates"))


def test_catch_list_has_clear_button_with_confirmation(monkeypatch):
    from macro import tgcontrol as tc
    calls = []
    monkeypatch.setattr(tc, "_call", lambda token, method, params=None, timeout=15: calls.append((method, params)) or {})
    cfg = dict(config.DEFAULTS, tg_control=True, telegram_token="1" * 9 + ":" + "X" * 35, telegram_chat_id="42")
    bot = tc.TelegramControl(cfg, lambda c: None, log=lambda t: None)
    try:
        bot._send("Очистить список улова? Сейчас в нём 5 рыб", tc.RESET_CONFIRM)
        markup = json.loads(next(p for m, p in calls if m == "sendMessage")["reply_markup"])
        assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == ["reset_catches", "reset_cancel"]
        assert tc.CATCH_BUTTONS[0][0]["callback_data"] == "reset_ask"
        # Нажатие кнопки под списком доходит до окна как команда
        got = []
        bot.on_command = got.append
        bot._handle("t", "42", {"callback_query": {"id": "1", "data": "reset_ask",
                                                    "message": {"chat": {"id": 42, "type": "private"}}}})
        assert got == ["reset_ask"]
    finally:
        bot.stop()
    from macro import remote
    assert "reset_catches" in remote.COMMANDS                 # кнопка «Очистить» в мини-приложении


def test_hud_layout_stays_outside_minigame_area():
    """Рамка, шкала и панель HUD рисуются снаружи области мини-игры — распознаванию не мешают."""
    from macro.hud import PANEL_W, layout
    area = [400, 200, 120, 420]
    (x, y, w, h), frame, (px, py) = layout(area, 1920, 1080)
    fx0, fy0, fx1, fy1 = frame
    assert x + fx0 < 400 and x + fx1 >= 520 and y + fy0 < 200 and y + fy1 >= 620   # рамка вокруг
    assert x + px >= 520                                                       # панель справа
    # У правого края экрана панель уходит налево от области
    (x, y, w, h), frame, (px, py) = layout([1700, 200, 120, 420], 1920, 1080)
    assert x + px + PANEL_W <= 1700
    assert layout(None, 1920, 1080)[1] is None                                 # без области — только панель


def test_black_screen_checks_roblox_window_and_rechecks(monkeypatch):
    """Тёмный рабочий стол на другом мониторе или один тёмный кадр — не повод прыгать."""
    from macro import engine as eng
    e = eng.MacroEngine(dict(config.DEFAULTS), log=lambda t: None)
    keys = []
    monkeypatch.setattr(eng.winput, "press_key", lambda k: keys.append(k))
    monkeypatch.setattr(eng.winput, "roblox_rect", lambda: (0, 0, 800, 600))
    monkeypatch.setattr(e, "_sleep", lambda s: None)
    monkeypatch.setattr(e, "_delay", lambda k: None)
    night = np.full((600, 800, 3), 14, np.uint8)            # тёмная ночь в игре, но не чёрный экран
    monkeypatch.setattr(eng.screen, "grab", lambda r: night)
    monkeypatch.setattr(eng.screen, "grab_monitor", lambda: np.zeros((10, 10, 3), np.uint8))  # чёрный 2-й монитор
    e._check_black_screen()
    assert keys == []
    frames = iter([np.zeros((600, 800, 3), np.uint8), night])  # один чёрный кадр, потом нормально
    monkeypatch.setattr(eng.screen, "grab", lambda r: next(frames))
    e._check_black_screen()
    assert keys == []
    shots = iter([np.zeros((600, 800, 3), np.uint8)] * 3 + [night])   # настоящий чёрный экран
    monkeypatch.setattr(eng.screen, "grab", lambda r: next(shots))
    e._check_black_screen()
    assert keys == ["space"]
    cfg = dict(config.DEFAULTS, black_threshold=0.5)
    config.migrate(cfg, 4)
    assert cfg["black_threshold"] == 0.85
