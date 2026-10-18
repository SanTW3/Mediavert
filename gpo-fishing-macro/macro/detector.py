"""Распознавание мини-игры рыбалки по скриншоту выбранной области.

Как выглядит мини-игра GPO (см. README):
  * левая вертикальная полоса — синий трек;
  * по треку ходит тёмно-серая полоса игрока (зажатая ЛКМ поднимает её, отпущенная — роняет);
  * белая горизонтальная черта на той же полосе — рыба;
  * правая зелёная полоса — прогресс поимки (нам не нужна).
Задача — держать белую черту внутри тёмно-серой полосы.

Режим bar_mode="blue" — наоборот: полоса игрока синяя, трек тёмный (на случай, если в вашей
версии игры так).
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class Detection:
    zone_top: int        # y верхнего края полосы игрока (в координатах области)
    zone_bottom: int     # y нижнего края
    bar_top: int         # верх трека (куда может доехать полоса)
    bar_bottom: int      # низ трека
    fish_y: float | None  # y белой черты, None если не нашли
    bar_x0: int
    bar_x1: int

    @property
    def zone_center(self):
        return (self.zone_top + self.zone_bottom) / 2

    @property
    def bar_height(self):
        return max(self.bar_bottom - self.bar_top, 1)


def _longest_run(mask_1d, max_gap):
    """Самый длинный участок True, склеивая разрывы до max_gap (белая черта рвёт полосу)."""
    idx = np.flatnonzero(mask_1d)
    if idx.size == 0:
        return None
    best = (idx[0], idx[0])
    start = prev = idx[0]
    for i in idx[1:]:
        if i - prev > max_gap + 1:
            if prev - start > best[1] - best[0]:
                best = (start, prev)
            start = i
        prev = i
    if prev - start > best[1] - best[0]:
        best = (start, prev)
    return int(best[0]), int(best[1])


def detect(rgb, zone_color=(85, 170, 255), tolerance=40, white_min=200, min_zone_pixels=30,
           bar_mode="dark", dark_max=60, gap_mult=2.0):
    """rgb — массив HxWx3 (uint8). Возвращает Detection или None, если мини-игры нет.

    zone_color — цвет синего трека: по нему находим колонки полосы и понимаем, что мини-игра идёт.
    dark_max — максимальная яркость «тёмно-серых» пикселей полосы игрока.
    gap_mult — Gap Tolerance Multiplier: разрывы в тёмно-серой полосе до white_height * gap_mult
    склеиваются (их создаёт белая черта рыбы поверх полосы).
    """
    img = rgb.astype(np.int16)
    diff = np.abs(img - np.array(zone_color, dtype=np.int16)).max(axis=2)
    blue_mask = diff <= tolerance
    if blue_mask.sum() < min_zone_pixels:
        return None

    # Колонки полосы: те, где синего больше половины от максимума
    col_counts = blue_mask.sum(axis=0)
    peak = col_counts.max()
    run = _longest_run(col_counts >= max(peak * 0.5, 3), max_gap=1)
    if run is None:
        return None
    x0, x1 = run
    h, w = blue_mask.shape
    # Трек мини-игры — узкая вертикальная полоса. Широкое синее пятно — это вода или небо
    if x1 - x0 + 1 > max(w * 0.3, 6):
        return None
    cols = img[:, x0:x1 + 1]
    blue_rows = blue_mask[:, x0:x1 + 1].mean(axis=1) >= 0.5

    # Белая черта: почти белые пиксели в колонках полосы (с небольшим запасом по краям)
    pad = 2
    xa, xb = max(x0 - pad, 0), min(x1 + pad + 1, img.shape[1])
    white = img[:, xa:xb].min(axis=2) >= white_min
    white_rows = white.sum(axis=1) >= max((xb - xa) * 0.4, 2)
    fish_y = None
    white_height = 2
    wrun = _longest_run(white_rows, max_gap=1)
    if wrun:
        fish_y = (wrun[0] + wrun[1]) / 2
        white_height = wrun[1] - wrun[0] + 1
    # Сглаженные края черты дают серые строки сверху и снизу — тоже считаем их частью черты
    near_white = img[:, x0:x1 + 1].min(axis=2).mean(axis=1) >= 60

    gray = (cols.max(axis=2) <= dark_max) & ((cols.max(axis=2) - cols.min(axis=2)) <= 25)
    dark_rows = gray.mean(axis=1) >= 0.5

    if bar_mode == "blue":
        zone_rows, gap = blue_rows, 6
    else:
        zone_rows = dark_rows
        gap = max(int(round(white_height * float(gap_mult))), 2) + 2
    zrun = _longest_run(zone_rows, max_gap=gap)
    if zrun is None or zrun[1] - zrun[0] < 4:
        return None
    zone_top, zone_bottom = zrun

    # Трек — синие, тёмные и белые строки в колонках полосы
    trun = _longest_run(blue_rows | dark_rows | white_rows | near_white, max_gap=3)
    bar_top, bar_bottom = trun if trun else (zone_top, zone_bottom)
    bar_top = min(bar_top, zone_top)
    bar_bottom = max(bar_bottom, zone_bottom)
    # Трек занимает большую часть высоты выбранной области; короткий обрывок — ложное срабатывание
    if bar_bottom - bar_top < h * 0.4:
        return None

    return Detection(zone_top, zone_bottom, bar_top, bar_bottom, fish_y, x0, x1)


def black_fraction(rgb, level=20):
    """Доля почти чёрных пикселей (античит GPO затемняет экран)."""
    return float((rgb.max(axis=2) <= level).mean())
