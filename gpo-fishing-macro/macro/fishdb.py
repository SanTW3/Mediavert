"""Каталог рыб GPO: иконки из игры, редкость (по цвету рамки) и цена продажи.

Имён рыб в интерфейсе игры нет, поэтому названия даны по внешнему виду — их можно
поменять в макросе (Рыбалка → Каталог рыб), правки хранятся в config["fish_names"].

Пойманную рыбу находим по снимку уведомления: сравниваем цветовой состав снимка
с цветовым составом каждой иконки (гистограмма цветов без фона). Такое сравнение
не зависит от размера иконки и фона вокруг неё.
"""
import os
import sys

import numpy as np
from PIL import Image

RARITIES = {
    "common": {"title": "Обычная", "color": "#e8eef5"},
    "rare": {"title": "Редкая", "color": "#4fd8ff"},
    "epic": {"title": "Эпическая", "color": "#b48cff"},
    "legendary": {"title": "Легендарная", "color": "rainbow"},
}

# id = имя файла иконки в assets/fish; price — цена продажи одной рыбы (Peli)
FISH = [
    {"id": "00", "name": "Огненный хвост", "rarity": "rare", "price": 1004},
    {"id": "01", "name": "Мухоморная рыба", "rarity": "rare", "price": 1057},
    {"id": "02", "name": "Голубой окунь", "rarity": "common", "price": 162},
    {"id": "03", "name": "Конфетная рыба", "rarity": "common", "price": 38},
    {"id": "04", "name": "Пятнистая карамель", "rarity": "legendary", "price": 11941},
    {"id": "05", "name": "Тигровая карамель", "rarity": "legendary", "price": 12840},
    {"id": "06", "name": "Тыквенный удильщик", "rarity": "legendary", "price": 10583},
    {"id": "07", "name": "Призрачная акула", "rarity": "epic", "price": 14913},
    {"id": "08", "name": "Чёрный окунь", "rarity": "rare", "price": 2703},
    {"id": "09", "name": "Огненный кальмар", "rarity": "rare", "price": 12318},
    {"id": "10", "name": "Тигровая рыба", "rarity": "rare", "price": 2000},
    {"id": "11", "name": "Зебровый серп", "rarity": "rare", "price": 2328},
]
RARITY_ORDER = {"legendary": 0, "epic": 1, "rare": 2, "common": 3, None: 4}
MATCH_THRESHOLD = 0.40   # меньше — «неизвестная рыба»
BINS = 6                 # уровней на канал → 216 корзин цвета


def _assets_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "assets", "fish")


def icon_path(fish_id):
    return os.path.join(_assets_dir(), f"{fish_id}.png")


_icons = {}
_features = None


def icon(fish_id):
    """PIL RGBA-иконка рыбы (фон прозрачный)."""
    if fish_id not in _icons:
        _icons[fish_id] = Image.open(icon_path(fish_id)).convert("RGBA")
    return _icons[fish_id]


def by_id(fish_id):
    for f in FISH:
        if f["id"] == fish_id:
            return f
    return None


def display_name(fish, cfg=None):
    names = (cfg or {}).get("fish_names") or {}
    return names.get(fish["id"]) or fish["name"]


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
    if _features is None:
        _features = {}
        for f in FISH:
            a = np.asarray(icon(f["id"]))
            _features[f["id"]] = _hist(a[a[:, :, 3] > 128][:, :3])
    return _features


def foreground(rgb):
    """Пиксели снимка без фона. Фон — самый частый цвет по краю снимка."""
    a = np.asarray(rgb)[:, :, :3].astype(np.int16)
    edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    q = edge // 8
    keys, counts = np.unique(q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2], return_counts=True)
    k = keys[counts.argmax()]
    bg = np.array([k // 1024, (k // 32) % 32, k % 32]) * 8 + 4
    mask = np.abs(a - bg).max(axis=2) > 12
    return a[mask].astype(np.uint8)


def match(rgb):
    """(рыба из каталога, сходство 0..1) или (None, лучшее сходство)."""
    a = np.asarray(rgb)
    # Края снимка — рамка редкости и её свечение: они путают сравнение (серое свечение «похоже» на акулу)
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
