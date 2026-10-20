"""Тема FishPilot: глубокий синий и циан. Палитра окна и шрифты для картинок (список улова)."""
import os

from PIL import Image, ImageDraw, ImageFont

# Палитра
BG = "#06122b"
SIDEBAR = "#040c1f"
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
