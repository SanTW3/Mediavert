"""Окно управления макросом (CustomTkinter): тёмная тема, карточки, вкладки как в оригинале."""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk

import customtkinter as ctk

from . import alarm, config, screen
from .blackout import Blackout
from .engine import MacroEngine
from .overlay import AreaSelector, PointPicker
from .remote import Remote, webapp_link
from .tgcontrol import TelegramControl, status_text

try:
    from pynput import keyboard, mouse
except ImportError:  # без pynput горячие клавиши не работают, но окно открывается
    keyboard = mouse = None

# Флаги «событие сгенерировано программой» в low-level хуках Windows:
# так отличаем свои нажатия (SendInput макроса) от нажатий человека
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

DIGITS = [str(d) for d in range(10)]

# Палитра — морская тема в духе Persona 3 Reload (macro/theme.py)
from .theme import (ACCENT, ACCENT_HOVER, BG, CARD, CARD_BORDER, FIELD, GREEN, GREEN_HOVER,  # noqa: E402
                    HILITE, MUTED, ORANGE, RED, RED_HOVER, TEXT, WARN_BG)
from . import theme  # noqa: E402

STATUS_COLORS = {"red": RED, "green": GREEN, "blue": ACCENT, "orange": ORANGE}

FONT = "Segoe UI"
# Подписи внутри карточек — обычные tk.Label со сплошным фоном: прозрачные CTkLabel
# в прокручиваемой области не успевают перерисоваться и оставляют «следы» при быстрой прокрутке
TK_TITLE = (FONT, 11, "bold")
TK_SUB = (FONT, 8)
TK_LABEL = (FONT, 10)
TK_HINT = (FONT, 8)


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
    return f"X {p[0]}  ·  Y {p[1]}" if p else "Not set"


def smooth_scrolling(frame):
    """Прокрутка без «рваных» полос на Windows.

    Каждый виджет внутри CTkScrollableFrame — отдельное окно Windows, и при быстрой прокрутке
    система перерисовывает их по очереди: старые кадры остаются полосами поверх новых.
    Поэтому на время сдвига отключаем перерисовку холста (WM_SETREDRAW), даём Tk расставить
    все виджеты на новые места и перерисовываем холст со всеми детьми одним разом.
    """
    if sys.platform != "win32":
        return frame
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.RedrawWindow.argtypes = (wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
    wm_setredraw = 0x000B
    # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW | RDW_FRAME
    flags = 0x0001 | 0x0004 | 0x0080 | 0x0100 | 0x0400
    canvas = frame._parent_canvas
    original = canvas.yview

    def yview(*args):
        if not args:
            return original()
        hwnd = canvas.winfo_id()
        user32.SendMessageW(hwnd, wm_setredraw, 0, 0)
        try:
            original(*args)
            canvas.update_idletasks()
        finally:
            user32.SendMessageW(hwnd, wm_setredraw, 1, 0)
            user32.RedrawWindow(hwnd, None, None, flags)

    canvas.yview = yview  # колесо мыши (CTk вызывает self._parent_canvas.yview)
    frame._scrollbar.configure(command=yview)  # перетаскивание полосы прокрутки
    return frame


def resource(name):
    """Путь к файлу из assets/ и в исходниках, и внутри exe (PyInstaller)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", name)


class App:
    def __init__(self):
        self.cfg = config.load()
        self.events = queue.Queue()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        # Резкие углы, как в меню P3R
        for widget, radius in (("CTkButton", 2), ("CTkEntry", 2), ("CTkOptionMenu", 2),
                               ("CTkSegmentedButton", 2), ("CTkTextbox", 2)):
            ctk.ThemeManager.theme[widget]["corner_radius"] = radius
        self.root = ctk.CTk(fg_color=BG)
        self.root.title("GPO Fishing Macro")
        self.root.geometry("600x760")
        self.root.minsize(600, 600)  # шапка-картинка рассчитана на 600 px
        try:
            if sys.platform == "win32":
                self.root.iconbitmap(resource("icon.ico"))
        except tk.TclError:
            pass
        # Узкий шрифт заголовков (Bahnschrift Condensed в Windows 10/11)
        global TK_TITLE
        self.display = theme.display_family(self.root)
        TK_TITLE = (self.display, 12, "bold", "italic")
        self.vars = {}
        self._save_job = None
        self._rebinding = None
        self._preview = None
        self._overlay = None
        self._stats = {}
        self._was_running = None

        self.engine = MacroEngine(
            self.cfg,
            log=lambda text: self.events.put(("log", text)),
            on_status=lambda text, color: self.events.put(("status", (text, color))),
            on_stats=lambda stats: self.events.put(("stats", stats)),
        )

        self.blackout = Blackout(self.root, self.log_threadsafe)
        self._blackout_job = None
        self._status_text = "Stopped"
        self.tg = TelegramControl(self.cfg, lambda cmd: self.events.put(("tg", cmd)), self.log_threadsafe,
                                  webapp_link=self._webapp_link)
        self.remote = Remote(self.cfg, self._remote_status, lambda cmd: self.events.put(("tg", cmd)),
                             self.log_threadsafe)

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
        v = tk.BooleanVar(value=bool(value)) if kind is bool else tk.StringVar(value=str(value))

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

    # ---------- строительные блоки ----------
    def _card(self, parent, title, subtitle=None):
        card = ctk.CTkFrame(parent, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=4)
        card.pack(fill="x", pady=(0, 10), padx=2)
        # Заголовок карточки — как плашки меню P3R: курсив капсом и циановая черта слева
        tk.Frame(card, bg=HILITE, width=4).place(x=1, y=13, width=4, height=20)
        tk.Label(card, text=title.upper(), font=TK_TITLE, fg=HILITE, bg=CARD).pack(
            anchor="w", padx=16, pady=(10, 0 if subtitle else 6))
        if subtitle:
            tk.Label(card, text=subtitle, font=TK_SUB, fg=MUTED, bg=CARD).pack(anchor="w", padx=16, pady=(0, 6))
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="x", padx=16, pady=(0, 12))
        body.columnconfigure(0, weight=1)
        return body

    def _row(self, body):
        return body.grid_size()[1]

    def _label(self, body, row, text, hint=None):
        box = tk.Frame(body, bg=CARD)
        box.grid(row=row, column=0, sticky="w", pady=4)
        tk.Label(box, text=text, font=TK_LABEL, fg=TEXT, bg=CARD, anchor="w").pack(anchor="w")
        if hint:
            tk.Label(box, text=hint, font=TK_HINT, fg=MUTED, bg=CARD, anchor="w",
                     justify="left", wraplength=340).pack(anchor="w")
        return box

    def _num(self, body, text, key, hint=None, kind=float, unit=None, width=90):
        row = self._row(body)
        self._label(body, row, text, hint)
        right = tk.Frame(body, bg=CARD)
        right.grid(row=row, column=1, sticky="e", pady=4)
        ctk.CTkEntry(right, width=width, textvariable=self.var(key, kind), fg_color=FIELD,
                     border_color=CARD_BORDER, justify="center").pack(side="left")
        if unit:
            tk.Label(right, text=unit, font=TK_LABEL, fg=MUTED, bg=CARD, width=3).pack(side="left", padx=(6, 0))

    def _text(self, body, text, key, hint=None, secret=False):
        row = self._row(body)
        self._label(body, row, text, hint)
        ctk.CTkEntry(body, width=230, textvariable=self.var(key, str), fg_color=FIELD, border_color=CARD_BORDER,
                     show="•" if secret else "").grid(row=row, column=1, sticky="e", pady=4)

    def _switch(self, body, text, key, hint=None, command=None):
        row = self._row(body)
        self._label(body, row, text, hint)
        sw = ctk.CTkSwitch(body, text="", variable=self.var(key, bool), onvalue=True, offvalue=False,
                           progress_color=ACCENT, command=command, width=46)
        sw.grid(row=row, column=1, sticky="e", pady=4)
        return sw

    def _option(self, body, text, key, values, hint=None):
        row = self._row(body)
        self._label(body, row, text, hint)
        ctk.CTkOptionMenu(body, values=values, variable=self.var(key, str), width=90, fg_color=FIELD,
                          button_color=CARD_BORDER, button_hover_color=ACCENT).grid(row=row, column=1, sticky="e",
                                                                                     pady=4)

    def _point(self, body, button, key, hint):
        row = self._row(body)
        lbl = tk.Label(body, text=fmt_point(self.cfg.get(key)), font=TK_LABEL, bg=CARD,
                       fg=GREEN if self.cfg.get(key) else MUTED)

        def pick():
            def done(point):
                self.cfg[key] = point
                lbl.configure(text=fmt_point(point), fg=GREEN)
                self._save()
            self._open_overlay(lambda: PointPicker(self.root, hint, done))

        ctk.CTkButton(body, text=button, width=170, height=32, fg_color=FIELD, hover_color=ACCENT,
                      border_color=CARD_BORDER, border_width=1, anchor="w", command=pick).grid(
            row=row, column=0, sticky="w", pady=4)
        lbl.grid(row=row, column=1, sticky="e", pady=4)

    def _buttons(self, body, *buttons):
        row = tk.Frame(body, bg=CARD)
        row.grid(row=self._row(body), column=0, columnspan=2, sticky="w", pady=(8, 2))
        for text, command, color, hover in buttons:
            ctk.CTkButton(row, text=text, command=command, width=130, height=32, fg_color=color,
                          hover_color=hover).pack(side="left", padx=(0, 8))
        return row

    def _scroll(self, tab):
        frame = smooth_scrolling(ctk.CTkScrollableFrame(tab, fg_color="transparent",
                                                        scrollbar_button_color=CARD_BORDER))
        frame.pack(fill="both", expand=True)
        return frame

    # ---------- построение окна ----------
    def _build(self):
        root = self.root
        # Шапка — картинка, нарисованная один раз (theme.render_banner), поверх неё статус и Start
        banner = theme.render_banner(600, 110)
        self._banner = ctk.CTkImage(banner, size=(600, 110))
        header = ctk.CTkLabel(root, text="", image=self._banner, fg_color=BG, height=110)
        header.pack(fill="x", pady=(0, 8))
        self.status = ctk.CTkLabel(header, text="  ●  STOPPED  ", font=(self.display, 13, "bold", "italic"),
                                   text_color="white", fg_color=RED, corner_radius=0, height=22)
        self.status.place(x=22, y=70)
        self.start_btn = ctk.CTkButton(header, text="▶  START", width=150, height=48, corner_radius=0,
                                       font=(self.display, 20, "bold", "italic"), fg_color=GREEN,
                                       hover_color=GREEN_HOVER, border_width=2, border_color=HILITE,
                                       command=self.engine.toggle)
        self.start_btn.place(relx=1, x=-20, y=26, anchor="ne")
        self.start_hint = ctk.CTkLabel(header, text="", font=(FONT, 11, "bold"), text_color=HILITE,
                                       fg_color="#0b3478", corner_radius=0, height=18)
        self.start_hint.place(relx=1, x=-20, y=78, anchor="ne")

        stats = ctk.CTkFrame(root, fg_color="transparent")
        stats.pack(fill="x", padx=16, pady=(0, 8))
        self.stat_values = {}
        for i, (key, title) in enumerate((("catches", "Catches"), ("casts", "Casts"), ("runtime", "Runtime"),
                                          ("rate", "Fish / hour"))):
            stats.columnconfigure(i, weight=1, uniform="stats")
            card = ctk.CTkFrame(stats, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=4)
            card.grid(row=0, column=i, sticky="ew", padx=3)
            tk.Frame(card, bg=HILITE, height=3).place(x=1, y=1, relwidth=0.35, height=3)
            value = ctk.CTkLabel(card, text="0", font=(self.display, 26, "bold", "italic"), text_color=TEXT)
            value.pack(pady=(8, 0))
            ctk.CTkLabel(card, text=title.upper(), font=(self.display, 11, "italic"),
                         text_color=HILITE).pack(pady=(0, 8))
            self.stat_values[key] = value

        tabs = ctk.CTkTabview(root, fg_color=BG, segmented_button_fg_color=CARD,
                              segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT_HOVER,
                              segmented_button_unselected_color=CARD,
                              segmented_button_unselected_hover_color=FIELD, corner_radius=4)
        tabs._segmented_button.configure(font=(self.display, 13, "bold", "italic"), corner_radius=2)
        tabs.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        for title, builder in (("General", self._tab_general), ("Casting", self._tab_casting),
                               ("Fishing", self._tab_fishing), ("Pre-Cast", self._tab_precast),
                               ("Literally Everything Else", self._tab_else)):
            builder(tabs.add(title))
        self._refresh_hotkey_hint()

    # --- General ---
    def _tab_general(self, tab):
        page = self._scroll(tab)
        hk = self._card(page, "Hotkeys", "Work everywhere, even when the game window is active")
        self.hotkey_labels = {}
        for label, key in (("Start / Stop", "hotkey_start_stop"), ("Change Area", "hotkey_change_area"),
                           ("Exit", "hotkey_exit"), ("Screen Off / On", "hotkey_blackout")):
            row = self._row(hk)
            self._label(hk, row, label)
            right = tk.Frame(hk, bg=CARD)
            right.grid(row=row, column=1, sticky="e", pady=4)
            cap = ctk.CTkLabel(right, text=self.cfg[key].upper(), width=56, height=30, corner_radius=2,
                               fg_color=FIELD, text_color=ACCENT, font=(FONT, 13, "bold"))
            cap.pack(side="left", padx=(0, 8))
            ctk.CTkButton(right, text="Rebind", width=80, height=30, fg_color="transparent", border_width=1,
                          border_color=CARD_BORDER, hover_color=FIELD,
                          command=lambda k=key: self._rebind(k)).pack(side="left")
            self.hotkey_labels[key] = cap

        win = self._card(page, "Window")
        self._switch(win, "Always on Top", "always_on_top", command=self._apply_topmost)

        bo = self._card(page, "Screen Off While Fishing",
                        "Black screen for you, the macro still sees the game")
        self._switch(bo, "Turn screen black when the macro starts", "blackout_enabled",
                     "Any key / mouse button, the hotkey or the Telegram button turns it back on")
        self._switch(bo, "All monitors", "blackout_all_monitors", "Off = only the monitor with Roblox")
        self._num(bo, "Delay before going black", "blackout_delay", "After pressing Start", unit="s")

        nh = self._card(page, "Number Hotkeys", "Hotbar slots in the game")
        self._option(nh, "Rod Hotkey", "rod_hotkey", DIGITS, "Slot with your fishing rod")
        self._option(nh, "Anything Else Hotkey", "other_hotkey", DIGITS,
                     "Any other slot; pressed before the rod so the rod is always equipped")

    # --- Casting ---
    def _tab_casting(self, tab):
        page = self._scroll(tab)
        wp = self._card(page, "Water Point", "Where the cursor aims when casting")
        self._point(wp, "Set Water Point", "water_point", "Кликните по воде, куда забрасывать удочку")
        ct = self._card(page, "Cast Timing")
        self._num(ct, "Cast Hold Duration", "cast_hold", "How long to hold the mouse to cast", unit="s")
        self._num(ct, "Recast Timeout", "recast_timeout", "Recast if no bite after this long", unit="s")

    # --- Fishing ---
    def _tab_fishing(self, tab):
        page = self._scroll(tab)
        pd = self._card(page, "PD Control Parameters", "Keeps your dark bar on the white fish line")
        self._num(pd, "Kp (Proportional)", "kp", "How hard the bar is pulled towards the fish")
        self._num(pd, "Kd (Derivative)", "kd", "Damping: raise it if the bar overshoots the fish")
        self._num(pd, "PD Clamp", "pd_clamp", "Limit of the controller output")
        tm = self._card(page, "Timing")
        self._num(tm, "Fish End Delay", "fish_end_delay", "Pause after the minigame ends", unit="s")

    # --- Pre-Cast ---
    def _tab_precast(self, tab):
        page = self._scroll(tab)

        def section(flag_key, title, hint, build):
            card = self._card(page, title, hint)
            settings = tk.Frame(card, bg=CARD)
            settings.columnconfigure(0, weight=1)
            build(settings)

            def toggle(*_):
                if self.vars[flag_key].get():
                    settings.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
                else:
                    settings.grid_forget()
            self._switch(card, "Enabled", flag_key)
            self.vars[flag_key].trace_add("write", toggle)
            toggle()

        def buy(f):
            tk.Label(f, text="Order: E → Left → Middle → amount → Left → Right → Middle",
                     font=TK_HINT, fg=MUTED, bg=CARD).grid(row=self._row(f), column=0, columnspan=2,
                                                                 sticky="w", pady=(0, 4))
            self._point(f, "Set Left Point", "buy_left", "Кликните по левой кнопке покупки")
            self._point(f, "Set Middle Point", "buy_middle", "Кликните по средней кнопке покупки")
            self._point(f, "Set Right Point", "buy_right", "Кликните по правой кнопке покупки")
            self._num(f, "Loops Per Purchase", "loops_per_purchase", "Buy bait every N loops", kind=int)
            self._switch(f, "Type bait amount", "buy_type_amount", "Types Loops Per Purchase after the first Middle click")

        def fruit(f):
            self._option(f, "Fruit Hotkey", "fruit_hotkey", DIGITS)
            self._point(f, "Set Store Point", "store_point", "Кликните по кнопке Store")

        def bait(f):
            self._point(f, "Set Bait Point", "bait_point", "Кликните по верхней наживке в списке")

        section("auto_buy_bait", "Auto Buy Common Bait", "Stand next to the bait seller", buy)
        section("auto_store_fruit", "Auto Store Devil Fruit", "Stores fished devil fruits", fruit)
        section("auto_select_bait", "Auto Select Top Bait", "Clicks the top bait before every cast", bait)

    # --- Literally Everything Else ---
    def _tab_else(self, tab):
        warn = ctk.CTkFrame(tab, fg_color=WARN_BG, border_color=RED, border_width=1, corner_radius=2)
        warn.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(warn, text="⚠  DON'T CHANGE IF YOU DON'T KNOW WHAT YOU'RE DOING",
                     font=(FONT, 13, "bold"), text_color="#ff7b72").pack(padx=12, pady=8)
        # Подразделы: в каждом немного карточек — прокрутка короче и не тормозит
        groups = ["Delays", "Anti-Macro & PD", "Phone Alarm", "Detection", "Log"]
        pages = {}
        holder = ctk.CTkFrame(tab, fg_color="transparent")

        def show(name):
            for n, p in pages.items():
                if n == name:
                    p.pack(fill="both", expand=True)
                else:
                    p.pack_forget()

        nav = ctk.CTkSegmentedButton(tab, values=groups, command=show, fg_color=CARD,
                                     selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                     unselected_color=CARD, unselected_hover_color=FIELD)
        nav.pack(fill="x", pady=(0, 8))
        holder.pack(fill="both", expand=True)
        for name in groups:
            pages[name] = smooth_scrolling(ctk.CTkScrollableFrame(holder, fg_color="transparent",
                                                                  scrollbar_button_color=CARD_BORDER))

        page = pages["Delays"]
        f = self._card(page, "Roblox Window Focus")
        self._num(f, "Roblox Focus Delay (s)", "focus_delay",
                  "Time to wait after focusing Roblox window before continuing")
        self._num(f, "Post-Focus Extra Delay (s)", "post_focus_delay",
                  "Additional delay after Roblox window is focused (for stability)")

        f = self._card(page, "Auto Buy Bait Delays")
        self._num(f, "Press 'E' Delay (s)", "buy_e_delay", "Wait time after pressing 'E' to open shop")
        self._num(f, "Click Delay (s)", "buy_click_delay", "Wait time between each click in bait purchase sequence")
        self._num(f, "Type Number Delay (s)", "buy_type_delay", "Wait time after typing bait quantity")
        self._num(f, "Anti-Detect Micro Delay (s)", "micro_delay",
                  "Tiny delay for anti-Roblox detection mouse movement (0.05 = 50ms)")

        f = self._card(page, "Auto Store Devil Fruit Delays")
        self._num(f, "Hotkey Press Delay (s)", "fruit_hotkey_delay", "Wait time after pressing devil fruit hotkey")
        self._num(f, "Click Store Point Delay (s)", "fruit_store_delay", "Wait time after clicking store fruit point")
        self._num(f, "First Shift Delay (s)", "fruit_shift_delay", "Wait time after first shift press")
        self._num(f, "Backspace Delay (s)", "fruit_backspace_delay", "Wait time after backspace press")

        f = self._card(page, "Auto Select Top Bait Delays")
        self._num(f, "Bait Click Delay (s)", "bait_click_delay", "Wait time after clicking bait point")

        f = self._card(pages["Anti-Macro & PD"], "Anti-Macro Black Screen Detection")
        self._num(f, "Black Screen Threshold (%)", "black_threshold",
                  "% of screen that must be black to trigger (0.5 = 50%)")
        self._num(f, "Key Spam Delay (s)", "black_spam_delay",
                  "Delay between key presses when spamming to clear black screen")
        self._num(f, "Spam Key", "black_spam_key", "Key to spam (space, e, enter, 1...)", kind=str)
        self._num(f, "Alarm After (s)", "black_max_sec",
                  "Ring the phone if the black screen is still there after this time")

        f = self._card(page, "Fishing Stage Delays")
        self._num(f, "Rod Selection Delay (s)", "rod_select_delay",
                  "Wait time between hotkey presses when selecting fishing rod")
        self._num(f, "Cursor Anti-Detect Delay (s)", "cursor_delay",
                  "Tiny delay for anti-Roblox detection after cursor move (0.05 = 50ms)")
        self._num(f, "Color Scan Loop Delay (s)", "scan_loop_delay",
                  "Delay between each color scan iteration (lower = faster but more CPU)")

        page = pages["Anti-Macro & PD"]
        f = self._card(page, "PD Control Damping (Advanced)")
        self._num(f, "Approaching Damping Multiplier", "approach_damping",
                  "Damping strength when bar is moving TOWARD target (higher = stronger brake)")
        self._num(f, "Chasing Damping Multiplier", "chase_damping",
                  "Damping strength when bar is moving AWAY from target (lower = faster chase)")
        self._num(f, "Gap Tolerance Multiplier", "gap_mult",
                  "Multiplier for grouping dark gray pixels (gap_tolerance = white_height * THIS)")

        f = self._card(page, "Control State Sync")
        self._num(f, "State Resend Interval (s)", "resend_interval",
                  "Resend mouse input every X seconds if in same state (prevents game desync)")

        page = pages["Phone Alarm"]
        al = self._card(page, "Phone Alarm", "Rings your phone when something goes wrong")
        self._switch(al, "Ring my phone", "alarm_enabled", "When something goes wrong (see Watchdog)")
        self._text(al, "ntfy server", "ntfy_server")
        self._text(al, "ntfy topic", "ntfy_topic", "Same topic as in the ntfy app")
        self._text(al, "Telegram bot token", "telegram_token", secret=True)
        self._text(al, "Telegram chat id", "telegram_chat_id")
        self._text(al, "Pushover user key", "pushover_user", secret=True)
        self._text(al, "Pushover app token", "pushover_token", secret=True)
        self._num(al, "Repeat Alarm Every (s)", "alarm_repeat_sec",
                  "Repeat until you press Stop Alarm or Start (0 = once)")
        self._switch(al, "Attach screenshot (Telegram)", "alarm_screenshot")
        self._switch(al, "Telegram control buttons", "tg_control",
                     "Bot buttons: screen on/off, screenshot, status, stop. Send /menu to the bot")

        ma = self._card(page, "Telegram Mini App", "Live panel inside Telegram: stats, start/stop, screen, alarm")
        self._switch(ma, "Enable Mini App", "remote_enabled",
                     "Status and commands go through ntfy under a secret topic name")
        self._text(ma, "Mini App URL", "webapp_url", "Page on GitHub Pages")
        self._buttons(ma, ("Send panel to Telegram", lambda: self._on_tg("panel"), ACCENT, ACCENT_HOVER),
                      ("New secret link", self._new_secret, FIELD, CARD_BORDER))
        self._buttons(al, ("Test Alarm", self._test_alarm, ACCENT, ACCENT_HOVER),
                      ("Stop Alarm", self.engine.alarms.acknowledge, FIELD, CARD_BORDER))

        wd = self._card(page, "Watchdog", "When to ring")
        self._num(wd, "Failed Casts In A Row", "wd_failed_casts",
                  "No bite this many casts in a row: out of bait, disconnect, moved (0 = off)", kind=int)
        self._num(wd, "No Fish Caught For (min)", "wd_no_catch_min", "Nothing caught for this long (0 = off)")
        self._num(wd, "Window Lost Grace (s)", "wd_window_grace_sec",
                  "Roblox closed / not focused for this long (crash, minimized)")
        self._switch(wd, "Watch Roblox window", "wd_check_window")
        self._switch(wd, "Stop macro on alarm", "wd_stop_on_alarm")

        page = pages["Detection"]
        dt = self._card(page, "Minigame Detection")
        row = self._row(dt)
        self._label(dt, row, "Track color (blue)", "Click the blue track of the minigame to re-pick it")
        right = tk.Frame(dt, bg=CARD)
        right.grid(row=row, column=1, sticky="e", pady=4)
        self.color_swatch = ctk.CTkLabel(right, text="", width=70, height=28, corner_radius=6)
        self.color_swatch.pack(side="left", padx=(0, 6))
        ctk.CTkButton(right, text="Pick", width=60, height=28, fg_color=FIELD, hover_color=ACCENT,
                      command=self._pick_color).pack(side="left")
        self._update_swatch()
        self._num(dt, "Color Tolerance", "zone_tolerance", "How far a pixel may differ from the track color", kind=int)
        self._num(dt, "Dark Gray Max Brightness", "dark_max", "Brightest pixel still counted as the player bar",
                  kind=int)
        self._option(dt, "Player Bar Is", "bar_mode", ["dark", "blue"],
                     "dark = dark gray bar on blue track (default), blue = the opposite")
        self._switch(dt, "Require green progress bar", "require_progress",
                     "Minigame counts only if the green bar is next to the track (stops false M1 spam)")
        self._num(dt, "Fish Line Brightness", "fish_white_min", "Min brightness of the white fish line (0-255)",
                  kind=int)
        self._num(dt, "Min Track Pixels", "min_zone_pixels", "Fewer blue pixels = no minigame on screen", kind=int)
        self._num(dt, "Minigame Scan Rate (FPS)", "scan_fps", "Screen checks per second while reeling", kind=int)
        self.area_label = tk.Label(dt, text=self._area_text(), font=TK_LABEL, bg=CARD,
                                   fg=GREEN if self.cfg["area"] else MUTED)
        self.area_label.grid(row=self._row(dt), column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._buttons(dt, ("Change Area", self.change_area, ACCENT, ACCENT_HOVER),
                      ("Debug Preview", self._open_preview, FIELD, CARD_BORDER))

        lg = self._card(pages["Log"], "Log")
        self.log_box = ctk.CTkTextbox(lg, height=330, fg_color=FIELD, font=("Consolas", 11), wrap="word",
                                      state="disabled")
        self.log_box.grid(row=0, column=0, columnspan=2, sticky="ew")
        nav.set("Delays")
        show("Delays")

    # ---------- действия ----------
    def _apply_topmost(self):
        self.root.attributes("-topmost", bool(self.cfg["always_on_top"]))

    def _area_text(self):
        a = self.cfg["area"]
        return f"Area: X {a[0]}, Y {a[1]}, {a[2]}×{a[3]}" if a else "Area: not set (press F2 during a minigame)"

    def _overlay_open(self):
        return self._overlay is not None and not self._overlay.closed

    def _close_overlay(self):
        if self._overlay_open():
            self._overlay.cancel()
        self._overlay = None

    def _open_overlay(self, factory):
        """Только один оверлей за раз: повторный вызов закрывает открытый."""
        if self._overlay_open():
            self._close_overlay()
            return
        self._overlay = factory()

    def change_area(self):
        if self._overlay_open():  # повторный F2 — закрыть выбор
            self._close_overlay()
            return
        if self.engine.running:
            self.log("Сначала остановите макрос")
            return

        def done(area):
            self.cfg["area"] = area
            self.area_label.configure(text=self._area_text(), fg=GREEN)
            self._save()
            self.log(f"Область мини-игры: {area}")
        self._open_overlay(lambda: AreaSelector(self.root, self.cfg["area"], done))

    def _pick_color(self):
        def done(point):
            def grab():
                rgb = screen.grab((point[0], point[1], 1, 1))[0, 0]
                self.cfg["zone_color"] = [int(c) for c in rgb]
                self._update_swatch()
                self._save()
                self.log(f"Цвет трека: {self.cfg['zone_color']}")
            self.root.after(150, grab)  # ждём, пока оверлей исчезнет
        self._open_overlay(lambda: PointPicker(self.root, "Кликните по синему треку мини-игры", done))

    def _update_swatch(self):
        r, g, b = self.cfg["zone_color"]
        self.color_swatch.configure(fg_color=f"#{r:02x}{g:02x}{b:02x}", text=f"{r},{g},{b}",
                                    text_color="black" if r + g + b > 380 else "white", font=(FONT, 10))

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
        self.hotkey_labels[key].configure(text="...", text_color=ORANGE)

    def _refresh_hotkey_hint(self):
        self.start_hint.configure(text=f"  OR PRESS {self.cfg['hotkey_start_stop'].upper()}  ")

    def log_threadsafe(self, text):
        self.events.put(("log", text))

    # ---------- чёрный экран ----------
    def _blackout_on(self):
        self._blackout_job = None
        if self.blackout.show(bool(self.cfg["blackout_all_monitors"])):
            self.tg.send("🌑 Экран затемнён, макрос ловит рыбу")

    def _blackout_off(self, notify=False):
        if self._blackout_job:
            self.root.after_cancel(self._blackout_job)
            self._blackout_job = None
        if self.blackout.active:
            self.blackout.hide()
            if notify:
                self.tg.send("🔆 Экран включён", buttons=False)

    def _webapp_link(self):
        if not (self.cfg.get("remote_enabled") and str(self.cfg.get("webapp_url") or "").startswith("https://")):
            return None
        return webapp_link(self.cfg)

    def _remote_status(self):
        """Снимок для мини-приложения (вызывается из фонового потока)."""
        s = self.engine.stats
        started = s.get("started")
        running = self.engine.running
        runtime = int(time.time() - started) if started and running else 0
        alarms = self.engine.alarms
        return {
            "running": running,
            "state": self._status_text,
            "catches": s.get("catches", 0),
            "casts": s.get("casts", 0),
            "runtime": runtime // 10 * 10,  # с точностью до 10 с, чтобы не слать статус каждую секунду
            "blackout": self.blackout.active,
            "alarm": alarms.reason if alarms.active else None,
        }

    def _new_secret(self):
        self.cfg["remote_secret"] = config.new_secret()
        self._save()
        self.tg.refresh_menu_button()
        self.log("Новая секретная ссылка создана — старая панель больше не работает. "
                 "Нажмите «Send panel to Telegram»")

    def _on_tg(self, cmd):
        self.remote.poke()
        if cmd == "start":
            if not self.engine.running:
                self.engine.start()
            return
        if cmd == "alarm_off":
            self.engine.alarms.acknowledge()
            return
        if cmd == "panel":
            if self._webapp_link():
                self.tg.send("📱 Панель управления макросом — кнопка ниже или «Панель» слева от поля ввода")
            else:
                self.log("Мини-приложение выключено или не задан https-адрес")
            return
        if cmd == "screen_on":
            self._blackout_off()
            self.tg.send("🔆 Экран включён")
        elif cmd == "screen_off":
            self._blackout_on()
        elif cmd == "stop":
            self.engine.stop()  # сообщение «Макрос остановлен» пришлёт _refresh_running
        elif cmd == "shot":
            def job():
                try:
                    png = screen.screenshot_png()  # чёрное окно в захват не попадает — видна игра
                except Exception as exc:
                    self.log_threadsafe(f"Скриншот не удался: {exc}")
                    return
                self.tg.send_photo(png, status_text(self.engine, self.blackout.active))
            threading.Thread(target=job, daemon=True).start()
        elif cmd in ("status", "menu"):
            self.tg.send(status_text(self.engine, self.blackout.active))

    def log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("[%H:%M:%S] ") + text + "\n")
        self.log_box.see("end")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_box.delete("1.0", f"{lines - 500}.0")
        self.log_box.configure(state="disabled")

    def exit(self):
        self.engine.stop()
        self._blackout_off()
        self.tg.stop()
        self.remote.stop()
        self._save()
        if getattr(self, "listener", None):
            self.listener.stop()
        if getattr(self, "mouse_listener", None):
            self.mouse_listener.stop()
        self.root.destroy()

    # ---------- горячие клавиши и события из потоков ----------
    def _start_hotkeys(self):
        self.listener = None
        if keyboard is None:
            self.log("pynput не установлен — горячие клавиши F1/F2/F3 не работают")
            return
        def real_keys_only(msg, data):
            return not (data.flags & LLKHF_INJECTED)

        def real_clicks_only(msg, data):
            return not (data.flags & LLMHF_INJECTED)

        win = sys.platform == "win32"
        self.listener = keyboard.Listener(on_press=lambda k: self.events.put(("key", key_name(k))),
                                          **({"win32_event_filter": real_keys_only} if win else {}))
        self.listener.daemon = True
        self.listener.start()
        # Мышь слушаем только ради «разбудить экран»: кнопки, не движение
        self.mouse_listener = mouse.Listener(
            on_click=lambda x, y, button, pressed: pressed and self.events.put(("click", None)),
            **({"win32_event_filter": real_clicks_only} if win else {}))
        self.mouse_listener.daemon = True
        self.mouse_listener.start()

    def _on_key(self, name):
        if not name:
            return
        if self._rebinding:
            key, self._rebinding = self._rebinding, None
            self.cfg[key] = name
            self.hotkey_labels[key].configure(text=name.upper(), text_color=ACCENT)
            self._refresh_hotkey_hint()
            self._save()
            return
        if name == self.cfg["hotkey_blackout"]:
            if self.blackout.active:
                self._blackout_off(notify=True)
            else:
                self._blackout_on()
            return
        if self.blackout.active:
            # Любая клавиша будит экран и больше ничего не делает
            self._blackout_off(notify=True)
            return
        if self._overlay_open():
            # Пока открыт выбор области/точки — только отмена, остальные горячие клавиши молчат
            if name == "esc":
                self._close_overlay()
            elif name == self.cfg["hotkey_change_area"]:
                self.change_area()
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
                    self._status_text = text
                    self.remote.poke()
                    self.status.configure(text=f"  ●  {text.upper()}  ", fg_color=STATUS_COLORS.get(color, color))
                elif kind == "stats":
                    self._stats = payload
                    self.remote.poke()
                elif kind == "click":
                    if self.blackout.active:
                        self._blackout_off(notify=True)
                elif kind == "tg":
                    self._on_tg(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return  # окно уже закрыто
        self._refresh_running()
        self._refresh_stats()
        self.root.after(100, self._poll)

    def _refresh_running(self):
        running = self.engine.running
        if running == self._was_running:
            return
        was_running, self._was_running = self._was_running, running
        if running:
            self.start_btn.configure(text="■  STOP", fg_color=RED, hover_color=RED_HOVER)
            if self.cfg.get("blackout_enabled") and not self.blackout.active:
                delay = max(float(self.cfg.get("blackout_delay") or 0), 0)
                self._blackout_job = self.root.after(int(delay * 1000), self._blackout_on)
        else:
            self.start_btn.configure(text="▶  START", fg_color=GREEN, hover_color=GREEN_HOVER)
            if was_running:
                # Макрос остановился (вручную или по тревоге) — экран обратно, чтобы было видно, что случилось
                self._blackout_off()
                self.tg.send("⏹ Макрос остановлен\n" + status_text(self.engine, False))

    def _refresh_stats(self):
        s = self._stats
        started = s.get("started")
        runtime = int(time.time() - started) if started and self.engine.running else 0
        h, rem = divmod(runtime, 3600)
        catches = s.get("catches", 0)
        self.stat_values["catches"].configure(text=str(catches))
        self.stat_values["casts"].configure(text=str(s.get("casts", 0)))
        self.stat_values["runtime"].configure(text=f"{h}:{rem // 60:02d}:{rem % 60:02d}")
        rate = catches * 3600 / runtime if runtime >= 60 else 0
        self.stat_values["rate"].configure(text=f"{rate:.0f}")

    def run(self):
        self.root.mainloop()
