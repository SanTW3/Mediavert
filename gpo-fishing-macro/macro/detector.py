"""Распознавание мини-игры рыбалки по скриншоту выбранной области.

Как выглядит мини-игра GPO (см. README):
  * левая вертикальная полоса — тёмный трек, по которому ходит синяя зона
    игрока (зажатая ЛКМ поднимает её, отпущенная — роняет);
  * белая горизонтальная черта на той же полосе — рыба;
  * правая зелёная полоса — прогресс поимки (нам не нужна).
Задача — держать белую черту внутри синей зоны.
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class Detection:
    zone_top: int        # y верхнего края синей зоны (в координатах области)
    zone_bottom: int     # y нижнего края
    bar_top: int         # верх трека (куда может доехать зона)
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
    """Самый длинный участок True, склеивая разрывы до max_gap (белая черта рвёт зону)."""
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


def detect(rgb, zone_color=(85, 170, 255), tolerance=40, white_min=200, min_zone_pixels=30):
    """rgb — массив HxWx3 (uint8). Возвращает Detection или None, если мини-игры нет."""
    img = rgb.astype(np.int16)
    diff = np.abs(img - np.array(zone_color, dtype=np.int16)).max(axis=2)
    zone_mask = diff <= tolerance
    if zone_mask.sum() < min_zone_pixels:
        return None

    # Колонки полосы: те, где синего больше половины от максимума
    col_counts = zone_mask.sum(axis=0)
    peak = col_counts.max()
    cols = col_counts >= max(peak * 0.5, 3)
    run = _longest_run(cols, max_gap=1)
    if run is None:
        return None
    x0, x1 = run
    bar = zone_mask[:, x0:x1 + 1]
    rows = bar.mean(axis=1) >= 0.5
    zrun = _longest_run(rows, max_gap=6)
    if zrun is None or zrun[1] - zrun[0] < 4:
        return None
    zone_top, zone_bottom = zrun

    # Белая черта: почти белые пиксели в колонках полосы (с небольшим запасом по краям)
    pad = 2
    xa, xb = max(x0 - pad, 0), min(x1 + pad + 1, img.shape[1])
    white = img[:, xa:xb].min(axis=2) >= white_min
    white_rows = white.sum(axis=1) >= max((xb - xa) * 0.4, 2)
    fish_y = None
    wrows = np.flatnonzero(white_rows)
    if wrows.size:
        # Берём самую «плотную» группу строк
        wrun = _longest_run(white_rows, max_gap=1)
        fish_y = (wrun[0] + wrun[1]) / 2

    # Трек — тёмные или синие пиксели над и под зоной в тех же колонках
    off = x0 - xa
    track = (img[:, x0:x1 + 1].max(axis=2) <= 70) | bar | white[:, off:off + (x1 - x0 + 1)]
    trun = _longest_run(track.mean(axis=1) >= 0.5, max_gap=3)
    bar_top, bar_bottom = (trun if trun else (zone_top, zone_bottom))
    bar_top = min(bar_top, zone_top)
    bar_bottom = max(bar_bottom, zone_bottom)

    return Detection(zone_top, zone_bottom, bar_top, bar_bottom, fish_y, x0, x1)


def black_fraction(rgb, level=20):
    """Доля почти чёрных пикселей (античит GPO затемняет экран)."""
    return float((rgb.max(axis=2) <= level).mean())
