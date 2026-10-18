"""Журнал улова: какая рыба поймана — с иконкой, названием и цветом редкости — и список для Telegram.

После каждой мини-игры движок снимает область уведомления о рыбе. Рыбу узнаём по каталогу
(macro/fishdb.py): иконка, редкость, цена. Если в каталоге её нет — пробуем прочитать
название (Windows OCR), иконкой служит сам снимок, а одинаковые снимки группируются.

Список рисуется в стиле макроса: название окрашено по редкости, у легендарных рыб
название и рамка переливаются радугой — тогда в Telegram уходит анимация (GIF).
"""
import colorsys
import io
import re
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

from . import fishdb, ocr, theme

RARITY_EMOJI = {"legendary": "🌈", "epic": "🟣", "rare": "🔵", "common": "⚪", None: "🆕"}
# Слова уведомления, которые не являются названием рыбы («Fish» оставляем — бывает в названиях)
NOISE = re.compile(r"\b(you|caught|got|obtained|a|an|the|new|fished|x\d+|\+\d+)\b", re.IGNORECASE)


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


def fmt_peli(n):
    return f"{n:,}".replace(",", " ")


class Catch:
    """Сколько поймано рыбы одного вида. Данные о рыбе (иконка, редкость, цена) — из каталога."""

    def __init__(self, fish_id):
        self.fish_id = fish_id
        self.count = 0
        self.first = self.last = time.time()
        self.new = False          # рыба впервые попала в каталог при этой поимке

    @property
    def fish(self):
        return fishdb.by_id(self.fish_id) or {"id": self.fish_id, "name": "Unknown Fish", "rarity": None, "price": 0}

    @property
    def rarity(self):
        return self.fish.get("rarity")

    @property
    def price(self):
        return self.fish.get("price") or 0

    @property
    def icon(self):
        try:
            return fishdb.icon(self.fish_id)
        except OSError:
            return Image.new("RGBA", (8, 8), (79, 79, 79, 255))


class CatchLog:
    def __init__(self, cfg=None, log=print):
        self.cfg = cfg if cfg is not None else {}
        self.log = log
        self.items = []
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.items.clear()

    @property
    def total(self):
        return sum(c.count for c in self.items)

    def name_of(self, c):
        return fishdb.display_name(c.fish, self.cfg)

    # ---------- запись ----------
    def identify(self, rgb, text=None):
        """(рыба, новая ли) по снимку уведомления. Новую рыбу добавляет в каталог."""
        if text is None:
            text = ocr.read_text(np.ascontiguousarray(np.asarray(rgb)[:, :, :3]))
        name = clean_name(text)
        # 1) По названию из уведомления — надёжнее всего
        fish = fishdb.find_by_name(name, self.cfg) if name else None
        if fish is None:
            # 2) По иконке. Если у похожей рыбы уже есть другое прочитанное название — это другая рыба
            found, _score = fishdb.match(rgb)
            if found:
                known = fishdb.learned_name(found["id"])
                if name and known and known.lower() != name.lower():
                    found = None
                elif name and not known:
                    fishdb.learn_name(found["id"], name)
            fish = found
        if fish is not None:
            return fish, False
        # 3) Такой рыбы ещё нет — заносим в каталог: иконка из уведомления, название оттуда же
        fish = fishdb.add_fish(fishdb.extract_icon(rgb), name or None)
        if name:
            fishdb.learn_name(fish["id"], name)
        return fish, True

    def add(self, rgb, text=None):
        """Записать улов по снимку области уведомления. Возвращает Catch."""
        fish, new = self.identify(rgb, text)
        with self._lock:
            match = next((c for c in self.items if c.fish_id == fish["id"]), None)
            if match is None:
                match = Catch(fish["id"])
                self.items.append(match)
            match.count += 1
            match.last = time.time()
            match.new = new
            return match

    def _sorted(self):
        with self._lock:
            rows = list(self.items)
        return sorted(rows, key=lambda c: (fishdb.RARITY_ORDER.get(c.rarity, 4), -c.count))

    # ---------- текст ----------
    def summary_text(self, limit=30):
        rows = self._sorted()
        if not rows:
            return "🐟 Улов пока пуст"
        total = sum(c.count for c in rows)
        value = sum(c.count * c.price for c in rows)
        lines = [f"🐟 Улов: {total} {plural(total, ('рыба', 'рыбы', 'рыб'))}, "
                 f"{len(rows)} {plural(len(rows), ('вид', 'вида', 'видов'))}"]
        if value:
            lines.append(f"💰 ≈ {fmt_peli(value)} Peli")
        lines += [f"{RARITY_EMOJI.get(c.rarity, '🆕')} {self.name_of(c)} × {c.count}" for c in rows[:limit]]
        if len(rows) > limit:
            lines.append(f"…и ещё {len(rows) - limit}")
        return "\n".join(lines)

    def status(self, limit=12):
        """Список для мини-приложения: [{"id", "name", "count", "rarity"}]."""
        return [{"id": c.fish_id, "name": self.name_of(c), "count": c.count, "rarity": c.rarity}
                for c in self._sorted()[:limit]]

    # ---------- картинка ----------
    def render(self, rows=None, limit=12, frames=12):
        """(байты, "gif"|"png"). GIF — если есть легендарные: их название и рамка переливаются."""
        rows = (rows if rows is not None else self._sorted())[:limit]
        animated = frames > 1 and any(c.rarity == "legendary" for c in rows)
        if not animated:
            buf = io.BytesIO()
            self._draw(rows, 0.0).save(buf, "PNG")
            return buf.getvalue(), "png"
        images = [self._draw(rows, i / frames).convert("P", palette=Image.ADAPTIVE, colors=255)
                  for i in range(frames)]
        buf = io.BytesIO()
        images[0].save(buf, "GIF", save_all=True, append_images=images[1:], duration=90, loop=0)
        return buf.getvalue(), "gif"

    def _draw(self, rows, phase):
        W, ROW, HEAD, PAD = 720, 84, 96, 18
        H = HEAD + max(len(rows), 1) * ROW + PAD
        img = Image.new("RGBA", (W, H), theme._hex(theme.BG))
        d = ImageDraw.Draw(img)
        # Шапка: косая полоса и заголовок, как в окне макроса
        d.polygon([(0, 14), (W * 0.56, 14), (W * 0.52, 70), (0, 70)], fill=(26, 155, 224, 255))
        d.polygon([(0, 70), (W * 0.52, 70), (W * 0.517, 75), (0, 75)], fill=theme._hex(theme.HILITE))
        theme._slanted_text(img, (20, 16), "УЛОВ", theme._font(40), (255, 255, 255, 255))
        total = sum(c.count for c in rows)
        value = sum(c.count * c.price for c in rows)
        kinds = f"{len(rows)} {plural(len(rows), ('вид', 'вида', 'видов'))}"
        theme._slanted_text(img, (W * 0.57, 16), f"{total} шт · {kinds}", theme._font(22), theme._hex(theme.HILITE))
        if value:
            theme._slanted_text(img, (W * 0.57, 44), f"≈ {fmt_peli(value)} Peli", theme._font(20),
                                (255, 214, 90, 255))
        name_font, small_font, count_font = theme._font(25), theme._font(15), theme._font(30)
        if not rows:
            d.text((PAD, HEAD + 20), "Пока ничего не поймано", font=name_font, fill=theme._hex(theme.MUTED))
        for i, c in enumerate(rows):
            y = HEAD + i * ROW
            d.polygon([(PAD + 10, y), (W - PAD, y), (W - PAD - 10, y + ROW - 8), (PAD, y + ROW - 8)],
                      fill=theme._hex(theme.CARD), outline=theme._hex(theme.CARD_BORDER))
            # Квадрат иконки с рамкой цвета редкости — как в инвентаре игры
            box = (PAD + 20, y + 6, PAD + 20 + ROW - 20, y + ROW - 14)
            d.rectangle(box, fill=(79, 79, 79, 255))
            self._frame(img, box, c.rarity, phase)
            icon = c.icon.copy().convert("RGBA")
            icon.thumbnail((box[2] - box[0] - 6, box[3] - box[1] - 6))
            img.alpha_composite(icon, (box[0] + (box[2] - box[0] - icon.width) // 2,
                                       box[1] + (box[3] - box[1] - icon.height) // 2))
            tx = box[2] + 18
            self._rarity_text(img, (tx, y + 10), self.name_of(c), name_font, c.rarity, phase)
            title = fishdb.rarity_info(c.rarity)["title"]
            sub = title.upper() + (f"  ·  {fmt_peli(c.price)} Peli" if c.price else "")
            d.text((tx, y + 44), sub, font=small_font, fill=theme._hex(theme.MUTED))
            cnt = f"× {c.count}"
            tw = d.textlength(cnt, font=count_font)
            d.text((W - PAD - 30 - tw, y + 20), cnt, font=count_font, fill=theme._hex(theme.HILITE))
        return img.convert("RGB")

    @staticmethod
    def _rainbow(width, height, phase):
        hues = (np.linspace(0, 0.9, max(width, 1), endpoint=False) - phase) % 1.0
        row = (np.array([colorsys.hsv_to_rgb(h, 0.75, 1.0) for h in hues]) * 255).astype(np.uint8)
        return Image.fromarray(np.repeat(row[None, :, :], max(height, 1), axis=0), "RGB")

    def _rarity_text(self, img, xy, text, font, rarity, phase):
        color = fishdb.rarity_info(rarity)["color"]
        if color != "rainbow":
            ImageDraw.Draw(img).text(xy, text, font=font, fill=theme._hex(color))
            return
        bbox = font.getbbox(text)
        mask = Image.new("L", (bbox[2] + 4, bbox[3] + 4), 0)
        ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
        img.paste(self._rainbow(mask.width, mask.height, phase), (int(xy[0]), int(xy[1])), mask)

    def _frame(self, img, box, rarity, phase):
        color = fishdb.rarity_info(rarity)["color"]
        if color != "rainbow":
            ImageDraw.Draw(img).rectangle(box, outline=theme._hex(color), width=3)
            return
        x0, y0, x1, y1 = box
        mask = Image.new("L", (x1 - x0 + 1, y1 - y0 + 1), 0)
        ImageDraw.Draw(mask).rectangle([0, 0, x1 - x0, y1 - y0], outline=255, width=3)
        img.paste(self._rainbow(mask.width, mask.height, phase), (x0, y0), mask)
