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
import re
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
    {"id": "00", "name": "Crimson Snapper", "rarity": "rare", "price": 1004},
    {"id": "01", "name": "Crimson Polka Puffer", "rarity": "rare", "price": 1057},
    {"id": "02", "name": "Blue-Lip Grouper", "rarity": "common", "price": 162},
    {"id": "03", "name": "Tigerfin", "rarity": "common", "price": 38},
    {"id": "04", "name": "Golden Polka Puffer", "rarity": "legendary", "price": 11941},
    {"id": "05", "name": "Golden Tigerfin", "rarity": "legendary", "price": 12840},
    {"id": "06", "name": "Jack-O'-Bite", "rarity": "legendary", "price": 10583},
    {"id": "07", "name": "Skeletal Shark", "rarity": "epic", "price": 14913},
    {"id": "08", "name": "Dark Skeletal Shark", "rarity": "legendary", "price": 2703},
    {"id": "09", "name": "Candy Corn Squid", "rarity": "rare", "price": 12318},
    {"id": "10", "name": "Exotic Tigerfin", "rarity": "rare", "price": 2000},
    {"id": "11", "name": "Zebra Ribbon Angelfish", "rarity": "rare", "price": 2328},
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


# Названия рыб из игры (по-английски, как в уведомлении «New Item <…>»). OCR путает в них 1–2 буквы,
# поэтому прочитанное название сравниваем с этим списком с погрешностью (fuzzy)
KNOWN_NAMES = [
    "Swordfish", "Dark Skeletal Shark", "Golden Ribbon Angelfish", "Golden Tigerfin", "Golden Polka Puffer",
    "Candy Corn Squid", "Jack-O'-Bite", "Skeletal Shark", "Fangfish", "Exotic Tigerfin", "Crimson Snapper",
    "Zebra Ribbon Angelfish", "Crimson Polka Puffer", "Tigerfin", "Blue-Lip Grouper",
]


def name_key(name):
    """Только буквы: «Jack-O’-Bite» и «Jack O Bite» — одно и то же."""
    return re.sub(r"[^a-z]", "", (name or "").lower())


def tolerance(key):
    """Сколько букв OCR может перепутать: 1 в коротких названиях, до 3 в длинных."""
    n = len(key)
    return 0 if n < 4 else 1 if n < 8 else 2 if n < 14 else 3


def distance(a, b, limit=None):
    """Расстояние Левенштейна (замена, лишняя или потерянная буква = 1)."""
    if abs(len(a) - len(b)) > (limit if limit is not None else len(a) + len(b)):
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def nearest(name, choices):
    """Самое похожее из choices в пределах погрешности или None. Если два варианта одинаково
    похожи, не угадываем."""
    key = name_key(name)
    if not key:
        return None
    tol = tolerance(key)
    scored = sorted((distance(key, name_key(c), tol), i, c) for i, c in enumerate(choices) if name_key(c))
    if not scored or scored[0][0] > tol:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0] and name_key(scored[1][2]) != name_key(scored[0][2]):
        return None
    return scored[0][2]


def known_names(cfg=None, learned=True):
    """Названия, к которым можно «притянуть» прочитанное: список игры, каталог и (learned=True)
    выученные из уведомлений."""
    out = list(KNOWN_NAMES)
    for f in all_fish():
        if learned or not f["id"].startswith("u"):
            out.append(display_name(f, cfg))
    if learned:
        with _lock:
            out += list(_load()["names"].values())
    return out


def canonical(name, cfg=None, learned=True):
    """Правильное написание прочитанного названия («Crimson Polkaxpuffer» → «Crimson Polka Puffer»)
    или None, если такой рыбы мы не знаем. learned=False — только список игры и рыбы со скринов
    (выученные могли попасть в каталог по ошибке)."""
    return nearest(name, known_names(cfg, learned))


def find_by_name(name, cfg=None):
    if not name:
        return None
    key = name_key(name)
    for f in all_fish():
        if name_key(display_name(f, cfg)) == key:
            return f
    with _lock:
        names = dict(_load()["names"])
    for fid, n in names.items():
        if name_key(n) == key:
            return by_id(fid)
    # С погрешностью: OCR перепутал букву-другую
    choices = {display_name(f, cfg): f["id"] for f in all_fish()}
    choices.update({n: fid for fid, n in names.items()})
    best = nearest(name, list(choices))
    return by_id(choices[best]) if best else None


# Обрывки надписи «SAFE ZONE PROTECTED», которые версия 1.1.0 и раньше записывала как рыбу
JUNK = re.compile(r"\b(saf|safe|zone|cted|protected|ow)\b", re.IGNORECASE)


def tidy_learned(cfg=None):
    """Привести выученных рыб к правильным названиям: опечатки OCR исправить, а рыбу, которая
    уже есть в каталоге с иконкой, слить с ней. Возвращает [(старый id, новый id)] для журнала."""
    moved = []
    with _lock:
        names = _load()["names"]
        for fid, n in list(names.items()):
            right = nearest(n, KNOWN_NAMES)
            if right and right != n:
                names[fid] = right
                _save()
    for f in list(_load()["fish"]):
        name = learned_name(f["id"]) or f["name"]
        right = nearest(name, KNOWN_NAMES)
        if not right and JUNK.search(name):
            remove_fish(f["id"])            # «SAFE ZONE PROTECTED», прочитанное старой версией
            moved.append((f["id"], None))
            continue
        if right and right != name:
            with _lock:
                f["name"] = right
                _load()["names"].pop(f["id"], None)
                _save()
            name = right
        twin = next((b for b in all_fish() if b["id"] != f["id"]
                     and name_key(display_name(b, cfg)) == name_key(name)), None)
        if twin is not None and not twin["id"].startswith("u"):
            link_to_builtin(f["id"], twin["id"])
            moved.append((f["id"], twin["id"]))
        elif twin is not None and twin["id"] < f["id"]:
            remove_fish(f["id"])            # та же рыба записана дважды
            moved.append((f["id"], twin["id"]))
    return moved


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


def remove_fish(fish_id):
    """Удалить выученную рыбу (например, мусор, прочитанный не из уведомления о рыбе)."""
    global _features
    with _lock:
        data = _load()
        fish = next((f for f in data["fish"] if f["id"] == fish_id), None)
        if fish is None:
            return False
        data["fish"].remove(fish)
        data["names"].pop(fish_id, None)
        _save()
        _icons.pop(fish_id, None)
        _features = None
        try:
            os.remove(icon_path(fish_id))
        except OSError:
            pass
        return True


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
