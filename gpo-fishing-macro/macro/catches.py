"""Журнал улова: что поймали, с иконкой из игры и названием, и картинка-список для Telegram.

После каждой мини-игры движок снимает область, где игра показывает уведомление о рыбе
(её выбирают в настройках). Название читает Windows OCR, иконкой служит сам снимок.
Если название прочитать не удалось, одинаковые рыбы группируются по похожести снимка.
"""
import io
import re
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

from . import ocr, theme

UNKNOWN = "Неизвестная рыба"
# Слова уведомления, которые не являются названием рыбы
NOISE = re.compile(r"\b(you|caught|got|obtained|a|an|the|new|fish(ed)?|x\d+|\+\d+)\b", re.IGNORECASE)


def plural(n, forms):
    """plural(4, ("вид", "вида", "видов")) → «вида»."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def clean_name(text):
    """«You caught a Golden Carp! x1» → «Golden Carp»."""
    text = re.sub(r"[^A-Za-zА-Яа-яЁё' ]+", " ", text or "")
    text = NOISE.sub(" ", text)
    words = [w for w in text.split() if len(w) > 1]
    name = " ".join(words[:4]).strip()
    return name.title() if name else ""


def ahash(rgb, size=12):
    """Средний хеш снимка: похожие картинки дают близкие хеши."""
    img = Image.fromarray(rgb).convert("L").resize((size, size), Image.BILINEAR)
    a = np.asarray(img, dtype=np.float32)
    return (a > a.mean()).flatten()


class Catch:
    def __init__(self, name, icon, hash_):
        self.name = name
        self.icon = icon          # PIL.Image — снимок уведомления из игры
        self.hash = hash_
        self.count = 0
        self.first = self.last = time.time()


class CatchLog:
    def __init__(self, log=print):
        self.log = log
        self.items = []           # list[Catch], порядок первой поимки
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.items.clear()

    @property
    def total(self):
        return sum(c.count for c in self.items)

    def add(self, rgb, text=None):
        """Записать улов по снимку области уведомления. Возвращает Catch."""
        if text is None:
            text = ocr.read_text(rgb)
        name = clean_name(text)
        h = ahash(rgb)
        with self._lock:
            match = None
            for c in self.items:
                same_name = name and c.name == name
                same_look = not name and c.name == UNKNOWN and np.count_nonzero(c.hash != h) <= 14
                if same_name or same_look:
                    match = c
                    break
            if match is None:
                match = Catch(name or UNKNOWN, Image.fromarray(rgb), h)
                self.items.append(match)
            match.count += 1
            match.last = time.time()
            return match

    def summary_text(self, limit=30):
        with self._lock:
            rows = sorted(self.items, key=lambda c: -c.count)
        if not rows:
            return "🐟 Улов пока пуст"
        total = sum(c.count for c in rows)
        lines = [f"🐟 Улов: {total} {plural(total, ('рыба', 'рыбы', 'рыб'))}, "
                 f"{len(rows)} {plural(len(rows), ('вид', 'вида', 'видов'))}"]
        lines += [f"• {c.name} × {c.count}" for c in rows[:limit]]
        if len(rows) > limit:
            lines.append(f"…и ещё {len(rows) - limit}")
        return "\n".join(lines)

    def render_png(self, limit=12):
        """Картинка-список в стиле макроса: иконка из игры, название, количество."""
        with self._lock:
            rows = sorted(self.items, key=lambda c: -c.count)[:limit]
        W, ROW, HEAD, PAD = 720, 76, 92, 18
        H = HEAD + max(len(rows), 1) * ROW + PAD
        img = Image.new("RGBA", (W, H), theme._hex(theme.BG))
        d = ImageDraw.Draw(img)
        # Шапка: косая полоса и заголовок, как в окне макроса
        d.polygon([(0, 14), (W * 0.62, 14), (W * 0.58, 70), (0, 70)], fill=theme._hex(theme.ACCENT, 90))
        d.polygon([(0, 70), (W * 0.58, 70), (W * 0.577, 75), (0, 75)], fill=theme._hex(theme.HILITE))
        theme._slanted_text(img, (20, 16), "УЛОВ", theme._font(40), (255, 255, 255, 255))
        total = sum(c.count for c in rows)
        kinds = f"{len(rows)} {plural(len(rows), ('вид', 'вида', 'видов'))}"
        theme._slanted_text(img, (W * 0.64, 30), f"{total} шт · {kinds}", theme._font(22),
                            theme._hex(theme.HILITE))
        name_font, count_font = theme._font(24), theme._font(30)
        if not rows:
            d.text((PAD, HEAD + 20), "Пока ничего не поймано", font=name_font, fill=theme._hex(theme.MUTED))
        for i, c in enumerate(rows):
            y = HEAD + i * ROW
            # Карточка-параллелограмм
            d.polygon([(PAD + 10, y), (W - PAD, y), (W - PAD - 10, y + ROW - 8), (PAD, y + ROW - 8)],
                      fill=theme._hex(theme.CARD), outline=theme._hex(theme.CARD_BORDER))
            d.rectangle([PAD + 12, y, PAD + 70, y + 3], fill=theme._hex(theme.HILITE))
            icon = c.icon.copy()
            icon.thumbnail((150, ROW - 20))
            img.paste(icon.convert("RGBA"), (PAD + 18, y + (ROW - 8 - icon.height) // 2))
            d.text((PAD + 186, y + 20), c.name, font=name_font, fill=theme._hex(theme.TEXT))
            cnt = f"× {c.count}"
            tw = d.textlength(cnt, font=count_font)
            d.text((W - PAD - 30 - tw, y + 14), cnt, font=count_font, fill=theme._hex(theme.HILITE))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        return buf.getvalue()

    def status(self, limit=8):
        """Короткий список для мини-приложения: [{"name", "count"}]."""
        with self._lock:
            rows = sorted(self.items, key=lambda c: -c.count)[:limit]
        return [{"name": c.name, "count": c.count} for c in rows]
