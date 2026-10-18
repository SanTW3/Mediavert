"""Каталог рыб GPO: иконки, редкость (по цвету рамки) и цена продажи — и пополнение на ходу.

* Встроенные 12 рыб — иконки из инвентаря игры (assets/fish).
* Рыба, которой нет в каталоге, добавляется сама: иконка вырезается из уведомления
  о поимке, название читается оттуда же (Windows OCR, по-английски). Такие рыбы и
  прочитанные названия хранятся в папке fish_learned рядом с программой.
* Название, которое вписал пользователь (config["fish_names"]), важнее всех остальных.

Пойманную рыбу узнаём сначала по прочитанному названию, а если его нет —
по цветовому составу снимка (гистограмма цветов без фона): это не зависит
от размера иконки и фона вокруг неё.
"""
import json
import os
import sys
import threading

import numpy as np
from PIL import Image

RARITIES = {
    "common": {"title": "Обычная", "color": "#e8eef5"},
    "rare": {"title": "Редкая", "color": "#4fd8ff"},
    "epic": {"title": "Эпическая", "color": "#b48cff"},
    "legendary": {"title": "Легендарная", "color": "rainbow"},
}
NEW_RARITY = {"title": "Новая", "color": "#ffd65a"}

# Встроенные рыбы. Названий в инвентаре игры нет — это запасные имена по виду,
# пока игра не покажет настоящее в уведомлении о поимке. price — цена продажи одной рыбы (Peli)
BUILTIN = [
    {"id": "00", "name": "Fire Tail", "rarity": "rare", "price": 1004},
    {"id": "01", "name": "Toadstool Fish", "rarity": "rare", "price": 1057},
    {"id": "02", "name": "Blue Perch", "rarity": "common", "price": 162},
    {"id": "03", "name": "Candy Fish", "rarity": "common", "price": 38},
    {"id": "04", "name": "Spotted Caramel", "rarity": "legendary", "price": 11941},
    {"id": "05", "name": "Tiger Caramel", "rarity": "legendary", "price": 12840},
    {"id": "06", "name": "Pumpkin Angler", "rarity": "legendary", "price": 10583},
    {"id": "07", "name": "Ghost Shark", "rarity": "epic", "price": 14913},
    {"id": "08", "name": "Black Perch", "rarity": "rare", "price": 2703},
    {"id": "09", "name": "Fire Squid", "rarity": "rare", "price": 12318},
    {"id": "10", "name": "Tiger Fish", "rarity": "rare", "price": 2000},
    {"id": "11", "name": "Zebra Sickle", "rarity": "rare", "price": 2328},
]
FISH = BUILTIN  # совместимость
RARITY_ORDER = {"legendary": 0, "epic": 1, "rare": 2, "common": 3, None: 4}
MATCH_THRESHOLD = 0.40   # меньше — рыбы нет в каталоге
BINS = 6                 # уровней на канал → 216 корзин цвета

_lock = threading.RLock()
_store_dir = None
_learned = None          # {"names": {id: name}, "fish": [ {...} ]}
_icons = {}
_features = None


# ---------- хранилище ----------
def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def set_store_dir(path):
    """Папка для выученных рыб (для тестов). По умолчанию — fish_learned рядом с программой."""
    global _store_dir, _learned, _features
    with _lock:
        _store_dir = path
        _learned = None
        _features = None
        for k in [k for k in _icons if k.startswith("u")]:
            del _icons[k]


def store_dir():
    return _store_dir or os.path.join(_app_dir(), "fish_learned")


def _assets_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", "fish")


def _load():
    global _learned
    if _learned is None:
        try:
            with open(os.path.join(store_dir(), "catalog.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        _learned = {"names": dict(data.get("names") or {}), "fish": list(data.get("fish") or [])}
    return _learned


def _save():
    os.makedirs(store_dir(), exist_ok=True)
    path = os.path.join(store_dir(), "catalog.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(_load(), f, ensure_ascii=False, indent=2)
    os.replace(path + ".tmp", path)


# ---------- каталог ----------
def all_fish():
    with _lock:
        return BUILTIN + list(_load()["fish"])


def by_id(fish_id):
    for f in all_fish():
        if f["id"] == fish_id:
            return f
    return None


def icon_path(fish_id):
    if str(fish_id).startswith("u"):
        return os.path.join(store_dir(), f"{fish_id}.png")
    return os.path.join(_assets_dir(), f"{fish_id}.png")


def icon(fish_id):
    """PIL RGBA-иконка рыбы (фон прозрачный)."""
    with _lock:
        if fish_id not in _icons:
            _icons[fish_id] = Image.open(icon_path(fish_id)).convert("RGBA")
        return _icons[fish_id]


def display_name(fish, cfg=None):
    """Название: своё (из настроек) → прочитанное в игре → запасное."""
    if not fish:
        return "Unknown Fish"
    own = ((cfg or {}).get("fish_names") or {}).get(fish["id"])
    if own:
        return own
    with _lock:
        return _load()["names"].get(fish["id"]) or fish["name"]


def learned_name(fish_id):
    with _lock:
        return _load()["names"].get(fish_id)


def rarity_info(rarity):
    return RARITIES.get(rarity, NEW_RARITY)


def learn_name(fish_id, name):
    """Запомнить название, прочитанное из уведомления игры."""
    with _lock:
        data = _load()
        if name and data["names"].get(fish_id) != name:
            data["names"][fish_id] = name
            _save()


def set_rarity(fish_id, rarity):
    with _lock:
        for f in _load()["fish"]:
            if f["id"] == fish_id:
                f["rarity"] = rarity
                _save()
                return


def find_by_name(name, cfg=None):
    if not name:
        return None
    key = name.strip().lower()
    for f in all_fish():
        if display_name(f, cfg).strip().lower() == key:
            return f
    with _lock:
        names = _load()["names"]
    for fid, n in names.items():
        if n.strip().lower() == key:
            return by_id(fid)
    return None


def add_fish(icon_rgba, name=None, rarity=None):
    """Новая рыба из уведомления. Возвращает её запись."""
    global _features
    with _lock:
        data = _load()
        n = len(data["fish"]) + 1
        fid = f"u{n:03d}"
        while by_id(fid):
            n += 1
            fid = f"u{n:03d}"
        os.makedirs(store_dir(), exist_ok=True)
        icon_rgba.save(icon_path(fid))
        fish = {"id": fid, "name": name or f"Unknown Fish #{n}", "rarity": rarity, "price": 0, "placeholder": True}
        data["fish"].append(fish)
        _save()
        _icons[fid] = icon_rgba
        _features = None
        return fish


def link_to_builtin(fish_id, builtin_id):
    """Выученная рыба — это рыба со скрина: её название переходит к иконке из каталога."""
    global _features
    with _lock:
        data = _load()
        fish = next((f for f in data["fish"] if f["id"] == fish_id), None)
        if fish is None or not by_id(builtin_id):
            return
        data["names"][builtin_id] = data["names"].get(fish_id) or fish["name"]
        data["names"].pop(fish_id, None)
        data["fish"].remove(fish)
        _save()
        _icons.pop(fish_id, None)
        _features = None
        try:
            os.remove(icon_path(fish_id))
        except OSError:
            pass


def placeholder_icon(rarity=None, size=72):
    """Силуэт рыбы цвета редкости — пока у рыбы нет настоящей иконки."""
    import colorsys
    from PIL import ImageDraw
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    s = size
    d.ellipse([s * 0.12, s * 0.3, s * 0.72, s * 0.7], fill=255)
    d.polygon([(s * 0.66, s * 0.5), (s * 0.92, s * 0.28), (s * 0.92, s * 0.72)], fill=255)
    d.polygon([(s * 0.35, s * 0.32), (s * 0.5, s * 0.14), (s * 0.55, s * 0.34)], fill=255)
    d.ellipse([s * 0.22, s * 0.42, s * 0.3, s * 0.5], fill=0)   # глаз
    color = rarity_info(rarity)["color"]
    if color == "rainbow":
        row = np.array([colorsys.hsv_to_rgb(i / s, 0.7, 1.0) for i in range(s)]) * 255
        fill = Image.fromarray(np.repeat(row.astype(np.uint8)[None], s, axis=0), "RGB")
    else:
        c = color.lstrip("#")
        fill = Image.new("RGB", (s, s), tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)))
    icon = fill.convert("RGBA")
    icon.putalpha(mask)
    return icon


# ---------- распознавание по картинке ----------
def _hist(pixels):
    """Нормированная гистограмма цветов (N×3 uint8 → вектор)."""
    if len(pixels) == 0:
        return None
    q = (pixels.astype(np.int32) * BINS) // 256
    idx = q[:, 0] * BINS * BINS + q[:, 1] * BINS + q[:, 2]
    h = np.bincount(idx, minlength=BINS ** 3).astype(np.float32)
    return h / h.sum()


def _catalog_features():
    global _features
    with _lock:
        if _features is None:
            feats = {}
            for f in all_fish():
                try:
                    a = np.asarray(icon(f["id"]))
                except OSError:
                    continue
                h = _hist(a[a[:, :, 3] > 128][:, :3])
                if h is not None:
                    feats[f["id"]] = h
            _features = feats
        return _features


def _background(a):
    """Самый частый цвет по краю снимка."""
    edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    q = edge // 8
    keys, counts = np.unique(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2], return_counts=True)
    k = keys[counts.argmax()]
    return np.array([k // 1024, (k // 32) % 32, k % 32]) * 8 + 4


def foreground(rgb):
    """Пиксели снимка без фона."""
    a = np.asarray(rgb)[:, :, :3].astype(np.int16)
    mask = np.abs(a - _background(a)).max(axis=2) > 12
    return a[mask].astype(np.uint8)


def match(rgb):
    """(рыба из каталога, сходство 0..1) или (None, лучшее сходство)."""
    a = np.asarray(rgb)[:, :, :3]
    # Края снимка — рамка редкости и её свечение: они путают сравнение
    m = int(min(a.shape[:2]) * 0.06)
    if m:
        a = a[m:a.shape[0] - m, m:a.shape[1] - m]
    h = _hist(foreground(a))
    if h is None:
        return None, 0.0
    best, score = None, 0.0
    for fid, ref in _catalog_features().items():
        s = float(np.minimum(h, ref).sum())   # пересечение гистограмм
        if s > score:
            best, score = fid, s
    if score < MATCH_THRESHOLD:
        return None, score
    return by_id(best), score


def extract_icon(rgb):
    """Вырезать иконку рыбы из снимка уведомления: самый крупный цветной кусок без белого текста."""
    a = np.asarray(rgb)[:, :, :3].astype(np.int16)
    h, w = a.shape[:2]
    mask = np.abs(a - _background(a)).max(axis=2) > 12
    text = (a.min(axis=2) > 200) | ((a.max(axis=2) - a.min(axis=2) < 18) & (a.max(axis=2) > 150))
    body = mask & ~text
    cols = body.sum(axis=0) > max(2, h * 0.08)
    # Самый «тяжёлый» непрерывный участок столбцов — иконка (текст обычно отдельно справа)
    best, start, mass, best_mass = None, None, 0, 0
    for x in range(w + 1):
        on = x < w and cols[x]
        if on and start is None:
            start, mass = x, 0
        if on:
            mass += int(body[:, x].sum())
        elif start is not None:
            if mass > best_mass:
                best, best_mass = (start, x), mass
            start = None
    if best is None:
        x0, x1, y0, y1 = 0, w, 0, h
    else:
        x0, x1 = best
        rows = np.flatnonzero(body[:, x0:x1].sum(axis=1) > 0)
        y0, y1 = (rows[0], rows[-1] + 1) if rows.size else (0, h)
    pad = 3
    x0, y0, x1, y1 = max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, w), min(y1 + pad, h)
    crop = a[y0:y1, x0:x1].astype(np.uint8)
    alpha = (mask[y0:y1, x0:x1] * 255).astype(np.uint8)
    return Image.fromarray(np.dstack([crop, alpha]), "RGBA")
