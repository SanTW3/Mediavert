"""Морская тема в духе Persona 3 Reload: глубокий синий, циан, наклонные полосы и курсив.

Всё «красивое» (градиент, лучи, волны, пузыри) рисуется ОДИН раз при запуске в картинку
шапки через PIL — никаких анимаций в окне, поэтому нагрузка на ПК не меняется.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

# Палитра
BG = "#06122b"
CARD = "#0b1f45"
CARD_BORDER = "#16407a"
FIELD = "#0a1838"
TEXT = "#eaf6ff"
MUTED = "#7fa6d1"
ACCENT = "#1a9be0"
ACCENT_HOVER = "#1480bd"
HILITE = "#5ee0ff"
GREEN = "#14b37d"
GREEN_HOVER = "#0f9468"
RED = "#e0405a"
RED_HOVER = "#bf3149"
ORANGE = "#f2a33a"
WARN_BG = "#3a0f2a"

# Шрифт заголовков: узкий Bahnschrift есть в Windows 10/11; иначе — обычный Segoe UI
DISPLAY_CANDIDATES = ("Bahnschrift SemiBold Condensed", "Bahnschrift Condensed", "Bahnschrift")


def display_family(root):
    """Первое доступное семейство для заголовков (вызывать после создания окна)."""
    import tkinter.font as tkfont
    families = set(tkfont.families(root))
    for name in DISPLAY_CANDIDATES:
        if name in families:
            return name
    return "Segoe UI"


def _hex(c, alpha=255):
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def _font(size):
    """Жирный узкий шрифт для картинки шапки, с запасными вариантами."""
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    candidates = [
        (os.path.join(fonts_dir, "bahnschrift.ttf"), "Bold Condensed"),
        (os.path.join(fonts_dir, "segoeuib.ttf"), None),
        (os.path.join(fonts_dir, "arialbd.ttf"), None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None),
        ("DejaVuSans-Bold.ttf", None),
    ]
    for path, variation in candidates:
        try:
            font = ImageFont.truetype(path, size)
        except OSError:
            continue
        if variation:
            try:
                font.set_variation_by_name(variation)
            except Exception:
                pass
        return font
    return ImageFont.load_default()


def _slanted_text(img, xy, text, font, fill, slant=0.22):
    """Курсив «как в P3R»: рисуем текст на отдельном слое и сдвигаем верх вправо."""
    bbox = font.getbbox(text)
    w, h = bbox[2] + int(bbox[3] * slant) + 8, bbox[3] + 8
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((4, 4), text, font=font, fill=fill)
    layer = layer.transform(layer.size, Image.AFFINE, (1, slant, -slant * h, 0, 1, 0), resample=Image.BICUBIC)
    img.alpha_composite(layer, (int(xy[0]), int(xy[1])))
    return w


def render_banner(width=600, height=110, scale=2):
    """Картинка шапки: глубина моря, лучи света, косая полоса P3R, волны и пузыри."""
    W, H = width * scale, height * scale
    top, bottom = _hex("#020b1f"), _hex("#0b3a86")
    img = Image.new("RGBA", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))

    fx = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(fx)
    # Лучи света сквозь воду
    for x0, w0, a in ((0.55, 0.06, 28), (0.68, 0.035, 22), (0.80, 0.08, 18), (0.93, 0.03, 26)):
        x = x0 * W
        d.polygon([(x, 0), (x + w0 * W, 0), (x + w0 * W - 0.25 * W, H), (x - 0.25 * W, H)],
                  fill=(120, 220, 255, a))
    # Фирменная косая полоса: широкая полупрозрачная + тонкая яркая
    d.polygon([(-10, 0.16 * H), (0.64 * W, 0.16 * H), (0.58 * W, 0.80 * H), (-10, 0.80 * H)],
              fill=(26, 155, 224, 70))
    d.polygon([(-10, 0.80 * H), (0.58 * W, 0.80 * H), (0.575 * W, 0.84 * H), (-10, 0.84 * H)],
              fill=(94, 224, 255, 230))
    # Волны
    for k, (yy, amp, a) in enumerate(((0.90, 0.025, 70), (0.95, 0.02, 45))):
        pts = [(x, yy * H + amp * H * math.sin(x / (W * 0.07) + k * 1.7)) for x in range(0, W + 8, 8)]
        d.line(pts, fill=(200, 240, 255, a), width=max(2, scale * 2))
    # Пузыри
    for bx, by, r in ((0.60, 0.62, 7), (0.64, 0.36, 4), (0.69, 0.70, 3), (0.57, 0.28, 3), (0.71, 0.48, 5)):
        cx, cy, rr = bx * W, by * H, r * scale
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(170, 230, 255, 120), width=scale)
        d.ellipse([cx - rr * 0.45, cy - rr * 0.6, cx - rr * 0.1, cy - rr * 0.25], fill=(220, 245, 255, 120))
    img.alpha_composite(fx)

    # Заголовок
    big = _font(int(40 * scale))
    small = _font(int(15 * scale))
    x = _slanted_text(img, (16 * scale, 14 * scale), "GPO", big, (255, 255, 255, 255))
    _slanted_text(img, (16 * scale + x + 4 * scale, 22 * scale), "FISHING", small, _hex(HILITE))
    _slanted_text(img, (16 * scale + x + 4 * scale, 22 * scale + small.size + 2 * scale), "MACRO", small,
                  (255, 255, 255, 230))
    return img
