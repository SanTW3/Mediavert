"""Гайд при первом запуске: по шагам — что настроить в игре, в макросе и в Telegram.

Открывается сам один раз (config["guide_shown"]), потом — кнопкой «Гайд» в шапке.
На шагах есть кнопки и поля прямо из настроек, чтобы не искать их по вкладкам.
"""
import tkinter as tk

import customtkinter as ctk

from .theme import ACCENT, ACCENT_HOVER, BG, CARD, CARD_BORDER, FIELD, GREEN, GREEN_HOVER, HILITE, MUTED, TEXT

PAGES = [
    {
        "title": "Привет! Это макрос рыбалки GPO",
        "text": (
            "Он сам забрасывает удочку, проходит мини-игру, считает улов и звонит на телефон, "
            "если что-то пошло не так.\n\n"
            "Этот гайд — 6 коротких шагов. Его всегда можно открыть снова кнопкой «Гайд» в шапке окна.\n\n"
            "Все настройки сохраняются сами."
        ),
    },
    {
        "title": "1. Подготовка в игре",
        "text": (
            "• Встаньте у воды с удочкой.\n"
            "• Положите удочку в слот на панели и укажите его номер: «Основное → Слот удочки».\n"
            "• «Любой другой слот» — любой другой предмет: макрос жмёт его перед удочкой, "
            "чтобы удочка точно оказалась в руках.\n"
            "• Если покупаете наживку автоматически — встаньте рядом с продавцом и настройте "
            "точки во вкладке «Подготовка»."
        ),
    },
    {
        "title": "2. Мини-игра и точка воды",
        "text": (
            "• Поймайте одну рыбу вручную. Пока идёт мини-игра, нажмите F2 и обведите рамкой "
            "обе полосы: синюю с тёмно-серой и зелёную справа.\n"
            "• Рамку можно тянуть за углы и края, Enter — сохранить, Esc — отмена.\n"
            "• Затем укажите точку на воде, куда забрасывать удочку.\n"
            "• «Окно отладки» показывает, что видит макрос: пурпурная рамка — ваша полоса, "
            "красная линия — рыба."
        ),
        "buttons": [("Указать точку воды", "water"), ("Окно отладки", "preview")],
    },
    {
        "title": "3. Журнал улова",
        "text": (
            "После поимки игра пишет над персонажем «New Item <Название>». Макрос читает эту надпись "
            "(встроенное распознавание Windows) и ведёт список улова с цветом редкости.\n\n"
            "• Работает сразу: по умолчанию смотрит в центр окна Roblox.\n"
            "• Если надпись у вас в другом месте — выберите свою область.\n"
            "• «Проверить сейчас» (пока надпись на экране) покажет в журнале, что прочитано."
        ),
        "buttons": [("Выбрать область уведомления", "catch_area"), ("Проверить сейчас", "catch_test")],
    },
    {
        "title": "4. Telegram-бот",
        "text": (
            "1) Напишите @BotFather → /newbot → придумайте имя. Он пришлёт токен — вставьте ниже.\n"
            "   У каждого свой бот: один бот на двух ПК работать не будет.\n"
            "2) Найдите своего бота и нажмите «Start» — без этого бот не может вам написать.\n"
            "3) Нажмите «Узнать chat id» — программа сама вставит ваш id.\n"
            "4) Нажмите «Проверить Telegram» — бот пришлёт сообщение, а в журнале будет видно, что не так."
        ),
        "fields": [("Токен бота", "telegram_token", True), ("Chat id", "telegram_chat_id", False)],
        "buttons": [("Узнать chat id", "tg_chat"), ("Проверить Telegram", "tg_check")],
    },
    {
        "title": "5. Что умеет бот и мини-приложение",
        "text": (
            "• Слева от поля ввода в чате появится кнопка «Панель» — живая статистика, старт/стоп, "
            "экран, улов. Чтобы она работала, один раз включите GitHub Pages (см. README).\n"
            "• /menu — кнопки: 🔆 экран, 📸 скриншот, 📊 статус, 🐟 улов, ⏹ стоп.\n"
            "• /catches — список улова с иконками; кнопка «🗑 Очистить список» под ним (или /reset_catches).\n"
            "• Будильник: бот пишет, если нет поклёвок, рыбы, окна Roblox или макрос упал. "
            "Для настоящего звонка добавьте ntfy или Pushover во вкладке «Всё остальное → Будильник»."
        ),
        "buttons": [("Отправить панель в Telegram", "panel")],
    },
    {
        "title": "6. Горячие клавиши и старт",
        "text": (
            "F1 — старт / стоп\n"
            "F2 — выбрать область мини-игры\n"
            "F3 — выход\n"
            "F4 — погасить / включить экран\n\n"
            "Чёрный экран во время рыбалки включается в «Основное». Любая клавиша или кнопка мыши "
            "возвращает экран, а макрос под ним продолжает ловить.\n\n"
            "Готово! Перейдите в игру и нажмите F1."
        ),
    },
]


class Guide(ctk.CTkToplevel):
    def __init__(self, app):
        super().__init__(app.root, fg_color=BG)
        self.app = app
        self.title("Гайд — GPO макрос рыбалки")
        self.geometry("560x560")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.page = 0
        self.after(50, self.lift)

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=20, pady=(16, 6))
        self.step = tk.Label(head, bg=BG, fg=HILITE, font=(app.display, 11, "bold", "italic"))
        self.step.pack(anchor="w")
        self.title_lbl = tk.Label(head, bg=BG, fg=TEXT, font=(app.display, 18, "bold", "italic"),
                                  anchor="w", justify="left", wraplength=500)
        self.title_lbl.pack(anchor="w", fill="x")
        tk.Frame(self, bg=HILITE, height=3).pack(fill="x", padx=20, pady=(2, 10))

        self.card = ctk.CTkFrame(self, fg_color=CARD, border_color=CARD_BORDER, border_width=1, corner_radius=4)
        self.card.pack(fill="both", expand=True, padx=20)
        self.body = tk.Frame(self.card, bg=CARD)
        self.body.pack(fill="both", expand=True, padx=16, pady=14)

        nav = tk.Frame(self, bg=BG)
        nav.pack(fill="x", padx=20, pady=14)
        self.back = ctk.CTkButton(nav, text="← Назад", width=110, fg_color=FIELD, hover_color=CARD_BORDER,
                                  command=lambda: self.go(-1))
        self.back.pack(side="left")
        self.dots = tk.Label(nav, bg=BG, fg=MUTED, font=("Segoe UI", 12))
        self.dots.pack(side="left", expand=True)
        self.next = ctk.CTkButton(nav, text="Далее →", width=130, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                  command=lambda: self.go(1))
        self.next.pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.finish)
        self.render()

    def go(self, delta):
        if self.page + delta >= len(PAGES):
            self.finish()
            return
        self.page = max(0, self.page + delta)
        self.render()

    def render(self):
        page = PAGES[self.page]
        for child in self.body.winfo_children():
            child.destroy()
        self.step.configure(text=f"ШАГ {self.page} ИЗ {len(PAGES) - 1}" if self.page else "ГАЙД")
        self.title_lbl.configure(text=page["title"])
        tk.Label(self.body, text=page["text"], bg=CARD, fg=TEXT, font=("Segoe UI", 10), justify="left",
                 anchor="nw", wraplength=480).pack(anchor="w", fill="x")
        for label, key, secret in page.get("fields", ()):
            row = tk.Frame(self.body, bg=CARD)
            row.pack(fill="x", pady=(10, 0))
            tk.Label(row, text=label, bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
            ctk.CTkEntry(row, textvariable=self.app.var(key, str), fg_color=FIELD, border_color=CARD_BORDER,
                         show="•" if secret else "").pack(fill="x")
        if page.get("buttons"):
            row = tk.Frame(self.body, bg=CARD)
            row.pack(fill="x", pady=(14, 0))
            for text, action in page["buttons"]:
                ctk.CTkButton(row, text=text, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                              command=lambda a=action: self.do(a)).pack(side="left", padx=(0, 8))
        last = self.page == len(PAGES) - 1
        self.back.configure(state="normal" if self.page else "disabled")
        self.next.configure(text="Готово ✓" if last else "Далее →",
                            fg_color=GREEN if last else ACCENT, hover_color=GREEN_HOVER if last else ACCENT_HOVER)
        self.dots.configure(text=" ".join("●" if i == self.page else "○" for i in range(len(PAGES))))

    def do(self, action):
        app = self.app
        if action == "water":
            app.pick_point("water_point", "Кликните по воде, куда забрасывать удочку")
        elif action == "preview":
            app._open_preview()
        elif action == "catch_area":
            app._pick_catch_area()
        elif action == "catch_test":
            app._test_catch()
        elif action == "alarm_test":
            app._test_alarm()
        elif action == "tg_chat":
            app._find_chat_id()
        elif action == "tg_check":
            app._check_tg()
        elif action == "panel":
            app._on_tg("panel")

    def finish(self):
        self.app.cfg["guide_shown"] = True
        self.app._save()
        self.destroy()
