"""Окно FishPilot (CustomTkinter): меню разделов слева, «Старт» и статус всегда на виду, карточки настроек."""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk

import customtkinter as ctk
import logging

from . import __version__, alarm, applog, config, fishdb, i18n, screen, updater, winput
from .blackout import Blackout
from .catches import CatchLog
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

log = logging.getLogger("fishpilot")
DIGITS = [str(d) for d in range(10)]
DONATE_URL = "https://www.donationalerts.com/r/etnealy"

# Палитра — macro/theme.py
from .theme import (ACCENT, ACCENT_HOVER, BG, CARD, CARD_BORDER, FIELD, GREEN, GREEN_HOVER,  # noqa: E402
                    HILITE, MUTED, ORANGE, RED, RED_HOVER, SIDEBAR, TEXT)
from . import theme  # noqa: E402
from .i18n import tr

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
    return f"X {p[0]}  ·  Y {p[1]}" if p else tr("Не задано")


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
        applog.setup()
        try:
            moved = config.migrate_old_location()     # config.json и улов рядом с exe → %APPDATA%\\FishPilot
        except OSError:
            log.exception("Не удалось перенести настройки")
            moved = False
        self.cfg = config.load()
        i18n.set_language(self.cfg.get("language"))
        startup_notes = []
        if moved:
            startup_notes.append(tr("Настройки и улов перенесены в {path}", path=config.data_dir()))
        if config.LAST_LOAD_ERROR:
            startup_notes.append(tr("Файл настроек был повреждён ({err}) — копия сохранена как config.json.bad, "
                                    "загружены настройки по умолчанию", err=config.LAST_LOAD_ERROR))
        self.events = queue.Queue()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        for widget, radius in (("CTkButton", 6), ("CTkEntry", 6), ("CTkOptionMenu", 6),
                               ("CTkSegmentedButton", 6), ("CTkTextbox", 6)):
            ctk.ThemeManager.theme[widget]["corner_radius"] = radius
        self.root = ctk.CTk(fg_color=BG)
        self.root.title(tr("FishPilot — автопилот рыбалки для GPO"))
        self.root.geometry("920x720")
        self.root.minsize(840, 600)
        try:
            if sys.platform == "win32":
                self.root.iconbitmap(resource("icon.ico"))
        except tk.TclError:
            pass
        # Узкий шрифт заголовков (Bahnschrift Condensed в Windows 10/11)
        global TK_TITLE
        self.display = theme.display_family(self.root)
        TK_TITLE = (FONT, 12, "bold")
        self.vars = {}
        self._save_job = None
        self._rebinding = None
        self._preview = None
        self._overlay = None
        self._point_labels = {}
        self._guide = None
        self._stats = {}
        self._was_running = None
        self._check_rows = {}
        self._page = None
        self._closing = False
        self._entries = {}

        self.engine = MacroEngine(
            self.cfg,
            log=lambda text: self.events.put(("log", text)),
            on_status=lambda text, color: self.events.put(("status", (text, color))),
            on_stats=lambda stats: self.events.put(("stats", stats)),
            on_catch=self._on_catch,
        )
        self.catch_log = CatchLog(self.cfg, self.log_threadsafe)
        try:
            moves = fishdb.tidy_learned(self.cfg)
        except Exception as exc:
            moves = []
            print(f"tidy_learned: {exc}")
        self.catch_log.apply_moves(moves)
        if moves:
            self.root.after(500, lambda n=len(moves): self.log(
                tr("Каталог рыб: исправлено или убрано записей с ошибками распознавания: {n}", n=n)))

        self.blackout = Blackout(self.root, self.log_threadsafe)
        from .hud import Hud
        self.hud = Hud(self.root, self.log_threadsafe, font=self.display)
        self.root.after(1000, self._tick_hud)
        self._update = None
        self._updating = False
        self.root.after(4000, self._startup_update)
        self._blackout_job = None
        self._status_text = tr("Остановлен")
        self._status_color = "red"
        self.tg = TelegramControl(self.cfg, lambda cmd: self.events.put(("tg", cmd)), self.log_threadsafe,
                                  webapp_link=self._webapp_link)
        self.remote = Remote(self.cfg, self._remote_status, lambda cmd: self.events.put(("tg", cmd)),
                             self.log_threadsafe)

        self._build()
        for note in startup_notes:
            self.root.after(400, lambda n=note: self.log(n))
        # Ошибки в обработчиках окна (кнопки, таймеры) — в журнал и лог-файл, а не в никуда
        self.root.report_callback_exception = self._on_tk_error
        self._apply_topmost()
        self._start_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.exit)
        self.root.after(50, self._poll)
        if not self.cfg.get("guide_shown"):
            self.root.after(700, self.open_guide)  # первый запуск — сразу гайд

    # ---------- переменные, связанные с конфигом ----------
    def var(self, key, kind=float):
        if key in self.vars:
            return self.vars[key]
        value = self.cfg[key]
        v = tk.BooleanVar(value=bool(value)) if kind is bool else tk.StringVar(value=str(value))

        def changed(*_):
            try:
                raw = v.get()
            except tk.TclError:
                return
            if kind is bool:
                value = raw
            elif kind in (int, float):
                value = config.parse_number(key, raw, kind)   # «0,5» тоже годится; NaN и мусор — нет
            elif key == "black_spam_key":
                value = raw.strip().lower() if raw.strip().lower() in winput.VK else None
            else:
                value = raw
            self._mark_field(key, value is not None)
            if value is None:
                return                  # неверный ввод не сохраняем, поле подсвечено красным
            self.cfg[key] = value
            self._schedule_save()

        v.trace_add("write", changed)
        self.vars[key] = v
        return v

    def _mark_field(self, key, ok):
        entry = self._entries.get(key)
        if entry is not None and entry.winfo_exists():
            entry.configure(border_color=CARD_BORDER if ok else RED)

    def _schedule_save(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(500, self._save)

    def _save(self):
        self._save_job = None
        self._refresh_checklist()
        try:
            config.save(self.cfg)
        except OSError as exc:
            self.log(tr("Не удалось сохранить настройки: {exc}", exc=exc))

    # ---------- строительные блоки ----------
    def _card(self, parent, title, subtitle=None):
        card = ctk.CTkFrame(parent, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=8)
        card.pack(fill="x", pady=(0, 10), padx=10)
        tk.Label(card, text=title, font=TK_TITLE, fg=TEXT, bg=CARD).pack(
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
        entry = ctk.CTkEntry(right, width=width, textvariable=self.var(key, kind), fg_color=FIELD,
                             border_color=CARD_BORDER, justify="center")
        entry.pack(side="left")
        self._entries[key] = entry
        # Колонка единиц есть всегда — поля стоят ровно одно под другим
        tk.Label(right, text=unit or "", font=TK_LABEL, fg=MUTED, bg=CARD, width=4).pack(side="left", padx=(6, 0))

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
        self._point_labels[key] = lbl

        def pick():
            self.pick_point(key, hint)

        ctk.CTkButton(body, text=button, width=170, height=32, fg_color=FIELD, hover_color=ACCENT,
                      border_color=CARD_BORDER, border_width=1, anchor="w", command=pick).grid(
            row=row, column=0, sticky="w", pady=4)
        lbl.grid(row=row, column=1, sticky="e", pady=4)

    def pick_point(self, key, hint):
        """Выбор точки на экране (кнопки «… точка» и гайд)."""
        def done(point):
            self.cfg[key] = point
            lbl = self._point_labels.get(key)
            if lbl is not None:
                lbl.configure(text=fmt_point(point), fg=GREEN)
            self._save()
        self._open_overlay(lambda: PointPicker(self.root, hint, done))

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
    PAGES = (("home", "🏠", "Главная"), ("setup", "🎯", "Настройка"), ("catch", "🐟", "Улов"),
             ("phone", "📱", "Телефон"), ("auto", "🛒", "Автоматизация"), ("tune", "🔧", "Тонкая настройка"),
             ("app", "⚙", "Программа"))

    def _build(self):
        """Слева — меню разделов и всегда видимые «Старт» и статус, справа — выбранный раздел."""
        root = self.root
        side = self._side = tk.Frame(root, bg=SIDEBAR, width=200)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="FISHPILOT", font=(self.display, 22, "bold"), fg=HILITE, bg=SIDEBAR).pack(
            anchor="w", padx=18, pady=(18, 0))
        tk.Label(side, text=tr("автопилот рыбалки · GPO"), font=TK_HINT, fg=MUTED, bg=SIDEBAR, wraplength=170,
                 justify="left").pack(anchor="w", padx=18)

        self.status = ctk.CTkLabel(side, text=tr("●  Остановлен"), font=(FONT, 12, "bold"), text_color="white",
                                   fg_color=RED, corner_radius=12, height=26)
        self.status.pack(fill="x", padx=16, pady=(16, 8))
        self.start_btn = ctk.CTkButton(side, text=tr("▶  Старт"), height=46, corner_radius=8,
                                       font=(self.display, 18, "bold"), fg_color=GREEN, hover_color=GREEN_HOVER,
                                       command=self.engine.toggle)
        self.start_btn.pack(fill="x", padx=16)
        self.start_hint = tk.Label(side, text="", font=TK_HINT, fg=MUTED, bg=SIDEBAR)
        self.start_hint.pack(pady=(3, 14))

        self.nav_buttons = {}
        for key, icon, title in self.PAGES:
            btn = ctk.CTkButton(side, text=f"  {icon}   {tr(title)}", anchor="w", height=36, corner_radius=6,
                                fg_color="transparent", hover_color=FIELD, text_color=TEXT,
                                font=(FONT, 13), command=lambda k=key: self.show_page(k))
            btn.pack(fill="x", padx=10, pady=1)
            self.nav_buttons[key] = btn

        bottom = tk.Frame(side, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=16, pady=14)
        # Появляется, когда на GitHub есть версия новее
        self.update_btn = ctk.CTkButton(bottom, text="", height=30, corner_radius=6, fg_color=GREEN,
                                        hover_color=GREEN_HOVER, font=(FONT, 11, "bold"), command=self._do_update)
        self._update_btn_box = bottom
        self._donate_btn = ctk.CTkButton(bottom, text=tr("♥  Поддержать автора"), height=30, corner_radius=6,
                                         fg_color="#7a2948", hover_color="#9c3459", font=(FONT, 11, "bold"),
                                         command=self._donate)
        self._donate_btn.pack(fill="x", pady=(0, 6))
        self._guide_btn = ctk.CTkButton(bottom, text=tr("?  Гайд по настройке"), height=30, corner_radius=6,
                                        fg_color=FIELD, hover_color=ACCENT, font=(FONT, 11), command=self.open_guide)
        self._guide_btn.pack(fill="x")
        tk.Label(bottom, text=tr("версия {__version__}", __version__=__version__), font=TK_HINT, fg=MUTED, bg=SIDEBAR).pack(pady=(6, 0))

        self.content = tk.Frame(root, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)
        self.pages = {}
        builders = {"home": self._page_home, "setup": self._page_setup, "catch": self._page_catch,
                    "phone": self._page_phone, "auto": self._page_auto, "tune": self._page_tune,
                    "app": self._page_app}
        for key, icon, title in self.PAGES:
            frame = tk.Frame(self.content, bg=BG)
            tk.Label(frame, text=tr(title), font=(self.display, 20, "bold"), fg=TEXT, bg=BG).pack(
                anchor="w", padx=22, pady=(16, 8))
            self.pages[key] = frame
            builders[key](frame)
        self._refresh_hotkey_hint()
        self._refresh_checklist()
        self.show_page("home")

    def set_language(self, code):
        """Сменить язык и перестроить окно на месте (без перезапуска): журнал и раздел сохраняются."""
        if code == i18n.current():
            return
        i18n.set_language(code)
        self.cfg["language"] = i18n.current()
        self._save()
        self.rebuild()

    def rebuild(self):
        """Перестроить окно на месте (смена языка, импорт настроек): журнал и раздел сохраняются."""
        i18n.set_language(self.cfg.get("language"))
        if not self.engine.running:
            self._status_text = tr("Остановлен")   # во время ловли статус обновит сам движок
        log_text = self.log_box.get("1.0", "end-1c")
        page = self._page
        self._side.destroy()
        self.content.destroy()
        self._check_rows = {}
        self._point_labels = {}
        self._entries = {}
        self._build()
        self.log_box.configure(state="normal")
        self.log_box.insert("1.0", log_text)
        self.log_box.configure(state="disabled")
        self.log_box.see("end")
        self.status.configure(text=f"●  {self._status_text}",
                              fg_color=STATUS_COLORS.get(self._status_color, self._status_color))
        self._was_running = None
        self._refresh_running()
        self._refresh_stats()
        if self._update is not None:
            self._on_update_found(self._update, silent=True)
        self.show_page(page or "home")
        self.remote.poke()

    def _donate(self):
        """Страница пожертвований автору — в обычном браузере."""
        import webbrowser
        webbrowser.open(DONATE_URL)
        self.log(tr("Спасибо за поддержку! ♥ Страница пожертвований открыта в браузере"))

    def show_page(self, key):
        for k, frame in self.pages.items():
            if k == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color=ACCENT if k == key else "transparent",
                          font=(FONT, 13, "bold") if k == key else (FONT, 13))
        self._page = key

    def _page_scroll(self, frame):
        page = smooth_scrolling(ctk.CTkScrollableFrame(frame, fg_color="transparent",
                                                       scrollbar_button_color=CARD_BORDER))
        page.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        return page

    # --- Главная: счётчики, готовность к ловле, журнал событий ---
    def _page_home(self, frame):
        stats = tk.Frame(frame, bg=BG)
        stats.pack(fill="x", padx=18)
        self.stat_values = {}
        for i, (key, title) in enumerate((("catches", tr("Поймано")), ("casts", tr("Забросов")), ("runtime", tr("Время")),
                                          ("rate", tr("Рыб в час")))):
            stats.columnconfigure(i, weight=1, uniform="stats")
            card = ctk.CTkFrame(stats, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=8)
            card.grid(row=0, column=i, sticky="ew", padx=4)
            value = ctk.CTkLabel(card, text="0", font=(self.display, 26, "bold"), text_color=TEXT)
            value.pack(pady=(8, 0))
            ctk.CTkLabel(card, text=title, font=(FONT, 11), text_color=MUTED).pack(pady=(0, 8))
            self.stat_values[key] = value

        ready = ctk.CTkFrame(frame, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=8)
        ready.pack(fill="x", padx=22, pady=(12, 0))
        tk.Label(ready, text=tr("Готовность к ловле"), font=(FONT, 12, "bold"), fg=TEXT, bg=CARD).pack(
            anchor="w", padx=14, pady=(10, 2))
        self.checklist = tk.Frame(ready, bg=CARD)
        self.checklist.pack(fill="x", padx=14, pady=(0, 10))
        self.checklist.columnconfigure(1, weight=1)
        self._check_rows = {}
        items = (
            ("area", tr("Область мини-игры"), tr("Выбрать"), self.change_area),
            ("water", tr("Точка заброса"), tr("Указать"),
             lambda: self.pick_point("water_point", tr("Кликните по воде, куда забрасывать удочку"))),
            ("rod", tr("Слот удочки"), tr("Изменить"), lambda: self.show_page("setup")),
            ("catch", tr("Распознавание улова"), tr("Проверить"), self._test_catch),
            ("tg", "Telegram", tr("Настроить"), lambda: self.show_page("phone")),
            ("alarm", tr("Будильник на телефон"), tr("Тест"), self._test_alarm),
        )
        for row, (key, title, action, command) in enumerate(items):
            mark = tk.Label(self.checklist, text="●", font=(FONT, 13, "bold"), fg=MUTED, bg=CARD, width=2)
            mark.grid(row=row, column=0, sticky="w")
            box = tk.Frame(self.checklist, bg=CARD)
            box.grid(row=row, column=1, sticky="w", pady=3)
            tk.Label(box, text=title, font=TK_LABEL, fg=TEXT, bg=CARD).pack(anchor="w")
            detail = tk.Label(box, text="", font=TK_HINT, fg=MUTED, bg=CARD)
            detail.pack(anchor="w")
            ctk.CTkButton(self.checklist, text=action, width=96, height=28, corner_radius=6, fg_color=FIELD,
                          hover_color=ACCENT, border_color=CARD_BORDER, border_width=1,
                          command=command).grid(row=row, column=2, sticky="e")
            self._check_rows[key] = (mark, detail)

        logbox = ctk.CTkFrame(frame, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=8)
        logbox.pack(fill="both", expand=True, padx=22, pady=12)
        tk.Label(logbox, text=tr("Журнал событий"), font=(FONT, 12, "bold"), fg=TEXT, bg=CARD).pack(
            anchor="w", padx=14, pady=(10, 4))
        self.log_box = ctk.CTkTextbox(logbox, fg_color=FIELD, font=("Consolas", 11), wrap="word", state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _refresh_checklist(self):
        """Галочки на «Главной»: что уже настроено, а что нет (зелёное — готово, оранжевое — надо сделать)."""
        if not getattr(self, "_check_rows", None):
            return
        cfg = self.cfg
        token = str(cfg.get("telegram_token") or "").strip()
        chat = str(cfg.get("telegram_chat_id") or "").strip()
        channels = alarm.channels(cfg)
        area = cfg.get("catch_area")
        states = {
            "area": (bool(cfg.get("area")), self._area_text() if cfg.get("area")
                     else tr("Не выбрана — во время мини-игры нажмите {v0}", v0=cfg['hotkey_change_area'].upper())),
            "water": (bool(cfg.get("water_point")), fmt_point(cfg.get("water_point")) if cfg.get("water_point")
                      else tr("Не указана — кликните по воде")),
            "rod": (True, tr("Удочка в слоте {v0}, другой слот {v1}", v0=cfg['rod_hotkey'], v1=cfg['other_hotkey'])),
            "catch": (bool(cfg.get("catch_log_enabled")),
                      (tr("Своя область уведомления") if area else tr("Авто: центр окна Roblox"))
                      if cfg.get("catch_log_enabled") else tr("Выключено — включите в разделе «Улов»")),
            "tg": (bool(token and chat), tr("Бот подключён") if token and chat else tr("Не настроен — нужен свой бот")),
            "alarm": (bool(channels and cfg.get("alarm_enabled")),
                      ", ".join(channels) if channels else tr("Ни одного канала — настройте в разделе «Телефон»")),
        }
        for key, (ok, text) in states.items():
            mark, detail = self._check_rows[key]
            mark.configure(text="✓" if ok else "●", fg=GREEN if ok else ORANGE)
            detail.configure(text=text)

    # --- Настройка: главное по шагам ---
    def _page_setup(self, frame):
        page = self._page_scroll(frame)
        c = self._card(page, tr("1. Область мини-игры"),
                       tr("Поймайте рыбу вручную и во время мини-игры выделите рамкой обе полосы"))
        self.area_label = tk.Label(c, text=self._area_text(), font=TK_LABEL, bg=CARD,
                                   fg=GREEN if self.cfg["area"] else MUTED)
        self.area_label.grid(row=self._row(c), column=0, columnspan=2, sticky="w", pady=(0, 2))
        self._buttons(c, (tr("Выбрать область"), self.change_area, ACCENT, ACCENT_HOVER),
                      (tr("Что видит макрос"), self._open_preview, FIELD, CARD_BORDER))

        c = self._card(page, tr("2. Заброс"), tr("Куда и как долго забрасывать удочку"))
        self._point(c, tr("Указать точку воды"), "water_point", tr("Кликните по воде, куда забрасывать удочку"))
        self._num(c, tr("Удержание при забросе"), "cast_hold", tr("Сколько держать ЛКМ при забросе"), unit=tr("с"))
        self._num(c, tr("Перезаброс через"), "recast_timeout", tr("Если за это время не клюнуло"), unit=tr("с"))

        c = self._card(page, tr("3. Слоты предметов"), tr("Номера слотов на панели в игре"))
        self._option(c, tr("Слот удочки"), "rod_hotkey", DIGITS, tr("Где лежит удочка"))
        self._option(c, tr("Любой другой слот"), "other_hotkey", DIGITS,
                     tr("Нажимается перед удочкой, чтобы удочка точно оказалась в руках"))

        c = self._card(page, tr("4. Уведомление о пойманной рыбе"),
                       tr("Макрос читает надпись «New Item <рыба>» и ведёт список улова"))
        row = self._row(c)
        self._label(c, row, tr("Область уведомления"), tr("Где игра пишет, какая рыба поймана"))
        self.catch_area_label = tk.Label(c, text=self._catch_area_text(), font=TK_LABEL, bg=CARD,
                                         fg=GREEN if self.cfg["catch_area"] else HILITE)
        self.catch_area_label.grid(row=row, column=1, sticky="e", pady=4)
        self._buttons(c, (tr("Выбрать область"), self._pick_catch_area, ACCENT, ACCENT_HOVER),
                      (tr("Проверить сейчас"), self._test_catch, FIELD, CARD_BORDER))

    # --- Улов ---
    def _page_catch(self, frame):
        page = self._page_scroll(frame)
        c = self._card(page, tr("Список улова"), tr("Что и сколько поймано — кнопка «🐟 Улов» в Telegram"))
        self._switch(c, tr("Вести список улова"), "catch_log_enabled",
                     tr("После каждой мини-игры читает название рыбы из уведомления игры"))
        self._switch(c, tr("Сообщать о каждой рыбе"), "catch_notify_each", tr("Присылать в Telegram каждую поимку"))
        self._buttons(c, (tr("Отправить в Telegram"), self._send_catches, ACCENT, ACCENT_HOVER),
                      (tr("Очистить список"), self._clear_catches, FIELD, CARD_BORDER))
        self._fish_catalog_card(page)

    # --- Телефон: Telegram, мини-приложение, будильник ---
    def _page_phone(self, frame):
        page = self._page_scroll(frame)
        c = self._card(page, tr("Telegram-бот"), tr("Сообщения, скриншоты, кнопки управления и список улова"))
        self._text(c, tr("Токен бота"), "telegram_token", tr("От @BotFather, у каждого свой бот"), secret=True)
        self._text(c, tr("Ваш chat id"), "telegram_chat_id", tr("Напишите боту /start и нажмите «Узнать chat id»"))
        self._buttons(c, (tr("Узнать chat id"), self._find_chat_id, FIELD, CARD_BORDER),
                      (tr("Проверить Telegram"), self._check_tg, ACCENT, ACCENT_HOVER))
        self._switch(c, tr("Кнопки управления в боте"), "tg_control",
                     tr("Экран, скриншот, статус, улов, стоп. Меню — команда /menu"))
        self._switch(c, tr("Скриншот к тревоге"), "alarm_screenshot",
                     hint=tr("Только окно Roblox, уходит на серверы Telegram вместе с тревогой"))

        c = self._card(page, tr("Мини-приложение"), tr("Живая панель в Telegram: статистика, старт/стоп, экран, улов"))
        self._switch(c, tr("Включить мини-приложение"), "remote_enabled",
                     tr("Кнопка «Панель» в чате с ботом. Связь через публичные серверы, всё подписано секретом"))
        self._buttons(c, (tr("Отправить панель"), lambda: self._on_tg("panel"), ACCENT, ACCENT_HOVER),
                      (tr("Новая ссылка"), self._new_secret, FIELD, CARD_BORDER))

        c = self._card(page, tr("Будильник"), tr("Звонит на телефон, если что-то пошло не так"))
        self._switch(c, tr("Звонить при проблемах"), "alarm_enabled")
        self._num(c, tr("Повторять тревогу"), "alarm_repeat_sec", tr("Пока не нажмёте «Стоп тревоги» (0 — один раз)"),
                  unit=tr("с"))
        self._buttons(c, (tr("Тест тревоги"), self._test_alarm, ACCENT, ACCENT_HOVER),
                      (tr("Стоп тревоги"), self.engine.alarms.acknowledge, FIELD, CARD_BORDER))

        c = self._card(page, tr("Когда звонить"))
        self._num(c, tr("Забросов без поклёвки подряд"), "wd_failed_casts",
                  tr("Кончилась наживка, дисконнект или сдвинули персонажа (0 — выкл)"), kind=int)
        self._num(c, tr("Нет рыбы дольше"), "wd_no_catch_min", tr("Ни одной рыбы за это время (0 — выкл)"), unit=tr("мин"))
        self._switch(c, tr("Следить за окном Roblox"), "wd_check_window", tr("Игра закрылась или окно свёрнуто"))
        self._num(c, tr("Окно Roblox потеряно дольше"), "wd_window_grace_sec", unit=tr("с"))
        self._switch(c, tr("Останавливать ловлю при тревоге"), "wd_stop_on_alarm")

        c = self._card(page, tr("Настоящий звонок"), tr("Telegram пишет сообщение, а эти приложения умеют звонить"))
        self._text(c, tr("ntfy: тема"), "ntfy_topic", tr("Та же тема, что в приложении ntfy"))
        self._text(c, tr("ntfy: сервер"), "ntfy_server")
        self._text(c, "Pushover: user key", "pushover_user", secret=True)
        self._text(c, "Pushover: app token", "pushover_token", secret=True)

    # --- Автоматизация: наживка и фрукты ---
    def _page_auto(self, frame):
        page = self._page_scroll(frame)

        def section(flag_key, title, hint, build):
            card = self._card(page, title, hint)
            settings = tk.Frame(card, bg=CARD)
            settings.columnconfigure(0, weight=1)
            build(settings)

            def toggle(*_):
                if not settings.winfo_exists():
                    return          # окно перестроили (смена языка) — это старая карточка
                if self.vars[flag_key].get():
                    settings.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
                else:
                    settings.grid_forget()
            self._switch(card, tr("Включено"), flag_key)
            self.vars[flag_key].trace_add("write", toggle)
            toggle()

        def buy(f):
            tk.Label(f, text=tr("Порядок: E → левая → средняя → количество → левая → правая → средняя"),
                     font=TK_HINT, fg=MUTED, bg=CARD).grid(row=self._row(f), column=0, columnspan=2,
                                                                 sticky="w", pady=(0, 4))
            self._point(f, tr("Левая точка"), "buy_left", tr("Кликните по левой кнопке покупки"))
            self._point(f, tr("Средняя точка"), "buy_middle", tr("Кликните по средней кнопке покупки"))
            self._point(f, tr("Правая точка"), "buy_right", tr("Кликните по правой кнопке покупки"))
            self._num(f, tr("Покупать каждые"), "loops_per_purchase", tr("Сколько циклов между покупками"),
                      kind=int, unit=tr("цикл"))
            self._switch(f, tr("Вводить количество"), "buy_type_amount",
                         tr("Вписывает число циклов после первого клика по средней точке"))
            self._switch(f, tr("Не покупать при старте"), "buy_skip_first",
                         tr("Первая покупка — только через заданное число циклов"))

        def fruit(f):
            self._option(f, tr("Слот фрукта"), "fruit_hotkey", DIGITS)
            self._point(f, tr("Точка Store"), "store_point", tr("Кликните по кнопке Store"))

        def bait(f):
            self._point(f, tr("Точка наживки"), "bait_point", tr("Кликните по верхней наживке в списке"))

        section("auto_buy_bait", tr("Покупка наживки"), tr("Встаньте рядом с продавцом наживки"), buy)
        section("auto_store_fruit", tr("Фрукты в хранилище"), tr("Убирает выловленные дьявольские фрукты"), fruit)
        section("auto_select_bait", tr("Верхняя наживка"), tr("Выбирает верхнюю наживку перед каждым забросом"), bait)

    # --- Тонкая настройка: всё, что обычно трогать не нужно ---
    def _page_tune(self, frame):
        note = tk.Label(frame, text=tr("Значения по умолчанию подходят большинству. Меняйте, если что-то работает не так."),
                        font=TK_HINT, fg=ORANGE, bg=BG)
        note.pack(anchor="w", padx=22, pady=(0, 6))
        groups = [tr("Мини-игра"), tr("Распознавание"), tr("Задержки"), tr("Античит")]
        pages = {}
        holder = tk.Frame(frame, bg=BG)

        def show(name):
            for n, p in pages.items():
                if n == name:
                    p.pack(fill="both", expand=True)
                else:
                    p.pack_forget()

        nav = ctk.CTkSegmentedButton(frame, values=groups, command=show, fg_color=CARD,
                                     selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                     unselected_color=CARD, unselected_hover_color=FIELD)
        nav.pack(fill="x", padx=22, pady=(0, 8))
        holder.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        for name in groups:
            pages[name] = smooth_scrolling(ctk.CTkScrollableFrame(holder, fg_color="transparent",
                                                                  scrollbar_button_color=CARD_BORDER))

        page = pages[tr("Мини-игра")]
        f = self._card(page, tr("Как полоса гонится за рыбой"), tr("PD-регулятор держит вашу полосу на белой черте рыбы"))
        self._num(f, tr("Сила притяжения (Kp)"), "kp", tr("Насколько сильно тянуть полосу к рыбе"))
        self._num(f, tr("Гашение (Kd)"), "kd", tr("Увеличьте, если полоса проскакивает рыбу"))
        self._num(f, tr("Ограничение"), "pd_clamp", tr("Предел выхода регулятора"))
        self._num(f, tr("Тормоз при сближении"), "approach_damping", tr("Когда полоса едет к рыбе (больше — сильнее)"))
        self._num(f, tr("Гашение при погоне"), "chase_damping", tr("Когда рыба уходит от полосы"))
        self._num(f, tr("Повтор нажатия ЛКМ"), "resend_interval",
                  tr("Повторять нажатие, если оно не менялось — против «потерянной» кнопки"), unit=tr("с"))
        self._num(f, tr("Пауза после рыбы"), "fish_end_delay", tr("Ожидание после конца мини-игры"), unit=tr("с"))

        page = pages[tr("Распознавание")]
        dt = self._card(page, tr("Распознавание мини-игры"), tr("Если мини-игра не находится — смотрите «Что видит макрос»"))
        row = self._row(dt)
        self._label(dt, row, tr("Цвет трека"), tr("Нажмите «Взять» и кликните по синему треку"))
        right = tk.Frame(dt, bg=CARD)
        right.grid(row=row, column=1, sticky="e", pady=4)
        self.color_swatch = ctk.CTkLabel(right, text="", width=70, height=28, corner_radius=6)
        self.color_swatch.pack(side="left", padx=(0, 6))
        ctk.CTkButton(right, text=tr("Взять"), width=60, height=28, fg_color=FIELD, hover_color=ACCENT,
                      command=self._pick_color).pack(side="left")
        self._update_swatch()
        self._num(dt, tr("Допуск цвета"), "zone_tolerance", tr("Насколько пиксель может отличаться от цвета трека"), kind=int)
        self._option(dt, tr("Полоса игрока"), "bar_mode", ["dark", "blue"],
                     tr("dark — тёмно-серая на синем треке (обычно), blue — наоборот"))
        self._num(dt, tr("Яркость тёмной полосы"), "dark_max", tr("Самый яркий пиксель, который ещё считается полосой"),
                  kind=int)
        self._num(dt, tr("Склейка полосы"), "gap_mult", tr("Белая черта рвёт полосу: допуск = высота черты × это число"))
        self._num(dt, tr("Яркость черты рыбы"), "fish_white_min", tr("Минимальная яркость белой черты (0–255)"), kind=int)
        self._switch(dt, tr("Требовать зелёную полосу"), "require_progress",
                     tr("Мини-игра засчитывается, только если видна полоса прогресса (против ложных кликов)"))
        self._num(dt, tr("Мин. пикселей трека"), "min_zone_pixels", tr("Меньше — мини-игры на экране нет"), kind=int)
        self._num(dt, tr("Проверок в секунду"), "scan_fps", tr("Во время мини-игры"), kind=int)
        self._buttons(dt, (tr("Что видит макрос"), self._open_preview, ACCENT, ACCENT_HOVER))
        f = self._card(page, tr("Уведомление о рыбе"))
        self._num(f, tr("Снимать через"), "catch_capture_delay", tr("После конца мини-игры"), unit=tr("с"))

        page = pages[tr("Задержки")]
        f = self._card(page, tr("Окно Roblox"))
        self._num(f, tr("После переключения"), "focus_delay", unit=tr("с"))
        self._num(f, tr("Дополнительно"), "post_focus_delay", tr("Пауза для стабильности"), unit=tr("с"))
        f = self._card(page, tr("Рыбалка"))
        self._num(f, tr("Между слотами при выборе удочки"), "rod_select_delay", unit=tr("с"))
        self._num(f, tr("После движения курсора"), "cursor_delay", unit=tr("с"))
        self._num(f, tr("Проверка поклёвки каждые"), "scan_loop_delay", tr("Меньше — быстрее, но больше нагрузка"), unit=tr("с"))
        self._num(f, tr("Микропауза мыши"), "micro_delay", tr("Против детекта Roblox (0.05 = 50 мс)"), unit=tr("с"))
        f = self._card(page, tr("Покупка наживки"))
        self._num(f, tr("После E"), "buy_e_delay", tr("Ожидание открытия магазина"), unit=tr("с"))
        self._num(f, tr("Между кликами"), "buy_click_delay", unit=tr("с"))
        self._num(f, tr("После ввода числа"), "buy_type_delay", unit=tr("с"))
        f = self._card(page, tr("Фрукты и наживка"))
        self._num(f, tr("После слота фрукта"), "fruit_hotkey_delay", unit=tr("с"))
        self._num(f, tr("После клика Store"), "fruit_store_delay", unit=tr("с"))
        self._num(f, tr("После Shift"), "fruit_shift_delay", unit=tr("с"))
        self._num(f, tr("После Backspace"), "fruit_backspace_delay", unit=tr("с"))
        self._num(f, tr("После клика по наживке"), "bait_click_delay", unit=tr("с"))

        page = pages[tr("Античит")]
        f = self._card(page, tr("Чёрный экран античита"), tr("Если игра затемнила экран, макрос жмёт клавишу, пока он не уйдёт"))
        self._num(f, tr("Порог"), "black_threshold",
                  tr("Доля окна Roblox, которая должна стать чёрной (0.85 = 85%, 0 — выключить)"))
        self._num(f, tr("Клавиша"), "black_spam_key", "space, e, enter, 1…", kind=str)
        self._num(f, tr("Жать каждые"), "black_spam_delay", unit=tr("с"))
        self._num(f, tr("Тревога через"), "black_max_sec", tr("Если чёрный экран не ушёл"), unit=tr("с"))
        nav.set(tr("Мини-игра"))
        show(tr("Мини-игра"))

    # --- Программа: клавиши, окно, экран, обновления ---
    def _page_app(self, frame):
        page = self._page_scroll(frame)
        hk = self._card(page, tr("Горячие клавиши"), tr("Работают везде, даже когда активно окно игры"))
        self.hotkey_labels = {}
        for label, key in ((tr("Старт / стоп"), "hotkey_start_stop"), (tr("Выбрать область мини-игры"), "hotkey_change_area"),
                           (tr("Погасить / включить экран"), "hotkey_blackout"), (tr("HUD вкл / выкл"), "hotkey_hud"),
                           (tr("Закрыть программу"), "hotkey_exit")):
            row = self._row(hk)
            self._label(hk, row, label)
            right = tk.Frame(hk, bg=CARD)
            right.grid(row=row, column=1, sticky="e", pady=4)
            cap = ctk.CTkLabel(right, text=self.cfg[key].upper(), width=56, height=30, corner_radius=6,
                               fg_color=FIELD, text_color=ACCENT, font=(FONT, 13, "bold"))
            cap.pack(side="left", padx=(0, 8))
            ctk.CTkButton(right, text=tr("Сменить"), width=80, height=30, fg_color="transparent", border_width=1,
                          border_color=CARD_BORDER, hover_color=FIELD,
                          command=lambda k=key: self._rebind(k)).pack(side="left")
            self.hotkey_labels[key] = cap

        c = self._card(page, tr("Язык"), "Language · Idioma")
        names = {name: code for code, (name, _) in i18n.LANGUAGES.items()}
        row = self._row(c)
        self._label(c, row, tr("Язык интерфейса"), tr("Окно, гайд, HUD и сообщения бота"))
        lang = ctk.CTkOptionMenu(c, values=list(names), width=170, fg_color=FIELD, button_color=CARD_BORDER,
                                 button_hover_color=ACCENT, command=lambda name: self.set_language(names[name]))
        lang.set(i18n.LANGUAGES[i18n.current()][0])
        lang.grid(row=row, column=1, sticky="e", pady=4)

        c = self._card(page, tr("Экран"))
        self._switch(c, tr("Окно программы поверх всех"), "always_on_top", command=self._apply_topmost)
        self._switch(c, tr("HUD поверх игры"), "hud_enabled",
                     tr("Рамка области и показатели над Roblox, пока идёт ловля"),
                     command=lambda: self._tick_hud(once=True))
        self._switch(c, tr("Гасить экран после старта"), "blackout_enabled",
                     tr("Вы видите чёрный экран, а макрос — игру. Вернуть: любая клавиша или мышь"))
        self._switch(c, tr("Гасить все мониторы"), "blackout_all_monitors", tr("Выкл — только монитор с Roblox"))
        self._num(c, tr("Гасить через"), "blackout_delay", tr("После «Старт»"), unit=tr("с"))

        c = self._card(page, tr("Помощь"), tr("Если что-то не работает — пришлите диагностику автору"))
        self._buttons(c, (tr("Скопировать диагностику"), self._copy_diagnostics, ACCENT, ACCENT_HOVER),
                      (tr("Папка с логами"), self._open_logs, FIELD, CARD_BORDER))
        self._buttons(c, (tr("Экспорт настроек"), self._export_settings, FIELD, CARD_BORDER),
                      (tr("Импорт настроек"), self._import_settings, FIELD, CARD_BORDER))
        tk.Label(c, text=tr("В экспорт не попадают токены и секреты — файлом можно делиться"), font=TK_HINT,
                 fg=MUTED, bg=CARD).grid(row=self._row(c), column=0, columnspan=2, sticky="w")

        c = self._card(page, tr("Обновления"), tr("Сейчас версия {__version__}", __version__=__version__))
        self._switch(c, tr("Проверять при запуске"), "auto_update",
                     tr("Новая версия скачивается прямо в программу, настройки и улов остаются"))
        self._buttons(c, (tr("Проверить сейчас"), lambda: self._check_update(silent=False), ACCENT, ACCENT_HOVER),
                      (tr("Обновить"), self._do_update, FIELD, CARD_BORDER))

    # ---------- действия ----------
    def _apply_topmost(self):
        self.root.attributes("-topmost", bool(self.cfg["always_on_top"]))

    def _area_text(self):
        a = self.cfg["area"]
        return tr("Область: X {v0}, Y {v1}, {v2}×{v3}", v0=a[0], v1=a[1], v2=a[2], v3=a[3]) if a else tr("Область не задана (F2 во время мини-игры)")

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
            self.log(tr("Сначала остановите макрос"))
            return

        def done(area):
            self.cfg["area"] = area
            self.area_label.configure(text=self._area_text(), fg=GREEN)
            self._save()
            self.log(tr("Область мини-игры: {area}", area=area))
        self._open_overlay(lambda: AreaSelector(self.root, self.cfg["area"], done))

    def _pick_color(self):
        def done(point):
            def grab():
                rgb = screen.grab((point[0], point[1], 1, 1))[0, 0]
                self.cfg["zone_color"] = [int(c) for c in rgb]
                self._update_swatch()
                self._save()
                self.log(tr("Цвет трека: {v0}", v0=self.cfg['zone_color']))
            self.root.after(150, grab)  # ждём, пока оверлей исчезнет
        self._open_overlay(lambda: PointPicker(self.root, tr("Кликните по синему треку мини-игры"), done))

    def _update_swatch(self):
        r, g, b = self.cfg["zone_color"]
        self.color_swatch.configure(fg_color=f"#{r:02x}{g:02x}{b:02x}", text=f"{r},{g},{b}",
                                    text_color="black" if r + g + b > 380 else "white", font=(FONT, 10))

    def _test_alarm(self):
        names = alarm.channels(self.cfg)
        if not names:
            self.log(tr("Заполните хотя бы один канал: ntfy topic, Telegram или Pushover"))
            return
        self.log(tr("Отправляю тестовую тревогу: ") + ", ".join(names))

        def job():
            try:
                png = screen.game_screenshot() if self.cfg.get("alarm_screenshot") else None
            except Exception:
                png = None
            alarm.send_alarm(self.cfg, tr("Тестовая тревога — всё работает"), png,
                             log=lambda t: self.events.put(("log", t)))
        threading.Thread(target=job, daemon=True).start()

    def _check_tg(self):
        from .tgcontrol import diagnose
        self.log(tr("Проверяю Telegram…"))

        def job():
            ok, lines = diagnose(self.cfg)
            for line in lines:
                self.log_threadsafe("Telegram: " + line)
            self.log_threadsafe(tr("Telegram работает ✓") if ok else tr("Telegram не настроен — см. строки выше"))
        threading.Thread(target=job, daemon=True).start()

    def _find_chat_id(self):
        from .tgcontrol import clean_token, explain, find_chats
        token = clean_token(self.cfg.get("telegram_token"))
        if not token:
            self.log(tr("Сначала вставьте токен бота"))
            return

        def job():
            chats = dict(self.tg.seen_chats)
            if not chats and not self.tg.polling:
                try:
                    chats = dict(find_chats(token))
                except Exception as exc:
                    self.log_threadsafe(f"Telegram: {explain(exc)}")
                    return
            if not chats:
                self.log_threadsafe(tr("Никто ещё не писал боту: откройте его в Telegram, нажмите «Start» (или напишите любое сообщение) и нажмите «Узнать chat id» ещё раз"))
                return
            if len(chats) > 1:
                self.log_threadsafe(tr("Боту писали несколько человек: ") +
                                    ", ".join(f"{n} — {cid}" for cid, n in chats.items()) +
                                    tr(". Впишите свой chat id вручную"))
                return
            (cid, name), = chats.items()
            self.call_soon(lambda: self._set_chat_id(cid, name))
        threading.Thread(target=job, daemon=True).start()

    def _set_chat_id(self, cid, name):
        self.var("telegram_chat_id", str).set(cid)
        self.log(tr("Chat id найден: {cid} ({name}). Теперь нажмите «Проверить Telegram»", cid=cid, name=name))

    def open_guide(self):
        if self._guide is not None and self._guide.winfo_exists():
            self._guide.lift()
            return
        from .guide import Guide
        self._guide = Guide(self)

    def _open_preview(self):
        if self._preview and self._preview.winfo_exists():
            self._preview.lift()
            return
        from .preview import Preview
        self._preview = Preview(self.root, self.cfg, self.engine)

    def _rebind(self, key):
        if keyboard is None:
            self.log(tr("pynput не установлен — горячие клавиши недоступны"))
            return
        self._rebinding = key
        self.hotkey_labels[key].configure(text="...", text_color=ORANGE)

    def _refresh_hotkey_hint(self):
        self.start_hint.configure(text=tr("или клавиша {v0}", v0=self.cfg['hotkey_start_stop'].upper()))
        self._refresh_checklist()

    def log_threadsafe(self, text):
        self.events.put(("log", text))

    # ---------- чёрный экран ----------
    def _hud_data(self):
        s = self.engine.stats
        started = s.get("started")
        last = max(self.catch_log.items, key=lambda c: c.last, default=None)
        return {
            "area": self.cfg.get("area"), "area_key": self.cfg.get("hotkey_change_area"),
            "status": self._status_text, "color": self._status_color,
            "runtime": time.time() - started if started and self.engine.running else 0,
            "casts": s.get("casts", 0), "catches": s.get("catches", 0),
            "last_fish": self.catch_log.name_of(last) if last else None,
            "live": self.engine.live if self.engine.running else None,
        }

    def _tick_hud(self, once=False):
        """HUD виден, пока идёт ловля и активно окно Roblox: поверх других окон он не нужен.
        Прячется и на время выбора области/точки (там своя рамка), и при чёрном экране."""
        try:
            in_roblox = not winput.IS_WINDOWS or winput.foreground_title() == "Roblox"
            want = (bool(self.cfg.get("hud_enabled")) and self.engine.running and in_roblox
                    and not self._overlay_open() and not self.blackout.active)
            if want and not self.hud.visible:
                self.hud.show()
            elif not want and self.hud.visible:
                self.hud.hide()
            if self.hud.visible:
                self.hud.update(self._hud_data())
        except Exception as exc:
            self.log(tr("HUD: ошибка {exc!r} — выключаю", exc=exc))
            self.cfg["hud_enabled"] = False
            self.hud.hide()
        if not once:
            self.root.after(150, self._tick_hud)

    def _blackout_on(self):
        self._blackout_job = None
        if self.blackout.show(bool(self.cfg["blackout_all_monitors"])):
            self.tg.send(tr("🌑 Экран затемнён, макрос ловит рыбу"))

    def _blackout_off(self, notify=False):
        if self._blackout_job:
            self.root.after_cancel(self._blackout_job)
            self._blackout_job = None
        if self.blackout.active:
            self.blackout.hide()
            if notify:
                self.tg.send(tr("🔆 Экран включён"), buttons=False)

    # ---------- журнал улова ----------
    def _fish_catalog_card(self, page):
        """Каталог рыб: иконка, редкость и название. Новые рыбы появляются здесь сами."""
        self._catalog_body = self._card(page, tr("Каталог рыб"),
                                        tr("Названия — из уведомления игры. Можно поменять своё. Новые рыбы добавляются сами"))
        self._fish_images = []
        self._rainbow_labels = []
        self._fill_catalog()
        self._rainbow_phase = 0.0
        self.root.after(120, self._animate_rainbow)

    def _fill_catalog(self):
        from PIL import Image
        from . import fishdb
        card = self._catalog_body
        for child in card.winfo_children():
            child.destroy()
        self._fish_images.clear()
        self._rainbow_labels.clear()
        names = self.cfg.setdefault("fish_names", {})
        rarity_titles = {tr(fishdb.NEW_RARITY["title"]): None}
        rarity_titles.update({tr(v["title"]): k for k, v in fishdb.RARITIES.items()})
        fish_list = sorted(fishdb.all_fish(), key=lambda f: (fishdb.RARITY_ORDER.get(f["rarity"], 4),
                                                              -(f.get("price") or 0), f["id"]))
        for fish in fish_list:
            row = self._row(card)
            left = tk.Frame(card, bg=CARD)
            left.grid(row=row, column=0, sticky="w", pady=3)
            bg = Image.new("RGBA", (40, 40), (79, 79, 79, 255))
            try:
                ic = fishdb.icon(fish["id"]).copy()
                ic.thumbnail((36, 36))
                bg.alpha_composite(ic, ((40 - ic.width) // 2, (40 - ic.height) // 2))
            except OSError:
                pass
            img = ctk.CTkImage(bg, size=(40, 40))
            self._fish_images.append(img)
            ctk.CTkLabel(left, text="", image=img, width=40).pack(side="left", padx=(0, 10))
            info = fishdb.rarity_info(fish["rarity"])
            if fish["id"].startswith("u"):
                # Выученная рыба: редкость — по цвету текста в игре, можно поправить
                var_r = tk.StringVar(value=info["title"])

                def pick(title, fid=fish["id"]):
                    fishdb.set_rarity(fid, rarity_titles[title])
                    self.root.after(10, self._fill_catalog)
                ctk.CTkOptionMenu(left, values=list(rarity_titles), variable=var_r, command=pick, width=118,
                                  fg_color=FIELD, button_color=CARD_BORDER, button_hover_color=ACCENT,
                                  font=(self.display, 12, "bold")).pack(side="left")
                # Это рыба со скрина? Тогда название переходит к её настоящей иконке
                choices = {f"{fishdb.display_name(b, self.cfg)} ({fishdb.rarity_info(b['rarity'])['title'].lower()})":
                           b["id"] for b in fishdb.BUILTIN}

                def link(label, fid=fish["id"], choices=choices):
                    bid = choices.get(label)
                    if not bid:
                        return
                    fishdb.link_to_builtin(fid, bid)
                    self.catch_log.remap(fid, bid)
                    self.log(tr("Иконка привязана: {v0}", v0=fishdb.display_name(fishdb.by_id(bid), self.cfg)))
                    self.remote.poke()
                    self.root.after(10, self._fill_catalog)
                ctk.CTkOptionMenu(left, values=list(choices), variable=tk.StringVar(value=tr("Иконка со скрина…")),
                                  command=link, width=150, fg_color=FIELD, button_color=CARD_BORDER,
                                  button_hover_color=ACCENT, dynamic_resizing=False,
                                  font=(self.display, 12, "bold")).pack(side="left", padx=(6, 0))

                def remove(fid=fish["id"], title=fishdb.display_name(fish, self.cfg)):
                    fishdb.remove_fish(fid)
                    names.pop(fid, None)
                    self.catch_log.forget(fid)
                    self._schedule_save()
                    self.log(tr("Удалено из каталога: {title}", title=title))
                    self.remote.poke()
                    self.root.after(10, self._fill_catalog)
                ctk.CTkButton(left, text="✕", width=30, command=remove, fg_color=FIELD, hover_color=RED,
                              font=(self.display, 12, "bold")).pack(side="left", padx=(6, 0))
            else:
                color = info["color"]
                lbl = tk.Label(left, text=info["title"], font=(FONT, 10, "bold"),
                               bg=CARD, fg=HILITE if color == "rainbow" else color, width=12, anchor="w")
                lbl.pack(side="left")
                if color == "rainbow":
                    self._rainbow_labels.append(lbl)
            if fish.get("price"):
                tk.Label(left, text=f"{fish['price']} Peli", font=TK_HINT, fg=MUTED, bg=CARD).pack(side="left", padx=6)
            auto = fishdb.learned_name(fish["id"]) or fish["name"]
            var = tk.StringVar(value=names.get(fish["id"]) or auto)

            def changed(*_, fid=fish["id"], default=auto, v=var):
                text = v.get().strip()
                if text and text != default:
                    names[fid] = text
                else:
                    names.pop(fid, None)
                self._schedule_save()
            var.trace_add("write", changed)
            ctk.CTkEntry(card, width=190, textvariable=var, fg_color=FIELD, border_color=CARD_BORDER).grid(
                row=row, column=1, sticky="e", pady=3)

    def _animate_rainbow(self):
        """Легендарная редкость переливается: смена цвета пары надписей — нагрузки почти нет."""
        import colorsys
        try:
            self._rainbow_phase = (self._rainbow_phase + 0.04) % 1.0
            self._rainbow_labels = [lbl for lbl in self._rainbow_labels if lbl.winfo_exists()]
            r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(self._rainbow_phase, 0.7, 1.0))
            for lbl in self._rainbow_labels:
                lbl.configure(fg=f"#{r:02x}{g:02x}{b:02x}")
            self.root.after(120, self._animate_rainbow)
        except tk.TclError:
            pass  # окно закрыто

    def _catch_area_text(self):
        a = self.cfg.get("catch_area")
        return tr("{v0}×{v1} в X {v2}, Y {v3}", v0=a[2], v1=a[3], v2=a[0], v3=a[1]) if a else tr("Авто: центр окна Roblox")

    def _pick_catch_area(self):
        def done(area):
            self.cfg["catch_area"] = area
            self.catch_area_label.configure(text=self._catch_area_text(), fg=GREEN)
            self._save()
            self.log(tr("Область уведомления о рыбе: {area}", area=area))
        self._open_overlay(lambda: AreaSelector(self.root, self.cfg.get("catch_area"), done,
                                                hint=tr("Выделите место, где появляется «New Item <рыба>»")))

    def _on_catch(self, frames):
        """Из потока макроса: распознаём в отдельном потоке, чтобы не тормозить рыбалку."""
        def job():
            try:
                record()
            except Exception as exc:  # раньше такие ошибки терялись молча
                self.log_threadsafe(tr("Журнал улова: ошибка {exc!r}", exc=exc))

        def record():
            from . import ocr
            log = self.catch_log
            catch = log.add_frames(frames)
            if catch is None:
                if sys.platform == "win32" and not ocr.available():
                    self.log_threadsafe(tr("Журнал улова: Windows OCR недоступен — названия рыб не читаются"))
                    return
                seen = max(log.last_texts[-len(frames):], key=len) if log.last_texts else ""
                self.log_threadsafe(tr("Уведомления «New Item <рыба>» не нашёл — улов не записан. Прочитано: «{v0}». Проверьте область уведомления", v0=seen[:80] or 'ничего'))
                return
            name = log.name_of(catch)
            if catch.fish.get("name") == fishdb.UNKNOWN_NAME:
                raw = " / ".join(dict.fromkeys(log.last_raw)) or "?"
                self.log_threadsafe(tr("Поймано: рыба не распознана (прочитано «{v0}»), записана как {name}", v0=raw[:80], name=name))
            else:
                self.log_threadsafe(tr("Поймано: {name} (×{count})", name=name, count=catch.count))
            if catch.new:
                self.log_threadsafe(tr("🆕 Новая рыба в каталоге: {name}. Иконку можно привязать в «Улов → Каталог рыб»", name=name))
                self.events.put(("catalog", None))
                self.tg.send(tr("🆕 Новая рыба в каталоге: {name}", name=name), buttons=False)
            self.remote.poke()
            if self.cfg.get("catch_notify_each"):
                from .catches import RARITY_EMOJI
                data, kind = log.render(rows=[catch])
                self.tg.send_media(data, kind, tr("{v0} {name} × {count} · всего {total}", v0=RARITY_EMOJI.get(catch.rarity, '🐟'), name=name, count=catch.count, total=log.total))
        threading.Thread(target=job, daemon=True).start()

    def _test_catch(self):
        from .engine import catch_area
        area = catch_area(self.cfg)

        def job():
            from . import fishdb, ocr
            from .catches import read_notice
            rgb = screen.grab(area)
            lines = ocr.read_lines(rgb)
            if not ocr.available():
                self.log_threadsafe(tr("Windows OCR недоступен — уведомления о рыбе не читаются"))
                return
            text = " | ".join(line["text"] for line in lines) or tr("ничего")
            self.log_threadsafe(tr("Текст в области: {text}", text=text))
            notice = read_notice(rgb, lines, self.cfg) or self.catch_log.read_any(rgb)
            if not notice:
                self.log_threadsafe(tr("Уведомления «New Item <рыба>» нет — такие надписи макрос пропускает"))
                return
            name, rarity = notice
            title = fishdb.rarity_info(rarity)["title"].lower() if rarity else tr("не определена")
            fish = fishdb.find_by_name(name, self.cfg)
            if name == fishdb.UNKNOWN_NAME:
                self.log_threadsafe(tr("«New Item» есть, но название не похоже ни на одну рыбу из списка — улов запишется как Unknown Fish"))
                return
            known = tr("уже в каталоге") if fish else tr("новая — добавится при поимке")
            self.log_threadsafe(tr("Рыба: {name}, редкость по цвету: {title}, {known}", name=name, title=title, known=known))
        threading.Thread(target=job, daemon=True).start()

    def _send_catches(self):
        if not self.tg._creds()[0]:
            self.log(tr("Telegram не настроен — заполните токен и chat id в разделе «Телефон»"))
            return
        log = self.catch_log

        def job():
            from .tgcontrol import CATCH_BUTTONS
            data, kind = log.render()
            self.tg.send_media(data, kind, log.summary_text(), buttons=CATCH_BUTTONS if log.total else None)
        threading.Thread(target=job, daemon=True).start()

    def _clear_catches(self):
        self.catch_log.reset()
        self.log(tr("Журнал улова очищен"))
        self.remote.poke()

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
            # Точное время: панель досчитывает его сама. Округление до 10 с давало скачки назад
            "runtime": runtime,
            "blackout": self.blackout.active,
            "alarm": alarms.reason if alarms.active else None,
            "fish": self.catch_log.status(),
            "fish_log": bool(self.cfg.get("catch_log_enabled")),
            "lang": i18n.current(),          # мини-приложение говорит на том же языке
        }

    def _new_secret(self):
        self.cfg["remote_secret"] = config.new_secret()
        self._save()
        self.tg.refresh_menu_button()
        self.log(tr("Новая секретная ссылка создана — старая панель больше не работает. Нажмите «Отправить панель»"))

    def _on_tg(self, cmd):
        self.remote.poke()
        if cmd == "start":
            if not self.engine.running:
                self.engine.start()
            return
        if cmd == "alarm_off":
            self.engine.alarms.acknowledge()
            return
        if cmd == "catches":
            self._send_catches()
            return
        if cmd == "reset_ask":
            from .tgcontrol import RESET_CONFIRM
            total = self.catch_log.total
            if not total:
                self.tg.send(tr("🐟 Список улова и так пуст"), buttons=False)
            else:
                self.tg.send(tr("Очистить список улова? Сейчас в нём {total} рыб", total=total), buttons=RESET_CONFIRM)
            return
        if cmd == "reset_cancel":
            self.tg.send(tr("Список улова не тронут"), buttons=False)
            return
        if cmd == "reset_catches":
            self.catch_log.reset()
            self.log(tr("Журнал улова очищен из Telegram"))
            self.tg.send(tr("🗑 Список улова очищен"), buttons=False)
            return
        if cmd == "panel":
            if self._webapp_link():
                self.tg.send(tr("📱 Панель управления макросом — кнопка ниже или «Панель» слева от поля ввода"))
            else:
                self.log(tr("Мини-приложение выключено или не задан https-адрес"))
            return
        if cmd == "screen_on":
            self._blackout_off()
            self.tg.send(tr("🔆 Экран включён"))
        elif cmd == "screen_off":
            self._blackout_on()
        elif cmd == "stop":
            self.engine.stop()  # сообщение «Макрос остановлен» пришлёт _refresh_running
        elif cmd == "shot":
            def job():
                try:
                    png = screen.game_screenshot()  # чёрное окно в захват не попадает — видна игра
                except Exception as exc:
                    self.log_threadsafe(tr("Скриншот не удался: {exc}", exc=exc))
                    return
                self.tg.send_photo(png, status_text(self.engine, self.blackout.active))
            threading.Thread(target=job, daemon=True).start()
        elif cmd in ("status", "menu"):
            self.tg.send(status_text(self.engine, self.blackout.active) + "\n" + self.remote.diagnostics())

    def log(self, text):
        log.info(applog.redact(str(text), self.cfg))
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("[%H:%M:%S] ") + text + "\n")
        self.log_box.see("end")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_box.delete("1.0", f"{lines - 500}.0")
        self.log_box.configure(state="disabled")

    # ---------- помощь ----------
    def _copy_diagnostics(self):
        text = applog.diagnostics(self.cfg)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log(tr("Диагностика скопирована — вставьте её в сообщение автору (токенов и секретов в ней нет)"))

    def _open_logs(self):
        path = applog.log_dir()
        if sys.platform == "win32":
            os.startfile(path)
        else:
            self.log(path)

    def _export_settings(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                            initialfile="fishpilot-settings.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            config.export_settings(self.cfg, path)
            self.log(tr("Настройки сохранены в {path} (без токенов и секретов)", path=path))
        except OSError as exc:
            self.log(tr("Не удалось сохранить настройки: {exc}", exc=exc))

    def _import_settings(self):
        from tkinter import filedialog
        if self.engine.running:
            self.log(tr("Сначала остановите ловлю"))
            return
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            n = config.import_settings(self.cfg, path)
        except Exception as exc:
            self.log(tr("Не удалось загрузить настройки: {exc}", exc=exc))
            return
        self._save()
        # Поля окна берут значения из переменных — обновляем их и перестраиваем окно
        for key, var in list(self.vars.items()):
            try:
                var.set(self.cfg[key])
            except (tk.TclError, KeyError):
                pass
        self.rebuild()
        self.log(tr("Загружено настроек: {n}. Токены и секреты остались ваши", n=n))

    # ---------- обновление ----------
    def _startup_update(self):
        last = self.cfg.get("last_version")
        if last and last != __version__:
            self.log(tr("Программа обновлена: {last} → {__version__}", last=last, __version__=__version__))
        if last != __version__:
            self.cfg["last_version"] = __version__
            self._save()
        if updater.frozen():
            updater.cleanup()                     # старый exe от прошлого обновления
        if self.cfg.get("auto_update"):
            self._check_update(silent=True)
        self.root.after(6 * 3600 * 1000, self._startup_update)

    def _check_update(self, silent=True):
        if not updater.frozen():
            if not silent:
                self.log(tr("Обновление из программы работает только в exe (сейчас запуск из исходников)"))
            return
        if not silent:
            self.log(tr("Проверяю обновления…"))

        def job():
            try:
                upd = updater.check(updater.DEFAULT_REPO)   # адрес обновлений не берём из настроек
            except Exception as exc:
                if not silent:
                    self.log_threadsafe(tr("Не удалось проверить обновления: {exc}", exc=exc))
                return
            self.call_soon(lambda: self._on_update_found(upd, silent))
        threading.Thread(target=job, daemon=True).start()

    def _on_update_found(self, upd, silent):
        if upd is None:
            if not silent:
                self.log(tr("У вас последняя версия ({__version__})", __version__=__version__))
            return
        first = self._update is None or self._update.version != upd.version
        self._update = upd
        self.update_btn.configure(text=tr("⬆ Обновить до {version}", version=upd.version))
        if not self.update_btn.winfo_manager():
            self.update_btn.pack(fill="x", pady=(0, 6), before=self._donate_btn)
        if first:
            self.log(tr("Доступна новая версия {version} — нажмите «Обновить» слева внизу", version=upd.version))
            if self.tg._creds()[0]:
                self.tg.send(tr("⬆ Доступна новая версия макроса {version}. Обновить — кнопкой в программе", version=upd.version),
                             buttons=False)

    def _do_update(self):
        if self._updating:
            return
        if self._update is None:
            self._check_update(silent=False)
            return
        if not updater.frozen():
            self.log(tr("Обновление из программы работает только в exe"))
            return
        if self.engine.running:
            self.log(tr("Сначала остановите ловлю, потом обновляйтесь"))
            return
        self._updating = True
        upd = self._update
        self.update_btn.configure(text=tr("Скачиваю…"), state="disabled")
        self.log(tr("Скачиваю версию {version}…", version=upd.version))
        shown = [0]

        def progress(frac):
            step = int(frac * 4)
            if step > shown[0]:
                shown[0] = step
                self.log_threadsafe(tr("Обновление: {v0}%", v0=step * 25))

        def job():
            try:
                new = updater.download(upd, os.path.join(self._update_dir(), updater.ZIP_ASSET), progress)
            except Exception as exc:
                self.log_threadsafe(tr("Обновление не скачалось: {exc}", exc=exc))
                self.call_soon(self._update_failed)
                return
            self.call_soon(lambda: self._install_update(new, upd))
        threading.Thread(target=job, daemon=True).start()

    def _update_failed(self):
        self._updating = False
        if self._update:
            self.update_btn.configure(text=tr("⬆ Обновить до {version}", version=self._update.version), state="normal")

    def _update_dir(self):
        return os.path.join(config.data_dir(), "update")

    def _install_update(self, new, upd):
        self._save()
        try:
            app_dir = updater.install(new, self._update_dir())
        except Exception as exc:
            log.exception("update install failed")
            self.log(tr("Не удалось установить обновление: {exc}", exc=exc))
            self._update_failed()
            return
        if updater.onefile():
            # Один exe стал папкой: дальше запускать FishPilot.exe из неё
            self.log(tr("FishPilot теперь живёт в папке {path} — запускайте FishPilot.exe оттуда, "
                        "старый exe можно удалить", path=app_dir))
        self.log(tr("Перезапуск в версии {version}…", version=upd.version))
        self.root.after(300, self.exit)

    def exit(self):
        if self._closing:
            return
        self._closing = True
        # Сначала ловля: дождаться, пока поток отпустит ЛКМ, и на всякий случай отпустить её самим
        self._safe(self.engine.stop)
        self._safe(self.engine.join, 1.5)
        self._safe(winput.mouse_up)
        self._safe(self._blackout_off)
        self._safe(self.hud.hide)
        self._safe(self.tg.stop)
        self._safe(self.remote.stop)
        self._safe(self._save)
        if getattr(self, "listener", None):
            self._safe(self.listener.stop)
        if getattr(self, "mouse_listener", None):
            self._safe(self.mouse_listener.stop)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ---------- горячие клавиши и события из потоков ----------
    def _start_hotkeys(self):
        self.listener = None
        if keyboard is None:
            self.log(tr("pynput не установлен — горячие клавиши F1/F2/F3 не работают"))
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
            taken = [k for k in self.hotkey_labels if k != key and self.cfg.get(k) == name]
            if name in ("esc", "enter") or taken:
                # Esc/Enter нужны рамке выбора области, а одна клавиша на два действия срабатывает не так
                self.hotkey_labels[key].configure(text=self.cfg[key].upper(), text_color=ACCENT)
                self.log(tr("Клавишу {name} нельзя: она уже занята или нужна программе", name=name.upper()))
                return
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
            elif name == "enter" and hasattr(self._overlay, "confirm"):
                self._overlay.confirm()  # Enter ловим глобально: оверлею Windows не всегда даёт фокус
            elif name == self.cfg["hotkey_change_area"]:
                self.change_area()
            return
        if name == self.cfg.get("hotkey_hud"):
            self.cfg["hud_enabled"] = not self.cfg.get("hud_enabled")
            if "hud_enabled" in self.vars:
                self.vars["hud_enabled"].set(self.cfg["hud_enabled"])
            self._save()
            self.log(tr("HUD включён") if self.cfg["hud_enabled"] else tr("HUD выключен"))
            self._tick_hud(once=True)
        elif name == self.cfg["hotkey_start_stop"]:
            self.engine.toggle()
        elif name == self.cfg["hotkey_change_area"]:
            self.change_area()
        elif name == self.cfg["hotkey_exit"]:
            self.exit()

    def _on_tk_error(self, exc_type, exc, tb):
        log.error("Ошибка в окне", exc_info=(exc_type, exc, tb))
        try:
            self.log(tr("Ошибка: {exc}. Подробности — в «Программа → Помощь → Скопировать диагностику»", exc=exc))
        except Exception:
            pass

    def call_soon(self, fn):
        """Выполнить fn в главном потоке окна. Из фоновых потоков нельзя трогать Tk (и root.after)."""
        self.events.put(("call", fn))

    def _handle_event(self, kind, payload):
        if kind == "key":
            self._on_key(payload)
        elif kind == "log":
            self.log(payload)
        elif kind == "status":
            text, color = payload
            self._status_text = text
            self._status_color = color
            self.remote.poke()
            self.status.configure(text=f"●  {text}", fg_color=STATUS_COLORS.get(color, color))
        elif kind == "stats":
            self._stats = payload
            self.remote.poke()
        elif kind == "click":
            if self.blackout.active:
                self._blackout_off(notify=True)
        elif kind == "catalog":
            self._fill_catalog()
        elif kind == "tg":
            self._on_tg(payload)
        elif kind == "call":
            payload()

    def _poll(self):
        """Очередь событий из потоков. Ошибка в одном событии не должна останавливать цикл: раньше после
        неё переставали работать горячие клавиши, журнал и выход из чёрного экрана."""
        if self._closing:
            return
        for _ in range(200):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(kind, payload)
            except Exception:
                log.exception("Ошибка при обработке события %s", kind)
                if self.blackout.active and kind in ("key", "click"):
                    self._safe(self._blackout_off)        # из чёрного экрана выйти можно всегда
            if self._closing:
                return
        self._safe(self._refresh_running)
        self._safe(self._refresh_stats)
        try:
            self.root.after(100, self._poll)
        except tk.TclError:
            pass  # окно уже закрыто

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception:
            log.exception("Ошибка в %s", getattr(fn, "__name__", fn))
            return None

    def _refresh_running(self):
        running = self.engine.running
        if running == self._was_running:
            return
        was_running, self._was_running = self._was_running, running
        if running:
            self.start_btn.configure(text=tr("■  Стоп"), fg_color=RED, hover_color=RED_HOVER)
            if self.cfg.get("blackout_enabled") and not self.blackout.active:
                delay = max(float(self.cfg.get("blackout_delay") or 0), 0)
                self._blackout_job = self.root.after(int(delay * 1000), self._blackout_on)
        else:
            self.start_btn.configure(text=tr("▶  Старт"), fg_color=GREEN, hover_color=GREEN_HOVER)
            if was_running:
                # Макрос остановился (вручную или по тревоге) — экран обратно, чтобы было видно, что случилось
                self._blackout_off()
                self.tg.send(tr("⏹ Макрос остановлен\n") + status_text(self.engine, False))

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
