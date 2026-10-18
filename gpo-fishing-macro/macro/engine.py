"""Главный цикл макроса: подготовка → заброс → ожидание поклёвки → мини-игра → повтор."""
import threading
import time

from . import alarm, screen, winput
from .controller import PDController
from .detector import black_fraction, detect
from .watchdog import Watchdog


class _Stopped(Exception):
    pass


class AlarmManager:
    """Шлёт тревогу и повторяет её, пока пользователь не отреагирует (Start или «Stop alarm»)."""

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self._event = threading.Event()
        self.active = False
        self.reason = None

    def trigger(self, reason, with_screenshot=True):
        if self.active or not self.cfg.get("alarm_enabled"):
            return
        self.active = True
        self.reason = reason
        self._event.clear()
        png = None
        if with_screenshot and self.cfg.get("alarm_screenshot"):
            try:
                png = screen.screenshot_png()
            except Exception:
                png = None
        threading.Thread(target=self._loop, args=(reason, png), daemon=True).start()

    def _loop(self, reason, png):
        while self.active:
            alarm.send_alarm(self.cfg, reason, png, log=self.log)
            repeat = float(self.cfg.get("alarm_repeat_sec") or 0)
            if repeat <= 0 or self._event.wait(repeat):
                break
        self.active = False

    def acknowledge(self):
        if self.active:
            self.log("Тревога сброшена")
        self.active = False
        self._event.set()


class MacroEngine:
    def __init__(self, cfg, log=print, on_status=None, on_stats=None):
        self.cfg = cfg
        self.log = log
        self.on_status = on_status or (lambda text, color: None)
        self.on_stats = on_stats or (lambda stats: None)
        self.alarms = AlarmManager(cfg, log)
        self.watchdog = Watchdog(cfg)
        self._stop = threading.Event()
        self._thread = None
        self._mouse_held = False
        self._mouse_sent = 0.0
        self._in_minigame = False  # в мини-игре рыбу можно на кадр потерять — не требуем её
        self.last_detection = None
        self.stats = {"casts": 0, "catches": 0, "loops": 0, "started": None}

    # ---------- управление ----------
    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        problems = self.validate()
        if problems:
            self.on_status("Не настроено: " + "; ".join(problems), "red")
            return
        self.alarms.acknowledge()
        self._stop.clear()
        self.stats.update(casts=0, catches=0, loops=0, started=time.time())
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.alarms.acknowledge()

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def validate(self):
        cfg = self.cfg
        problems = []
        if not cfg.get("area"):
            problems.append("область мини-игры (F2)")
        if not cfg.get("water_point"):
            problems.append("точка воды (Casting)")
        if cfg.get("auto_buy_bait") and not all(cfg.get(k) for k in ("buy_left", "buy_middle", "buy_right")):
            problems.append("точки покупки наживки")
        if cfg.get("auto_store_fruit") and not cfg.get("store_point"):
            problems.append("точка Store для фрукта")
        if cfg.get("auto_select_bait") and not cfg.get("bait_point"):
            problems.append("точка верхней наживки")
        return problems

    # ---------- утилиты ----------
    def _check(self):
        """Вызывается постоянно: проверяет остановку и сторожа."""
        if self._stop.is_set():
            raise _Stopped
        if self.cfg.get("wd_check_window"):
            ok = winput.roblox_window_exists() and winput.foreground_title() == "Roblox"
            self.watchdog.on_window_state(ok)
        reason = self.watchdog.check()
        if reason:
            self._raise_alarm(reason)

    def _raise_alarm(self, reason):
        self.log("ТРЕВОГА: " + reason)
        self._set_mouse(False)
        self.alarms.trigger(reason)
        if self.cfg.get("wd_stop_on_alarm"):
            self.on_status("Тревога: " + reason, "red")
            self._stop.set()
            raise _Stopped
        # Макрос продолжает работу — начинаем отсчёт заново, чтобы не спамить
        self.watchdog.reset()

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while True:
            self._check()
            left = end - time.monotonic()
            if left <= 0:
                return
            self._stop.wait(min(left, 0.1))

    def _set_mouse(self, held, resend_after=None):
        """Меняет состояние ЛКМ. resend_after — Control State Sync: если состояние не менялось
        дольше этого времени, повторяем событие, чтобы игра не «потеряла» зажатую/отпущенную кнопку."""
        now = time.monotonic()
        stale = resend_after is not None and resend_after > 0 and now - self._mouse_sent >= resend_after
        if held != self._mouse_held or (stale and held):
            winput.mouse_down() if held else winput.mouse_up()
        elif stale:
            winput.mouse_up()
        else:
            return
        self._mouse_held = held
        self._mouse_sent = now

    def _delay(self, key):
        self._sleep(float(self.cfg[key]))

    def _click(self, point, delay_key):
        winput.click(*point)
        self._delay(delay_key)

    def _key(self, name, delay_key):
        winput.press_key(name)
        self._delay(delay_key)

    def _focus_roblox(self):
        """Roblox Window Focus: выводим окно игры вперёд перед началом работы."""
        if not winput.focus_roblox():
            self.log("Окно Roblox не найдено")
            return False
        self._delay("focus_delay")
        self._delay("post_focus_delay")
        return True

    def _check_black_screen(self):
        """Anti-Macro Black Screen: спамим клавишей, пока экран не посветлеет; не вышло — тревога."""
        cfg = self.cfg
        threshold = float(cfg["black_threshold"])
        if threshold <= 0 or black_fraction(screen.grab_monitor()) < threshold:
            return
        self.log("Чёрный экран античита — спамлю клавишей " + str(cfg["black_spam_key"]))
        self.on_status("Clearing black screen", "orange")
        self._set_mouse(False)
        deadline = time.monotonic() + float(cfg["black_max_sec"])
        while black_fraction(screen.grab_monitor()) >= threshold:
            if time.monotonic() > deadline:
                self._raise_alarm(f"Чёрный экран не уходит {cfg['black_max_sec']:g} с — нужна ваша помощь")
                return
            winput.press_key(cfg["black_spam_key"])
            self._delay("black_spam_delay")
        self.log("Чёрный экран убран")

    def _grab_detect(self):
        cfg = self.cfg
        rgb = screen.grab(cfg["area"])
        det = detect(rgb, cfg["zone_color"], int(cfg["zone_tolerance"]),
                     int(cfg["fish_white_min"]), int(cfg["min_zone_pixels"]),
                     cfg["bar_mode"], int(cfg["dark_max"]), float(cfg["gap_mult"]),
                     cfg["progress_color"], bool(cfg["require_progress"]),
                     require_fish=not self._in_minigame)
        self.last_detection = (rgb, det)
        return det

    def _emit_stats(self):
        self.on_stats(dict(self.stats))

    # ---------- цикл ----------
    def _run(self):
        self.log("Макрос запущен")
        self.on_status("Running", "green")
        self.watchdog.reset()
        self._emit_stats()
        try:
            winput.micro_delay = float(self.cfg["micro_delay"])
            self._focus_roblox()
            while True:
                self._pre_cast()
                self._cast()
                if not self._wait_for_bite():
                    self.watchdog.on_cast_failed()
                    self.log(f"Нет поклёвки за {self.cfg['recast_timeout']} с — перезаброс "
                             f"({self.watchdog.failed_casts} подряд)")
                    self._check()
                    continue
                det = self.last_detection[1] if self.last_detection else None
                if det:
                    self.log(f"Мини-игра найдена: полоса {det.zone_top}-{det.zone_bottom}, "
                             f"трек {det.bar_top}-{det.bar_bottom}, колонки {det.bar_x0}-{det.bar_x1}")
                self._play_minigame()
                self._sleep(float(self.cfg["fish_end_delay"]))
                self.stats["catches"] += 1
                self.watchdog.on_catch()
                self._emit_stats()
                self._post_catch()
                self.stats["loops"] += 1
        except _Stopped:
            pass
        except Exception as exc:
            self.log(f"Ошибка: {exc!r}")
            self.alarms.trigger(f"Макрос упал с ошибкой: {exc!r}")
            self.on_status("Error", "red")
        finally:
            self._set_mouse(False)
            if not self.alarms.active:
                self.on_status("Stopped", "red")
            self.log("Макрос остановлен")

    def _pre_cast(self):
        cfg = self.cfg
        winput.micro_delay = float(cfg["micro_delay"])
        if winput.IS_WINDOWS and winput.foreground_title() != "Roblox":
            self._focus_roblox()
        self._check_black_screen()
        loops = self.stats["loops"]
        every = max(int(cfg["loops_per_purchase"] or 1), 1)
        if cfg.get("auto_buy_bait") and loops % every == 0:
            self.on_status("Buying bait", "blue")
            self._key(cfg["other_hotkey"], "rod_select_delay")  # убираем удочку, чтобы E сработала
            # E → левый → средний → ввод количества → левый → правый → средний
            self._key("e", "buy_e_delay")
            self._click(cfg["buy_left"], "buy_click_delay")
            self._click(cfg["buy_middle"], "buy_click_delay")
            if cfg.get("buy_type_amount"):
                winput.type_text(every)
                self._delay("buy_type_delay")
            self._click(cfg["buy_left"], "buy_click_delay")
            self._click(cfg["buy_right"], "buy_click_delay")
            self._click(cfg["buy_middle"], "buy_click_delay")
        # Сначала «что-то другое», потом удочку: так удочка гарантированно в руках,
        # а не снята повторным нажатием той же клавиши
        self._key(cfg["other_hotkey"], "rod_select_delay")
        self._key(cfg["rod_hotkey"], "rod_select_delay")
        if cfg.get("auto_select_bait"):
            self._click(cfg["bait_point"], "bait_click_delay")

    def _cast(self):
        cfg = self.cfg
        self.on_status("Casting", "blue")
        winput.move_to(*cfg["water_point"])
        self._delay("cursor_delay")
        self._set_mouse(True)
        try:
            self._sleep(float(cfg["cast_hold"]))
        finally:
            self._set_mouse(False)
        self.stats["casts"] += 1
        self._emit_stats()

    def _wait_for_bite(self):
        self.on_status("Waiting for bite", "blue")
        deadline = time.monotonic() + float(self.cfg["recast_timeout"])
        frame = max(float(self.cfg["scan_loop_delay"]), 0.005)
        seen = 0
        while time.monotonic() < deadline:
            self._check()
            seen = seen + 1 if self._grab_detect() else 0
            if seen >= 3:
                return True
            self._stop.wait(frame)
        return False

    def _play_minigame(self):
        cfg = self.cfg
        self.on_status("Fishing", "green")
        ctrl = PDController(float(cfg["kp"]), float(cfg["kd"]), float(cfg["pd_clamp"]),
                            float(cfg["approach_damping"]), float(cfg["chase_damping"]))
        resend = float(cfg["resend_interval"])
        self._in_minigame = True
        frame = 1.0 / max(int(cfg["scan_fps"]), 5)
        last_fish = None
        lost_since = None
        prev = time.monotonic()
        started = prev
        try:
            while time.monotonic() - started < 180:
                self._check()
                det = self._grab_detect()
                now = time.monotonic()
                dt, prev = now - prev, now
                if det is None:
                    self._set_mouse(False)
                    lost_since = lost_since or now
                    if now - lost_since > 0.6:
                        return
                else:
                    lost_since = None
                    fish = det.fish_y if det.fish_y is not None else last_fish
                    if fish is not None:
                        last_fish = fish
                        self._set_mouse(ctrl.update(det.zone_center, fish, det.bar_height, dt), resend)
                spent = time.monotonic() - now
                self._stop.wait(max(frame - spent, 0.001))
            self.log("Мини-игра длится больше 3 минут — сбрасываю")
        finally:
            self._in_minigame = False
            self._set_mouse(False)

    def _post_catch(self):
        cfg = self.cfg
        if cfg.get("auto_store_fruit"):
            self.on_status("Storing devil fruit", "blue")
            self._key(cfg["fruit_hotkey"], "fruit_hotkey_delay")
            self._click(cfg["store_point"], "fruit_store_delay")
            # Shift-lock выкл → Backspace (выбросить, если не сохранилось) → Shift-lock обратно
            self._key("shift", "fruit_shift_delay")
            self._key("backspace", "fruit_backspace_delay")
            self._key("shift", "fruit_shift_delay")
