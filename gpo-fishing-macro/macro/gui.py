"""Окно управления макросом (CustomTkinter): тёмная тема, карточки, вкладки как в оригинале."""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk

import customtkinter as ctk

from . import alarm, config, fishdb, screen
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


def italic_pad(text):
    """Наклонный текст шире прямого, а Tk меряет ширину по прямому — последний символ
    обрезается справа. Пара пробелов в конце даёт запас под наклон."""
    return f"{text}  "


def fmt_point(p):
    return f"X {p[0]}  ·  Y {p[1]}" if p else "Не задано"


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
        self.root.title("GPO — макрос рыбалки")
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
        self._point_labels = {}
        self._guide = None
        self._stats = {}
        self._was_running = None

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
                f"Каталог рыб: исправлено или убрано записей с ошибками распознавания: {n}"))

        self.blackout = Blackout(self.root, self.log_threadsafe)
        from .hud import Hud
        self.hud = Hud(self.root, self.log_threadsafe, font=self.display)
        self.root.after(1000, self._tick_hud)
        self._blackout_job = None
        self._status_text = "Остановлен"
        self._status_color = "red"
        self.tg = TelegramControl(self.cfg, lambda cmd: self.events.put(("tg", cmd)), self.log_threadsafe,
                                  webapp_link=self._webapp_link)
        self.remote = Remote(self.cfg, self._remote_status, lambda cmd: self.events.put(("tg", cmd)),
                             self.log_threadsafe)

        self._build()
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
        tk.Label(card, text=italic_pad(title.upper()), font=TK_TITLE, fg=HILITE, bg=CARD).pack(
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
    def _build(self):
        root = self.root
        # Шапка — картинка, нарисованная один раз (theme.render_banner), поверх неё статус и Start
        banner = theme.render_banner(600, 110)
        self._banner = ctk.CTkImage(banner, size=(600, 110))
        header = ctk.CTkLabel(root, text="", image=self._banner, fg_color=BG, height=110)
        header.pack(fill="x", pady=(0, 8))
        self.status = ctk.CTkLabel(header, text="  ●  ОСТАНОВЛЕН  ", font=(self.display, 13, "bold", "italic"),
                                   text_color="white", fg_color=RED, corner_radius=0, height=22)
        self.status.place(x=22, y=70)
        self.start_btn = ctk.CTkButton(header, text="▶  СТАРТ ", width=150, height=48, corner_radius=0,
                                       font=(self.display, 20, "bold", "italic"), fg_color=GREEN,
                                       hover_color=GREEN_HOVER, border_width=2, border_color=HILITE,
                                       command=self.engine.toggle)
        self.start_btn.place(relx=1, x=-20, y=26, anchor="ne")
        ctk.CTkButton(header, text="?  ГАЙД", width=74, height=22, corner_radius=0, fg_color="#0b3478",
                      hover_color=ACCENT, border_width=1, border_color=HILITE, text_color=HILITE,
                      font=(self.display, 12, "bold", "italic"), command=self.open_guide).place(
            relx=1, x=-150, y=78, anchor="ne")
        self.start_hint = ctk.CTkLabel(header, text="", font=(FONT, 11, "bold"), text_color=HILITE,
                                       fg_color="#0b3478", corner_radius=0, height=18)
        self.start_hint.place(relx=1, x=-20, y=78, anchor="ne")

        stats = ctk.CTkFrame(root, fg_color="transparent")
        stats.pack(fill="x", padx=16, pady=(0, 8))
        self.stat_values = {}
        for i, (key, title) in enumerate((("catches", "Поймано"), ("casts", "Забросов"), ("runtime", "Время"),
                                          ("rate", "Рыбы в час"))):
            stats.columnconfigure(i, weight=1, uniform="stats")
            card = ctk.CTkFrame(stats, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=4)
            card.grid(row=0, column=i, sticky="ew", padx=3)
            tk.Frame(card, bg=HILITE, height=3).place(x=1, y=1, relwidth=0.35, height=3)
            value = ctk.CTkLabel(card, text="0", font=(self.display, 26, "bold", "italic"), text_color=TEXT)
            value.pack(pady=(8, 0))
            ctk.CTkLabel(card, text=italic_pad(title.upper()), font=(self.display, 11, "italic"),
                         text_color=HILITE).pack(pady=(0, 8))
            self.stat_values[key] = value

        tabs = ctk.CTkTabview(root, fg_color=BG, segmented_button_fg_color=CARD,
                              segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT_HOVER,
                              segmented_button_unselected_color=CARD,
                              segmented_button_unselected_hover_color=FIELD, corner_radius=4)
        tabs._segmented_button.configure(font=(self.display, 14, "bold"), corner_radius=2)
        tabs.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        for title, builder in (("Основное", self._tab_general), ("Заброс", self._tab_casting),
                               ("Рыбалка", self._tab_fishing), ("Подготовка", self._tab_precast),
                               ("Всё остальное", self._tab_else)):
            builder(tabs.add(title))
        self._refresh_hotkey_hint()

    # --- General ---
    def _tab_general(self, tab):
        page = self._scroll(tab)
        hk = self._card(page, "Горячие клавиши", "Работают везде, даже когда активно окно игры")
        self.hotkey_labels = {}
        for label, key in (("Старт / стоп", "hotkey_start_stop"), ("Выбрать область", "hotkey_change_area"),
                           ("Выход", "hotkey_exit"), ("Экран выкл / вкл", "hotkey_blackout"),
                           ("HUD вкл / выкл", "hotkey_hud")):
            row = self._row(hk)
            self._label(hk, row, label)
            right = tk.Frame(hk, bg=CARD)
            right.grid(row=row, column=1, sticky="e", pady=4)
            cap = ctk.CTkLabel(right, text=self.cfg[key].upper(), width=56, height=30, corner_radius=2,
                               fg_color=FIELD, text_color=ACCENT, font=(FONT, 13, "bold"))
            cap.pack(side="left", padx=(0, 8))
            ctk.CTkButton(right, text="Сменить", width=80, height=30, fg_color="transparent", border_width=1,
                          border_color=CARD_BORDER, hover_color=FIELD,
                          command=lambda k=key: self._rebind(k)).pack(side="left")
            self.hotkey_labels[key] = cap

        win = self._card(page, "Окно")
        self._switch(win, "Поверх всех окон", "always_on_top", command=self._apply_topmost)
        self._switch(win, "HUD поверх игры", "hud_enabled",
                     "Рамка области мини-игры и показатели видны всё время. Клавиша — в «Горячих клавишах»",
                     command=lambda: self._tick_hud(once=True))

        bo = self._card(page, "Чёрный экран во время рыбалки",
                        "Вы видите чёрный экран, а макрос по-прежнему видит игру")
        self._switch(bo, "Гасить экран после старта", "blackout_enabled",
                     "Вернуть: любая клавиша или кнопка мыши, горячая клавиша или кнопка в Telegram")
        self._switch(bo, "Все мониторы", "blackout_all_monitors", "Выкл — только монитор с Roblox")
        self._num(bo, "Задержка перед затемнением", "blackout_delay", "После нажатия «Старт»", unit="с")

        nh = self._card(page, "Слоты предметов", "Номера слотов на панели в игре")
        self._option(nh, "Слот удочки", "rod_hotkey", DIGITS, "Где лежит удочка")
        self._option(nh, "Любой другой слот", "other_hotkey", DIGITS,
                     "Нажимается перед удочкой, чтобы удочка точно оказалась в руках")

    # --- Casting ---
    def _tab_casting(self, tab):
        page = self._scroll(tab)
        wp = self._card(page, "Точка воды", "Куда целится курсор при забросе")
        self._point(wp, "Указать точку воды", "water_point", "Кликните по воде, куда забрасывать удочку")
        ct = self._card(page, "Время заброса")
        self._num(ct, "Удержание при забросе", "cast_hold", "Сколько держать ЛКМ при забросе", unit="с")
        self._num(ct, "Перезаброс через", "recast_timeout", "Если за это время не клюнуло", unit="с")

    # --- Fishing ---
    def _tab_fishing(self, tab):
        page = self._scroll(tab)
        pd = self._card(page, "PD-регулятор", "Держит вашу тёмную полосу на белой черте рыбы")
        self._num(pd, "Kp (пропорциональный)", "kp", "Насколько сильно тянуть полосу к рыбе")
        self._num(pd, "Kd (дифференциальный)", "kd", "Гашение: увеличьте, если полоса проскакивает рыбу")
        self._num(pd, "Ограничение PD", "pd_clamp", "Предел выхода регулятора")
        cl = self._card(page, "Журнал улова", "Список пойманной рыбы с иконками — кнопка «🐟 Улов» в Telegram")
        self._switch(cl, "Вести журнал улова", "catch_log_enabled",
                     "После каждой мини-игры снимает уведомление о рыбе и читает название (Windows OCR)")
        row = self._row(cl)
        self._label(cl, row, "Область уведомления", "Где игра пишет, какая рыба поймана (иконка + название)")
        self.catch_area_label = tk.Label(cl, text=self._catch_area_text(), font=TK_LABEL, bg=CARD,
                                         fg=GREEN if self.cfg["catch_area"] else HILITE)
        self.catch_area_label.grid(row=row, column=1, sticky="e", pady=4)
        self._num(cl, "Снимок через", "catch_capture_delay",
                  "Сколько ждать после конца мини-игры, пока появится уведомление", unit="с")
        self._switch(cl, "Сообщать о каждой рыбе", "catch_notify_each", "Присылать в Telegram каждую поимку")
        self._buttons(cl, ("Выбрать область", self._pick_catch_area, ACCENT, ACCENT_HOVER),
                      ("Проверить сейчас", self._test_catch, FIELD, CARD_BORDER))
        self._buttons(cl, ("Отправить улов", self._send_catches, FIELD, CARD_BORDER),
                      ("Очистить", self._clear_catches, FIELD, CARD_BORDER))
        self._fish_catalog_card(page)
        tm = self._card(page, "Тайминги")
        self._num(tm, "Пауза после рыбы", "fish_end_delay", "Ожидание после конца мини-игры", unit="с")

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
            self._switch(card, "Включено", flag_key)
            self.vars[flag_key].trace_add("write", toggle)
            toggle()

        def buy(f):
            tk.Label(f, text="Порядок: E → левая → средняя → количество → левая → правая → средняя",
                     font=TK_HINT, fg=MUTED, bg=CARD).grid(row=self._row(f), column=0, columnspan=2,
                                                                 sticky="w", pady=(0, 4))
            self._point(f, "Левая точка", "buy_left", "Кликните по левой кнопке покупки")
            self._point(f, "Средняя точка", "buy_middle", "Кликните по средней кнопке покупки")
            self._point(f, "Правая точка", "buy_right", "Кликните по правой кнопке покупки")
            self._num(f, "Циклов на покупку", "loops_per_purchase", "Покупать наживку каждые N циклов", kind=int)
            self._switch(f, "Вводить количество", "buy_type_amount", "Вписывает «циклов на покупку» после первого клика по средней точке")
            self._switch(f, "Не покупать при старте", "buy_skip_first",
                         "Первая покупка — только через «Циклов на покупку», а не сразу после старта")

        def fruit(f):
            self._option(f, "Слот фрукта", "fruit_hotkey", DIGITS)
            self._point(f, "Точка Store", "store_point", "Кликните по кнопке Store")

        def bait(f):
            self._point(f, "Точка наживки", "bait_point", "Кликните по верхней наживке в списке")

        section("auto_buy_bait", "Автопокупка наживки", "Встаньте рядом с продавцом наживки", buy)
        section("auto_store_fruit", "Автосохранение фрукта", "Убирает выловленные дьявольские фрукты в хранилище", fruit)
        section("auto_select_bait", "Выбор верхней наживки", "Кликает верхнюю наживку перед каждым забросом", bait)

    # --- Literally Everything Else ---
    def _tab_else(self, tab):
        warn = ctk.CTkFrame(tab, fg_color=WARN_BG, border_color=RED, border_width=1, corner_radius=2)
        warn.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(warn, text="⚠  НЕ МЕНЯЙТЕ, ЕСЛИ НЕ ЗНАЕТЕ, ЧТО ДЕЛАЕТЕ ",
                     font=(FONT, 13, "bold"), text_color="#ff7b72").pack(padx=12, pady=8)
        # Подразделы: в каждом немного карточек — прокрутка короче и не тормозит
        groups = ["Задержки", "Античит и PD", "Будильник", "Распознавание", "Журнал"]
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

        page = pages["Задержки"]
        f = self._card(page, "Фокус окна Roblox")
        self._num(f, "Задержка фокуса (с)", "focus_delay",
                  "Ожидание после переключения на окно Roblox")
        self._num(f, "Доп. задержка после фокуса (с)", "post_focus_delay",
                  "Дополнительная пауза для стабильности")

        f = self._card(page, "Задержки покупки наживки")
        self._num(f, "После нажатия E (с)", "buy_e_delay", "Ожидание открытия магазина")
        self._num(f, "Между кликами (с)", "buy_click_delay", "Пауза между кликами при покупке")
        self._num(f, "После ввода числа (с)", "buy_type_delay", "Ожидание после ввода количества наживки")
        self._num(f, "Микрозадержка мыши (с)", "micro_delay",
                  "Крошечная пауза при движении мыши против детекта Roblox (0.05 = 50 мс)")

        f = self._card(page, "Задержки сохранения фрукта")
        self._num(f, "После клавиши фрукта (с)", "fruit_hotkey_delay", "Ожидание после нажатия слота фрукта")
        self._num(f, "После клика Store (с)", "fruit_store_delay", "Ожидание после клика по точке Store")
        self._num(f, "После первого Shift (с)", "fruit_shift_delay", "Ожидание после первого нажатия Shift")
        self._num(f, "После Backspace (с)", "fruit_backspace_delay", "Ожидание после нажатия Backspace")

        f = self._card(page, "Задержка выбора наживки")
        self._num(f, "После клика по наживке (с)", "bait_click_delay", "Ожидание после клика по точке наживки")

        f = self._card(pages["Античит и PD"], "Чёрный экран античита")
        self._num(f, "Порог чёрного экрана", "black_threshold",
                  "Доля окна Roblox, которая должна стать чёрной (0.85 = 85%, 0 — выключить)")
        self._num(f, "Пауза между нажатиями (с)", "black_spam_delay",
                  "Как часто жать клавишу, чтобы убрать чёрный экран")
        self._num(f, "Клавиша", "black_spam_key", "Какую клавишу жать (space, e, enter, 1…)", kind=str)
        self._num(f, "Тревога через (с)", "black_max_sec",
                  "Позвонить, если чёрный экран не ушёл за это время")

        f = self._card(page, "Задержки рыбалки")
        self._num(f, "Выбор удочки (с)", "rod_select_delay",
                  "Пауза между нажатиями слотов при выборе удочки")
        self._num(f, "После движения курсора (с)", "cursor_delay",
                  "Крошечная пауза после перемещения курсора (0.05 = 50 мс)")
        self._num(f, "Период сканирования (с)", "scan_loop_delay",
                  "Пауза между проверками экрана в ожидании поклёвки (меньше — быстрее, но больше нагрузка)")

        page = pages["Античит и PD"]
        f = self._card(page, "Гашение PD (продвинутое)")
        self._num(f, "Множитель при сближении", "approach_damping",
                  "Тормоз, когда полоса едет К рыбе (больше — сильнее тормоз)")
        self._num(f, "Множитель при погоне", "chase_damping",
                  "Гашение, когда рыба уходит ОТ полосы")
        self._num(f, "Множитель разрыва", "gap_mult",
                  "Склейка тёмно-серой полосы: допуск = высота белой черты × множитель")

        f = self._card(page, "Синхронизация кнопки")
        self._num(f, "Повтор состояния (с)", "resend_interval",
                  "Повторять нажатие/отпускание ЛКМ, если состояние не менялось (против рассинхрона)")

        page = pages["Будильник"]
        al = self._card(page, "Будильник", "Звонит на телефон, если что-то пошло не так")
        self._switch(al, "Звонить на телефон", "alarm_enabled", "Когда что-то пошло не так (см. «Сторож»)")
        self._text(al, "Сервер ntfy", "ntfy_server")
        self._text(al, "Тема ntfy", "ntfy_topic", "Та же тема, что в приложении ntfy")
        self._text(al, "Токен Telegram-бота", "telegram_token", secret=True)
        self._text(al, "Chat id в Telegram", "telegram_chat_id", "Ваш id, не бота: «Узнать chat id» найдёт его сам")
        self._buttons(al, ("Проверить Telegram", self._check_tg, ACCENT, ACCENT_HOVER),
                      ("Узнать chat id", self._find_chat_id, FIELD, CARD_BORDER))
        self._text(al, "Pushover: user key", "pushover_user", secret=True)
        self._text(al, "Pushover: app token", "pushover_token", secret=True)
        self._num(al, "Повторять тревогу (с)", "alarm_repeat_sec",
                  "Пока не нажмёте «Стоп тревоги» или «Старт» (0 = один раз)")
        self._switch(al, "Прикладывать скриншот (Telegram)", "alarm_screenshot")
        self._switch(al, "Кнопки управления в Telegram", "tg_control",
                     "Кнопки бота: экран, скриншот, статус, стоп. Меню — команда /menu")

        ma = self._card(page, "Мини-приложение Telegram", "Живая панель в Telegram: статистика, старт/стоп, экран, тревога")
        self._switch(ma, "Включить мини-приложение", "remote_enabled",
                     "Статус и команды идут через ntfy под секретным именем темы")
        self._text(ma, "Адрес мини-приложения", "webapp_url", "Страница на GitHub Pages")
        self._buttons(ma, ("Отправить панель", lambda: self._on_tg("panel"), ACCENT, ACCENT_HOVER),
                      ("Новая ссылка", self._new_secret, FIELD, CARD_BORDER))
        self._buttons(al, ("Тест тревоги", self._test_alarm, ACCENT, ACCENT_HOVER),
                      ("Стоп тревоги", self.engine.alarms.acknowledge, FIELD, CARD_BORDER))

        wd = self._card(page, "Сторож", "Когда звонить")
        self._num(wd, "Забросов без поклёвки подряд", "wd_failed_casts",
                  "Кончилась наживка, дисконнект или сдвинули персонажа (0 = выкл)", kind=int)
        self._num(wd, "Нет рыбы дольше (мин)", "wd_no_catch_min", "Ни одной рыбы за это время (0 = выкл)")
        self._num(wd, "Окно Roblox потеряно (с)", "wd_window_grace_sec",
                  "Roblox закрыт или не активен дольше (вылет, окно свёрнуто)")
        self._switch(wd, "Следить за окном Roblox", "wd_check_window")
        self._switch(wd, "Останавливать макрос при тревоге", "wd_stop_on_alarm")

        page = pages["Распознавание"]
        dt = self._card(page, "Распознавание мини-игры")
        row = self._row(dt)
        self._label(dt, row, "Цвет трека (синий)", "Нажмите «Взять» и кликните по синему треку")
        right = tk.Frame(dt, bg=CARD)
        right.grid(row=row, column=1, sticky="e", pady=4)
        self.color_swatch = ctk.CTkLabel(right, text="", width=70, height=28, corner_radius=6)
        self.color_swatch.pack(side="left", padx=(0, 6))
        ctk.CTkButton(right, text="Взять", width=60, height=28, fg_color=FIELD, hover_color=ACCENT,
                      command=self._pick_color).pack(side="left")
        self._update_swatch()
        self._num(dt, "Допуск цвета", "zone_tolerance", "Насколько пиксель может отличаться от цвета трека", kind=int)
        self._num(dt, "Яркость тёмно-серого", "dark_max", "Самый яркий пиксель, который ещё считается полосой игрока",
                  kind=int)
        self._option(dt, "Полоса игрока", "bar_mode", ["dark", "blue"],
                     "dark — тёмно-серая на синем треке (обычно), blue — наоборот")
        self._switch(dt, "Требовать зелёную полосу", "require_progress",
                     "Мини-игра засчитывается, только если рядом зелёная полоса прогресса (против ложного спама ЛКМ)")
        self._num(dt, "Яркость черты рыбы", "fish_white_min", "Минимальная яркость белой черты (0–255)",
                  kind=int)
        self._num(dt, "Мин. пикселей трека", "min_zone_pixels", "Меньше синих пикселей — мини-игры на экране нет", kind=int)
        self._num(dt, "Частота в мини-игре (FPS)", "scan_fps", "Проверок экрана в секунду во время вываживания", kind=int)
        self.area_label = tk.Label(dt, text=self._area_text(), font=TK_LABEL, bg=CARD,
                                   fg=GREEN if self.cfg["area"] else MUTED)
        self.area_label.grid(row=self._row(dt), column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._buttons(dt, ("Выбрать область", self.change_area, ACCENT, ACCENT_HOVER),
                      ("Окно отладки", self._open_preview, FIELD, CARD_BORDER))

        lg = self._card(pages["Журнал"], "Журнал")
        self.log_box = ctk.CTkTextbox(lg, height=330, fg_color=FIELD, font=("Consolas", 11), wrap="word",
                                      state="disabled")
        self.log_box.grid(row=0, column=0, columnspan=2, sticky="ew")
        nav.set("Задержки")
        show("Задержки")

    # ---------- действия ----------
    def _apply_topmost(self):
        self.root.attributes("-topmost", bool(self.cfg["always_on_top"]))

    def _area_text(self):
        a = self.cfg["area"]
        return f"Область: X {a[0]}, Y {a[1]}, {a[2]}×{a[3]}" if a else "Область не задана (F2 во время мини-игры)"

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

    def _check_tg(self):
        from .tgcontrol import diagnose
        self.log("Проверяю Telegram…")

        def job():
            ok, lines = diagnose(self.cfg)
            for line in lines:
                self.log_threadsafe("Telegram: " + line)
            self.log_threadsafe("Telegram работает ✓" if ok else "Telegram не настроен — см. строки выше")
        threading.Thread(target=job, daemon=True).start()

    def _find_chat_id(self):
        from .tgcontrol import clean_token, explain, find_chats
        token = clean_token(self.cfg.get("telegram_token"))
        if not token:
            self.log("Сначала вставьте токен бота")
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
                self.log_threadsafe("Никто ещё не писал боту: откройте его в Telegram, нажмите «Start» "
                                    "(или напишите любое сообщение) и нажмите «Узнать chat id» ещё раз")
                return
            if len(chats) > 1:
                self.log_threadsafe("Боту писали несколько человек: " +
                                    ", ".join(f"{n} — {cid}" for cid, n in chats.items()) +
                                    ". Впишите свой chat id вручную")
                return
            (cid, name), = chats.items()
            self.root.after(0, lambda: self._set_chat_id(cid, name))
        threading.Thread(target=job, daemon=True).start()

    def _set_chat_id(self, cid, name):
        self.var("telegram_chat_id", str).set(cid)
        self.log(f"Chat id найден: {cid} ({name}). Теперь нажмите «Проверить Telegram»")

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
            self.log("pynput не установлен — горячие клавиши недоступны")
            return
        self._rebinding = key
        self.hotkey_labels[key].configure(text="...", text_color=ORANGE)

    def _refresh_hotkey_hint(self):
        self.start_hint.configure(text=f"  ИЛИ КЛАВИША {self.cfg['hotkey_start_stop'].upper()}  ")

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
        """HUD видно всё время, кроме выбора области/точки (там своя рамка) и чёрного экрана."""
        try:
            want = (bool(self.cfg.get("hud_enabled")) and not self._overlay_open()
                    and not self.blackout.active)
            if want and not self.hud.visible:
                self.hud.show()
            elif not want and self.hud.visible:
                self.hud.hide()
            if self.hud.visible:
                self.hud.update(self._hud_data())
        except Exception as exc:
            self.log(f"HUD: ошибка {exc!r} — выключаю")
            self.cfg["hud_enabled"] = False
            self.hud.hide()
        if not once:
            self.root.after(150, self._tick_hud)

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

    # ---------- журнал улова ----------
    def _fish_catalog_card(self, page):
        """Каталог рыб: иконка, редкость и название. Новые рыбы появляются здесь сами."""
        self._catalog_body = self._card(page, "Каталог рыб",
                                        "Названия — из уведомления игры. Можно поменять своё. Новые рыбы добавляются сами")
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
        rarity_titles = {fishdb.NEW_RARITY["title"]: None}
        rarity_titles.update({v["title"]: k for k, v in fishdb.RARITIES.items()})
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
                    self.log(f"Иконка привязана: {fishdb.display_name(fishdb.by_id(bid), self.cfg)}")
                    self.remote.poke()
                    self.root.after(10, self._fill_catalog)
                ctk.CTkOptionMenu(left, values=list(choices), variable=tk.StringVar(value="Иконка со скрина…"),
                                  command=link, width=150, fg_color=FIELD, button_color=CARD_BORDER,
                                  button_hover_color=ACCENT, dynamic_resizing=False,
                                  font=(self.display, 12, "bold")).pack(side="left", padx=(6, 0))

                def remove(fid=fish["id"], title=fishdb.display_name(fish, self.cfg)):
                    fishdb.remove_fish(fid)
                    names.pop(fid, None)
                    self.catch_log.forget(fid)
                    self._schedule_save()
                    self.log(f"Удалено из каталога: {title}")
                    self.remote.poke()
                    self.root.after(10, self._fill_catalog)
                ctk.CTkButton(left, text="✕", width=30, command=remove, fg_color=FIELD, hover_color=RED,
                              font=(self.display, 12, "bold")).pack(side="left", padx=(6, 0))
            else:
                color = info["color"]
                lbl = tk.Label(left, text=italic_pad(info["title"].upper()), font=(self.display, 10, "bold", "italic"),
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
        return f"{a[2]}×{a[3]} в X {a[0]}, Y {a[1]}" if a else "Авто: центр окна Roblox"

    def _pick_catch_area(self):
        def done(area):
            self.cfg["catch_area"] = area
            self.catch_area_label.configure(text=self._catch_area_text(), fg=GREEN)
            self._save()
            self.log(f"Область уведомления о рыбе: {area}")
        self._open_overlay(lambda: AreaSelector(self.root, self.cfg.get("catch_area"), done,
                                                hint="Выделите место, где появляется «New Item <рыба>»"))

    def _on_catch(self, frames):
        """Из потока макроса: распознаём в отдельном потоке, чтобы не тормозить рыбалку."""
        def job():
            try:
                record()
            except Exception as exc:  # раньше такие ошибки терялись молча
                self.log_threadsafe(f"Журнал улова: ошибка {exc!r}")

        def record():
            from . import ocr
            log = self.catch_log
            catch = log.add_frames(frames)
            if catch is None:
                if sys.platform == "win32" and not ocr.available():
                    self.log_threadsafe("Журнал улова: Windows OCR недоступен — названия рыб не читаются")
                    return
                seen = max(log.last_texts[-len(frames):], key=len) if log.last_texts else ""
                self.log_threadsafe("Уведомления «New Item <рыба>» не нашёл — улов не записан. "
                                    f"Прочитано: «{seen[:80] or 'ничего'}». Проверьте область уведомления")
                return
            name = log.name_of(catch)
            if catch.fish.get("name") == fishdb.UNKNOWN_NAME:
                raw = " / ".join(dict.fromkeys(log.last_raw)) or "?"
                self.log_threadsafe(f"Поймано: рыба не распознана (прочитано «{raw[:80]}»), записана как {name}")
            else:
                self.log_threadsafe(f"Поймано: {name} (×{catch.count})")
            if catch.new:
                self.log_threadsafe(f"🆕 Новая рыба в каталоге: {name}. Иконку можно привязать в «Рыбалка → Каталог рыб»")
                self.events.put(("catalog", None))
                self.tg.send(f"🆕 Новая рыба в каталоге: {name}", buttons=False)
            self.remote.poke()
            if self.cfg.get("catch_notify_each"):
                from .catches import RARITY_EMOJI
                data, kind = log.render(rows=[catch])
                self.tg.send_media(data, kind, f"{RARITY_EMOJI.get(catch.rarity, '🐟')} {name} × {catch.count}"
                                               f" · всего {log.total}")
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
                self.log_threadsafe("Windows OCR недоступен — уведомления о рыбе не читаются")
                return
            text = " | ".join(line["text"] for line in lines) or "ничего"
            self.log_threadsafe(f"Текст в области: {text}")
            notice = read_notice(rgb, lines, self.cfg) or self.catch_log.read_any(rgb)
            if not notice:
                self.log_threadsafe("Уведомления «New Item <рыба>» нет — такие надписи макрос пропускает")
                return
            name, rarity = notice
            title = fishdb.rarity_info(rarity)["title"].lower() if rarity else "не определена"
            fish = fishdb.find_by_name(name, self.cfg)
            if name == fishdb.UNKNOWN_NAME:
                self.log_threadsafe("«New Item» есть, но название не похоже ни на одну рыбу из списка — "
                                    "улов запишется как Unknown Fish")
                return
            known = "уже в каталоге" if fish else "новая — добавится при поимке"
            self.log_threadsafe(f"Рыба: {name}, редкость по цвету: {title}, {known}")
        threading.Thread(target=job, daemon=True).start()

    def _send_catches(self):
        if not self.tg._creds()[0]:
            self.log("Telegram не настроен — заполните токен и chat id на вкладке «Будильник»")
            return
        log = self.catch_log

        def job():
            from .tgcontrol import CATCH_BUTTONS
            data, kind = log.render()
            self.tg.send_media(data, kind, log.summary_text(), buttons=CATCH_BUTTONS if log.total else None)
        threading.Thread(target=job, daemon=True).start()

    def _clear_catches(self):
        self.catch_log.reset()
        self.log("Журнал улова очищен")
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
            "runtime": runtime // 10 * 10,  # с точностью до 10 с, чтобы не слать статус каждую секунду
            "blackout": self.blackout.active,
            "alarm": alarms.reason if alarms.active else None,
            "fish": self.catch_log.status(),
            "fish_log": bool(self.cfg.get("catch_log_enabled")),
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
        if cmd == "catches":
            self._send_catches()
            return
        if cmd == "reset_ask":
            from .tgcontrol import RESET_CONFIRM
            total = self.catch_log.total
            if not total:
                self.tg.send("🐟 Список улова и так пуст", buttons=False)
            else:
                self.tg.send(f"Очистить список улова? Сейчас в нём {total} рыб", buttons=RESET_CONFIRM)
            return
        if cmd == "reset_cancel":
            self.tg.send("Список улова не тронут", buttons=False)
            return
        if cmd == "reset_catches":
            self.catch_log.reset()
            self.log("Журнал улова очищен из Telegram")
            self.tg.send("🗑 Список улова очищен", buttons=False)
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
            self.tg.send(status_text(self.engine, self.blackout.active) + "\n" + self.remote.diagnostics())

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
            self.log("HUD включён" if self.cfg["hud_enabled"] else "HUD выключен")
            self._tick_hud(once=True)
        elif name == self.cfg["hotkey_start_stop"]:
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
                    self._status_color = color
                    self.remote.poke()
                    self.status.configure(text=f"  ●  {text.upper()}  ", fg_color=STATUS_COLORS.get(color, color))
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
            self.start_btn.configure(text="■  СТОП ", fg_color=RED, hover_color=RED_HOVER)
            if self.cfg.get("blackout_enabled") and not self.blackout.active:
                delay = max(float(self.cfg.get("blackout_delay") or 0), 0)
                self._blackout_job = self.root.after(int(delay * 1000), self._blackout_on)
        else:
            self.start_btn.configure(text="▶  СТАРТ ", fg_color=GREEN, hover_color=GREEN_HOVER)
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
        self.stat_values["catches"].configure(text=italic_pad(catches))
        self.stat_values["casts"].configure(text=italic_pad(s.get("casts", 0)))
        self.stat_values["runtime"].configure(text=italic_pad(f"{h}:{rem // 60:02d}:{rem % 60:02d}"))
        rate = catches * 3600 / runtime if runtime >= 60 else 0
        self.stat_values["rate"].configure(text=italic_pad(f"{rate:.0f}"))

    def run(self):
        self.root.mainloop()
