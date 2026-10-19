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


# «New Item <Fangfish>»: название в угловых скобках. OCR иногда путает скобки — берём похожие
BRACKETED = re.compile(r"[<«‹(\[{]\s*([A-Za-z][A-Za-z0-9' \-]{1,40}?)\s*[>»›)\]}]")
NEW_ITEM = re.compile(r"new\s*item\s*[:\-]?\s*[<«‹(\[{]?\s*([A-Za-z][A-Za-z0-9' \-]{1,40}?)\s*(?:[>»›)\]}]|$)",
                      re.IGNORECASE)


def parse_name(text):
    """Название рыбы из текста уведомления или None, если это не уведомление о предмете."""
    for line in (text or "").splitlines():
        m = BRACKETED.search(line) or NEW_ITEM.search(line)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" -'")
            if len(name) >= 2:
                return name
    return None


def text_rarity(rgb, rect=None):
    """Редкость по цвету букв названия: голубой — редкая, фиолетовый — эпическая,
    много цветов — легендарная, белый — обычная. None — цвет не узнали."""
    a = np.asarray(rgb)[:, :, :3]
    if rect:
        x, y, w, h = (int(round(v)) for v in rect)
        a = a[max(y, 0):max(y + h, 0), max(x, 0):max(x + w, 0)]
    if a.size == 0:
        return None
    img = a.astype(np.float32) / 255.0
    mx2 = img.max(axis=2)
    # Буквы в игре с тёмной обводкой: берём только яркие пиксели рядом с тёмным контуром —
    # так голубая вода и небо за текстом не путают цвет
    dark = mx2 < 0.2
    near = dark.copy()
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            near |= np.roll(np.roll(dark, dy, axis=0), dx, axis=1)
    glyph = near & ~dark & (mx2 > 0.6)
    # …и не цвета фона: снаружи обводки сразу начинается фон (самый частый цвет по краю рамки)
    edge = np.concatenate([img[0], img[-1], img[:, 0], img[:, -1]])
    q = (edge * 15).astype(int)
    keys, counts = np.unique(q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2], return_counts=True)
    k = keys[counts.argmax()]
    bg = np.array([k // 256, (k // 16) % 16, k % 16]) / 15.0
    glyph &= np.abs(img - bg).max(axis=2) > 0.16
    f = img[glyph]
    mx, mn = f.max(axis=1), f.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    bright = np.ones(len(f), bool)
    colored = sat > 0.35
    if bright.sum() < 8:
        return None
    if colored.sum() < bright.sum() * 0.25:
        return "common"                       # белые буквы
    c = f[colored]
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    cmax, cmin = c.max(axis=1), c.min(axis=1)
    d = np.maximum(cmax - cmin, 1e-6)
    hue = np.where(cmax == r, ((g - b) / d) % 6, np.where(cmax == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    hist = np.histogram(hue, bins=12, range=(0, 360))[0] / len(hue)
    if (hist >= 0.08).sum() >= 4:
        return "legendary"                    # радуга
    main = (np.argmax(hist) + 0.5) * 30
    if 160 <= main <= 215:
        return "rare"
    if 245 <= main <= 320:
        return "epic"
    return None


def read_notice(rgb, lines):
    """(название, редкость) из уведомления «New Item <…>» или None."""
    for line in lines:
        name = parse_name(line["text"])
        if not name:
            continue
        # Рамка слов, из которых состоит название, — по ним меряем цвет
        parts = set(re.sub(r"[^a-z0-9 ]", " ", name.lower()).split())
        rects = [r for t, r in line.get("words", ())
                 if set(re.sub(r"[^a-z0-9 ]", " ", t.lower()).split()) & parts]
        rect = None
        if rects:
            x0 = min(r[0] for r in rects)
            y0 = min(r[1] for r in rects)
            x1 = max(r[0] + r[2] for r in rects)
            y1 = max(r[1] + r[3] for r in rects)
            rect = (x0, y0, x1 - x0, y1 - y0)
        return name, (text_rarity(rgb, rect) if rect else None)
    return None


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
        self.last_texts = []      # что OCR прочитал в последних снимках — для диагностики
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
    def add(self, rgb, text=None, lines=None):
        """Записать улов по снимку уведомления «New Item <рыба>». Возвращает Catch или None,
        если на снимке нет такого уведомления (другие надписи игры не считаются)."""
        if lines is None:
            lines = [{"text": text, "words": []}] if text is not None else ocr.read_lines(
                np.ascontiguousarray(np.asarray(rgb)[:, :, :3]))
        self.last_texts = (self.last_texts + [" | ".join(line["text"] for line in lines if line["text"])])[-12:]
        notice = read_notice(rgb, lines)
        if not notice:
            return None
        name, rarity = notice
        fish = fishdb.find_by_name(name, self.cfg)
        new = False
        if fish is None:
            # Такой рыбы ещё нет — в каталог: название из уведомления, редкость по цвету текста,
            # вместо иконки — силуэт цвета редкости (иконку можно привязать в каталоге)
            fish = fishdb.add_fish(fishdb.placeholder_icon(rarity), name, rarity=rarity)
            new = True
        elif fish["id"].startswith("u") and not fish.get("rarity") and rarity:
            fishdb.set_rarity(fish["id"], rarity)
        with self._lock:
            match = next((c for c in self.items if c.fish_id == fish["id"]), None)
            if match is None:
                match = Catch(fish["id"])
                self.items.append(match)
            match.count += 1
            match.last = time.time()
            match.new = new
            return match

    def add_frames(self, frames):
        """Несколько снимков подряд (уведомление появляется и гаснет) — берём первый с рыбой."""
        for rgb in frames:
            c = self.add(rgb)
            if c is not None:
                return c
        return None

    def remap(self, old_id, new_id):
        """Рыбу связали с иконкой из каталога — переносим её улов на новую запись."""
        with self._lock:
            old = next((c for c in self.items if c.fish_id == old_id), None)
            if old is None:
                return
            target = next((c for c in self.items if c.fish_id == new_id), None)
            if target is None:
                old.fish_id = new_id
            else:
                target.count += old.count
                self.items.remove(old)

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
