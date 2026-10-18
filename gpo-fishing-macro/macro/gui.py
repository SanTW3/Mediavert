"""Окно управления макросом (tkinter) — по образцу «GPO Fishing Macro Controls»."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import alarm, config, screen
from .engine import MacroEngine
from .overlay import AreaSelector, PointPicker

try:
    from pynput import keyboard
except ImportError:  # без pynput горячие клавиши не работают, но окно открывается
    keyboard = None

DIGITS = [str(d) for d in range(10)]


def key_name(key):
    """pynput-клавиша → строка вида 'f1', 'a', '7'."""
    if keyboard is None:
        return None
    if isinstance(key, keyboard.Key):
        return key.name
    if getattr(key, "char", None):
        return key.char.lower()
    vk = getattr(key, "vk", None)
    if vk is not None and 0x30 <= vk <= 0x5A:
        return chr(vk).lower()
    return None


def fmt_point(p):
    return f"X: {p[0]}, Y: {p[1]}" if p else "Not set"


class App:
    def __init__(self):
        self.cfg = config.load()
        self.events = queue.Queue()
        self.root = tk.Tk()
        self.root.title("GPO Fishing Macro")
        self.root.geometry("520x560")
        self.root.minsize(480, 500)
        self.vars = {}
        self._save_job = None
        self._rebinding = None
        self._preview = None

        self.engine = MacroEngine(
            self.cfg,
            log=lambda text: self.events.put(("log", text)),
            on_status=lambda text, color: self.events.put(("status", (text, color))),
            on_stats=lambda stats: self.events.put(("stats", stats)),
        )

        self._build()
        self._apply_topmost()
        self._start_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.root.after(50, self._poll)

    # ---------- переменные, связанные с конфигом ----------
    def var(self, key, kind=float):
        if key in self.vars:
            return self.vars[key]
        value = self.cfg[key]
        if kind is bool:
            v = tk.BooleanVar(value=bool(value))
        else:
            v = tk.StringVar(value=str(value))

        def changed(*_):
            try:
                raw = v.get()
                self.cfg[key] = raw if kind is bool else kind(raw)
            except (ValueError, tk.TclError):
                return
            self._schedule_save()

        v.trace_add("write", changed)
        self.vars[key] = v
        return v

    def _schedule_save(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(500, self._save)

    def _save(self):
        self._save_job = None
        try:
            config.save(self.cfg)
        except OSError as exc:
            self.log(f"Не удалось сохранить настройки: {exc}")

    # ---------- построение окна ----------
    def _build(self):
        root = self.root
        ttk.Label(root, text="GPO Fishing Macro Controls", font=("Segoe UI", 15, "bold")).pack(pady=(14, 4))
        self.status = tk.Label(root, text="Status: Stopped", fg="red", font=("Segoe UI", 10))
        self.status.pack(pady=(0, 8))

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        for title, builder in (("General", self._tab_general), ("Casting", self._tab_casting),
                               ("Fishing", self._tab_fishing), ("Pre-Cast", self._tab_precast),
                               ("Literally Everything Else", self._tab_else)):
            frame = ttk.Frame(nb, padding=6)
            nb.add(frame, text=title)
            builder(frame)

    def _spin(self, parent, row, label, key, frm, to, inc, kind=float, unit=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(10, 20), pady=3)
        ttk.Spinbox(parent, from_=frm, to=to, increment=inc, width=9,
                    textvariable=self.var(key, kind)).grid(row=row, column=1, sticky="w")
        if unit:
            ttk.Label(parent, text=unit).grid(row=row, column=2, sticky="w", padx=6)

    def _entry(self, parent, row, label, key, width=30, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(10, 10), pady=3)
        ttk.Entry(parent, width=width, show=show, textvariable=self.var(key, str)).grid(
            row=row, column=1, columnspan=2, sticky="w")

    def _point_row(self, parent, row, button, key, hint):
        lbl = tk.Label(parent, text=fmt_point(self.cfg.get(key)),
                       fg="green" if self.cfg.get(key) else "gray")

        def pick():
            def done(point):
                self.cfg[key] = point
                lbl.config(text=fmt_point(point), fg="green")
                self._save()
            PointPicker(self.root, hint, done)

        ttk.Button(parent, text=button, width=18, command=pick).grid(
            row=row, column=0, sticky="w", padx=(10, 10), pady=3)
        lbl.grid(row=row, column=1, columnspan=2, sticky="w")

    # --- General ---
    def _tab_general(self, tab):
        hk = ttk.LabelFrame(tab, text="Hotkeys", padding=8)
        hk.pack(fill="x", pady=(0, 10))
        self.hotkey_labels = {}
        for row, (label, key) in enumerate((("Start/Stop:", "hotkey_start_stop"),
                                            ("Change Area:", "hotkey_change_area"),
                                            ("Exit:", "hotkey_exit"))):
            ttk.Label(hk, text=label).grid(row=row, column=0, sticky="w", padx=(10, 30), pady=4)
            lbl = tk.Label(hk, text=self.cfg[key].upper(), fg="blue", font=("Segoe UI", 10, "bold"), width=8,
                           anchor="w")
            lbl.grid(row=row, column=1, sticky="w")
            self.hotkey_labels[key] = lbl
            ttk.Button(hk, text="Rebind", command=lambda k=key: self._rebind(k)).grid(row=row, column=2, padx=10)

        ttk.Separator(tab).pack(fill="x", pady=6)
        ttk.Checkbutton(tab, text="Always on Top", variable=self.var("always_on_top", bool),
                        command=self._apply_topmost).pack(pady=6)

        nh = ttk.LabelFrame(tab, text="Number Hotkeys", padding=8)
        nh.pack(fill="x", pady=6)
        for row, (label, key) in enumerate((("Rod Hotkey:", "rod_hotkey"),
                                            ("Anything Else Hotkey:", "other_hotkey"))):
            ttk.Label(nh, text=label).grid(row=row, column=0, sticky="w", padx=(10, 30), pady=4)
            ttk.Combobox(nh, values=DIGITS, width=9, state="readonly",
                         textvariable=self.var(key, str)).grid(row=row, column=1, sticky="w")

    # --- Casting ---
    def _tab_casting(self, tab):
        wp = ttk.LabelFrame(tab, text="Water Point", padding=8)
        wp.pack(fill="x", pady=(0, 10))
        self._point_row(wp, 0, "Set Water Point", "water_point", "Кликните по воде, куда забрасывать удочку")

        ct = ttk.LabelFrame(tab, text="Cast Timing", padding=8)
        ct.pack(fill="x")
        self._spin(ct, 0, "Cast Hold Duration:", "cast_hold", 0.1, 10, 0.1, unit="s")
        self._spin(ct, 1, "Recast Timeout:", "recast_timeout", 3, 300, 1, unit="s")

    # --- Fishing ---
    def _tab_fishing(self, tab):
        pd = ttk.LabelFrame(tab, text="PD Control Parameters", padding=8)
        pd.pack(fill="x", pady=(0, 10))
        self._spin(pd, 0, "Kp (Proportional):", "kp", 0, 20, 0.1)
        self._spin(pd, 1, "Kd (Derivative):", "kd", 0, 20, 0.05)
        self._spin(pd, 2, "PD Clamp:", "pd_clamp", 0.1, 10, 0.1)

        tm = ttk.LabelFrame(tab, text="Timing", padding=8)
        tm.pack(fill="x")
        self._spin(tm, 0, "Fish End Delay:", "fish_end_delay", 0, 30, 0.5, unit="s")

    # --- Pre-Cast ---
    def _tab_precast(self, tab):
        def section(flag_key, title, build):
            ttk.Checkbutton(tab, text=title, variable=self.var(flag_key, bool)).pack(anchor="w", pady=(4, 2))
            frame = ttk.LabelFrame(tab, text=f"{title} Settings", padding=8)
            build(frame)

            def toggle(*_):
                if self.vars[flag_key].get():
                    frame.pack(fill="x", pady=(0, 6), after=check_widgets[flag_key])
                else:
                    frame.pack_forget()
            check_widgets[flag_key] = tab.pack_slaves()[-1]
            self.vars[flag_key].trace_add("write", toggle)
            toggle()

        check_widgets = {}

        def buy(f):
            self._point_row(f, 0, "Set Left Point", "buy_left", "Кликните по левой кнопке покупки")
            self._point_row(f, 1, "Set Middle Point", "buy_middle", "Кликните по средней кнопке/полю")
            self._point_row(f, 2, "Set Right Point", "buy_right", "Кликните по правой кнопке покупки")
            self._spin(f, 3, "Loops Per Purchase:", "loops_per_purchase", 1, 10000, 1, kind=int)
            ttk.Checkbutton(f, text="Type amount into Middle Point",
                            variable=self.var("buy_type_amount", bool)).grid(row=4, column=0, columnspan=3,
                                                                             sticky="w", padx=10)

        def fruit(f):
            ttk.Label(f, text="Fruit Hotkey:").grid(row=0, column=0, sticky="w", padx=(10, 10), pady=3)
            ttk.Combobox(f, values=DIGITS, width=9, state="readonly",
                         textvariable=self.var("fruit_hotkey", str)).grid(row=0, column=1, sticky="w")
            self._point_row(f, 1, "Set Store Point", "store_point", "Кликните по кнопке Store")

        def bait(f):
            self._point_row(f, 0, "Set Bait Point", "bait_point", "Кликните по верхней наживке в списке")

        section("auto_buy_bait", "Auto Buy Common Bait", buy)
        section("auto_store_fruit", "Auto Store Devil Fruit", fruit)
        section("auto_select_bait", "Auto Select Top Bait", bait)

    # --- Literally Everything Else ---
    def _adv(self, parent, label, key, hint, kind=float):
        """Строка как в оригинале: подпись слева, поле справа, серая подсказка «↳ …» под ними."""
        row = parent.grid_size()[1]
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(10, 10), pady=(6, 0))
        ttk.Entry(parent, width=9, textvariable=self.var(key, kind)).grid(row=row, column=1, sticky="e", padx=(0, 4))
        tk.Label(parent, text="↳ " + hint, fg="gray", font=("Segoe UI", 8)).grid(
            row=row + 1, column=0, columnspan=2, sticky="w", padx=(30, 0))

    def _section(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 10), padx=(0, 4))
        frame.columnconfigure(0, weight=1)
        return frame

    def _tab_else(self, tab):
        tk.Label(tab, text="⚠ DON'T CHANGE IF YOU DON'T KNOW WHAT YOU'RE DOING", fg="red",
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(0, 6))
        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        f = self._section(inner, "Roblox Window Focus")
        self._adv(f, "Roblox Focus Delay (s):", "focus_delay",
                  "Time to wait after focusing Roblox window before continuing")
        self._adv(f, "Post-Focus Extra Delay (s):", "post_focus_delay",
                  "Additional delay after Roblox window is focused (for stability)")

        f = self._section(inner, "Auto Buy Bait Delays")
        self._adv(f, "Press 'E' Delay (s):", "buy_e_delay", "Wait time after pressing 'E' to open shop")
        self._adv(f, "Click Delay (s):", "buy_click_delay", "Wait time between each click in bait purchase sequence")
        self._adv(f, "Type Number Delay (s):", "buy_type_delay", "Wait time after typing bait quantity")
        self._adv(f, "Anti-Detect Micro Delay (s):", "micro_delay",
                  "Tiny delay for anti-Roblox detection mouse movement (0.05 = 50ms)")

        f = self._section(inner, "Auto Store Devil Fruit Delays")
        self._adv(f, "Hotkey Press Delay (s):", "fruit_hotkey_delay", "Wait time after pressing devil fruit hotkey")
        self._adv(f, "Click Store Point Delay (s):", "fruit_store_delay", "Wait time after clicking store fruit point")
        self._adv(f, "First Shift Delay (s):", "fruit_shift_delay", "Wait time after first shift press")
        self._adv(f, "Backspace Delay (s):", "fruit_backspace_delay", "Wait time after backspace press")

        f = self._section(inner, "Auto Select Top Bait Delays")
        self._adv(f, "Bait Click Delay (s):", "bait_click_delay", "Wait time after clicking bait point")

        f = self._section(inner, "Anti-Macro Black Screen Detection")
        self._adv(f, "Black Screen Threshold (%):", "black_threshold",
                  "% of screen that must be black to trigger (0.5 = 50%)")
        self._adv(f, "Key Spam Delay (s):", "black_spam_delay",
                  "Delay between key presses when spamming to clear black screen")
        self._adv(f, "Spam Key:", "black_spam_key", "Key to spam (space, e, enter, 1...)", kind=str)
        self._adv(f, "Alarm After (s):", "black_max_sec",
                  "Ring the phone if the black screen is still there after this time")

        f = self._section(inner, "Fishing Stage Delays")
        self._adv(f, "Rod Selection Delay (s):", "rod_select_delay",
                  "Wait time between hotkey presses when selecting fishing rod")
        self._adv(f, "Cursor Anti-Detect Delay (s):", "cursor_delay",
                  "Tiny delay for anti-Roblox detection after cursor move (0.05 = 50ms)")
        self._adv(f, "Color Scan Loop Delay (s):", "scan_loop_delay",
                  "Delay between each color scan iteration (lower = faster but more CPU)")

        f = self._section(inner, "PD Control Damping (Advanced)")
        self._adv(f, "Approaching Damping Multiplier:", "approach_damping",
                  "Damping strength when bar is moving TOWARD target (higher = stronger brake)")
        self._adv(f, "Chasing Damping Multiplier:", "chase_damping",
                  "Damping strength when bar is moving AWAY from target (lower = faster chase)")
        self._adv(f, "Gap Tolerance Multiplier:", "gap_mult",
                  "Multiplier for grouping dark gray pixels (gap_tolerance = white_height * THIS)")

        f = self._section(inner, "Control State Sync")
        self._adv(f, "State Resend Interval (s):", "resend_interval",
                  "Resend mouse input every X seconds if in same state (prevents game desync)")

        # Будильник
        al = self._section(inner, "Phone Alarm")
        ttk.Checkbutton(al, text="Ring my phone if something goes wrong",
                        variable=self.var("alarm_enabled", bool)).grid(row=0, column=0, columnspan=3, sticky="w")
        self._entry(al, 1, "ntfy server:", "ntfy_server", width=24)
        self._entry(al, 2, "ntfy topic:", "ntfy_topic", width=24)
        self._entry(al, 3, "Telegram bot token:", "telegram_token", width=24, show="•")
        self._entry(al, 4, "Telegram chat id:", "telegram_chat_id", width=24)
        self._entry(al, 5, "Pushover user key:", "pushover_user", width=24, show="•")
        self._entry(al, 6, "Pushover app token:", "pushover_token", width=24, show="•")
        self._adv(al, "Repeat Alarm Every (s):", "alarm_repeat_sec", "Repeat until you press Stop Alarm or F1 (0 = once)")
        ttk.Checkbutton(al, text="Attach screenshot (Telegram)",
                        variable=self.var("alarm_screenshot", bool)).grid(row=9, column=0, columnspan=3, sticky="w")
        btns = ttk.Frame(al)
        btns.grid(row=10, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(btns, text="Test Alarm", command=self._test_alarm).pack(side="left", padx=(10, 6))
        ttk.Button(btns, text="Stop Alarm", command=self.engine.alarms.acknowledge).pack(side="left")

        # Сторож
        wd = self._section(inner, "Watchdog (When To Ring)")
        self._adv(wd, "Failed Casts In A Row:", "wd_failed_casts",
                  "No bite this many casts in a row: out of bait, disconnect, moved (0 = off)", kind=int)
        self._adv(wd, "No Fish Caught For (min):", "wd_no_catch_min", "Nothing caught for this long (0 = off)")
        self._adv(wd, "Window Lost Grace (s):", "wd_window_grace_sec",
                  "Roblox closed / not focused for this long (crash, minimized)")
        row = wd.grid_size()[1]
        ttk.Checkbutton(wd, text="Watch Roblox window",
                        variable=self.var("wd_check_window", bool)).grid(row=row, column=0, sticky="w", padx=(6, 0))
        ttk.Checkbutton(wd, text="Stop macro on alarm",
                        variable=self.var("wd_stop_on_alarm", bool)).grid(row=row + 1, column=0, sticky="w",
                                                                          padx=(6, 0))

        # Распознавание
        dt = self._section(inner, "Minigame Detection")
        ttk.Label(dt, text="Track color (blue):").grid(row=0, column=0, sticky="w", padx=(10, 10), pady=3)
        self.color_swatch = tk.Label(dt, width=10, relief="solid", bd=1)
        self.color_swatch.grid(row=0, column=1, sticky="e", padx=(0, 4))
        ttk.Button(dt, text="Pick from screen", command=self._pick_color).grid(row=1, column=1, sticky="e",
                                                                               padx=(0, 4), pady=(2, 0))
        self._update_swatch()
        self._adv(dt, "Color Tolerance:", "zone_tolerance", "How far a pixel may differ from the track color",
                  kind=int)
        self._adv(dt, "Dark Gray Max Brightness:", "dark_max", "Brightest pixel still counted as the player bar",
                  kind=int)
        row = dt.grid_size()[1]
        ttk.Label(dt, text="Player Bar Is:").grid(row=row, column=0, sticky="w", padx=(10, 10), pady=(6, 0))
        ttk.Combobox(dt, values=["dark", "blue"], width=7, state="readonly",
                     textvariable=self.var("bar_mode", str)).grid(row=row, column=1, sticky="e", padx=(0, 4))
        tk.Label(dt, text="↳ dark = dark gray bar on blue track (default), blue = the opposite",
                 fg="gray", font=("Segoe UI", 8)).grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=(30, 0))
        self._adv(dt, "Fish Line Brightness:", "fish_white_min", "Min brightness of the white fish line (0-255)",
                  kind=int)
        self._adv(dt, "Min Track Pixels:", "min_zone_pixels", "Fewer blue pixels = no minigame on screen", kind=int)
        self._adv(dt, "Minigame Scan Rate (FPS):", "scan_fps", "Screen checks per second while reeling", kind=int)
        area_row = ttk.Frame(dt)
        area_row.grid(row=dt.grid_size()[1], column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(area_row, text="Change Area", command=self.change_area).pack(side="left", padx=(10, 6))
        ttk.Button(area_row, text="Debug Preview", command=self._open_preview).pack(side="left")
        self.area_label = tk.Label(dt, text=self._area_text(), fg="green" if self.cfg["area"] else "gray")
        self.area_label.grid(row=dt.grid_size()[1], column=0, columnspan=2, sticky="w", padx=10)

        # Статистика и лог
        st = self._section(inner, "Stats & Log")
        self.stats_label = ttk.Label(st, text="Casts: 0   Catches: 0   Runtime: 0:00:00")
        self.stats_label.pack(anchor="w")
        self.log_box = tk.Text(st, height=8, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, pady=(4, 0))
        self._stats = {}

    # ---------- действия ----------
    def _apply_topmost(self):
        self.root.attributes("-topmost", bool(self.cfg["always_on_top"]))

    def _area_text(self):
        a = self.cfg["area"]
        return f"Area: X {a[0]}, Y {a[1]}, {a[2]}×{a[3]}" if a else "Area: not set (press F2)"

    def change_area(self):
        if self.engine.running:
            self.log("Сначала остановите макрос")
            return

        def done(area):
            self.cfg["area"] = area
            self.area_label.config(text=self._area_text(), fg="green")
            self._save()
            self.log(f"Область мини-игры: {area}")
        AreaSelector(self.root, self.cfg["area"], done)

    def _pick_color(self):
        def done(point):
            def grab():
                rgb = screen.grab((point[0], point[1], 1, 1))[0, 0]
                self.cfg["zone_color"] = [int(c) for c in rgb]
                self._update_swatch()
                self._save()
                self.log(f"Цвет трека: {self.cfg['zone_color']}")
            self.root.after(150, grab)  # ждём, пока оверлей исчезнет
        PointPicker(self.root, "Кликните по синему треку мини-игры", done)

    def _update_swatch(self):
        r, g, b = self.cfg["zone_color"]
        self.color_swatch.config(bg=f"#{r:02x}{g:02x}{b:02x}", text=f"{r},{g},{b}",
                                 fg="black" if r + g + b > 380 else "white")

    def _test_alarm(self):
        names = alarm.channels(self.cfg)
        if not names:
            self.log("Заполните хотя бы один канал: ntfy topic, Telegram или Pushover")
            return
        self.log("Отправляю тестовую тревогу: " + ", ".join(names))

        def job():
            try:
                png = screen.screenshot_png() if self.cfg.get("alarm_screenshot") else None
            except Exception:
                png = None
            alarm.send_alarm(self.cfg, "Тестовая тревога — всё работает", png,
                             log=lambda t: self.events.put(("log", t)))
        threading.Thread(target=job, daemon=True).start()

    def _open_preview(self):
        if self._preview and self._preview.winfo_exists():
            self._preview.lift()
            return
        from .preview import Preview
        self._preview = Preview(self.root, self.cfg, self.engine)

    def _rebind(self, key):
        if keyboard is None:
            self.log("pynput не установлен — горячие клавиши недоступны")
            return
        self._rebinding = key
        self.hotkey_labels[key].config(text="...", fg="orange")

    def log(self, text):
        self.log_box.config(state="normal")
        self.log_box.insert("end", time.strftime("[%H:%M:%S] ") + text + "\n")
        self.log_box.see("end")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_box.delete("1.0", f"{lines - 500}.0")
        self.log_box.config(state="disabled")

    def exit(self):
        self.engine.stop()
        self._save()
        if getattr(self, "listener", None):
            self.listener.stop()
        self.root.destroy()

    # ---------- горячие клавиши и события из потоков ----------
    def _start_hotkeys(self):
        self.listener = None
        if keyboard is None:
            self.log("pynput не установлен — горячие клавиши F1/F2/F3 не работают")
            return
        self.listener = keyboard.Listener(on_press=lambda k: self.events.put(("key", key_name(k))))
        self.listener.daemon = True
        self.listener.start()

    def _on_key(self, name):
        if not name:
            return
        if self._rebinding:
            key, self._rebinding = self._rebinding, None
            self.cfg[key] = name
            self.hotkey_labels[key].config(text=name.upper(), fg="blue")
            self._save()
            return
        if name == self.cfg["hotkey_start_stop"]:
            self.engine.toggle()
        elif name == self.cfg["hotkey_change_area"]:
            self.change_area()
        elif name == self.cfg["hotkey_exit"]:
            self.exit()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "key":
                    self._on_key(payload)
                    if not self.root.winfo_exists():
                        return
                elif kind == "log":
                    self.log(payload)
                elif kind == "status":
                    text, color = payload
                    self.status.config(text=f"Status: {text}", fg=color)
                elif kind == "stats":
                    self._stats = payload
        except queue.Empty:
            pass
        except tk.TclError:
            return  # окно уже закрыто
        self._refresh_stats()
        self.root.after(50, self._poll)

    def _refresh_stats(self):
        s = self._stats
        started = s.get("started")
        runtime = int(time.time() - started) if started and self.engine.running else 0
        h, rem = divmod(runtime, 3600)
        self.stats_label.config(text=f"Casts: {s.get('casts', 0)}   Catches: {s.get('catches', 0)}   "
                                     f"Runtime: {h}:{rem // 60:02d}:{rem % 60:02d}")

    def run(self):
        self.root.mainloop()
