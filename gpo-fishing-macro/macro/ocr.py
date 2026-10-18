"""Распознавание текста встроенным в Windows OCR (Windows.Media.Ocr) — без Tesseract.

Нужны пакеты winrt-* (ставятся из requirements.txt только на Windows). Если их нет
или Windows не умеет OCR, read_text() возвращает "" — журнал улова тогда группирует
рыбу по иконке.
"""
import asyncio
import sys

_engine = None
_unavailable = False


def _get_engine():
    global _engine, _unavailable
    if _engine is not None or _unavailable:
        return _engine
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        # Текст в GPO английский: сначала английский движок, иначе — языки профиля
        engine = None
        try:
            if OcrEngine.is_language_supported(Language("en-US")):
                engine = OcrEngine.try_create_from_language(Language("en-US"))
        except Exception:
            engine = None
        _engine = engine or OcrEngine.try_create_from_user_profile_languages()
        if _engine is None:
            _unavailable = True
    except Exception:
        _unavailable = True
    return _engine


def available():
    return sys.platform == "win32" and _get_engine() is not None


async def _recognize(engine, rgb):
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    h, w = rgb.shape[:2]
    import numpy as np
    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0] = rgb[:, :, 2]
    bgra[:, :, 1] = rgb[:, :, 1]
    bgra[:, :, 2] = rgb[:, :, 0]
    bgra[:, :, 3] = 255
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    buffer = writer.detach_buffer()
    try:
        bitmap = SoftwareBitmap.create_copy_from_buffer(buffer, BitmapPixelFormat.BGRA8, w, h)
    except TypeError:
        # В разных версиях winrt разный набор перегрузок — запасной путь
        bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)
        bitmap.copy_from_buffer(buffer)
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append((w.text, (r.x, r.y, r.width, r.height)))
        lines.append({"text": line.text, "words": words})
    return {"text": result.text or "", "lines": lines}


last_error = None


def read_lines(rgb, raise_errors=False):
    """Строки текста со словами и их рамками в координатах исходной картинки:
    [{"text": "New Item <Fangfish>", "words": [("New", (x, y, w, h)), …]}, …]"""
    global last_error
    engine = _get_engine() if sys.platform == "win32" else None
    if engine is None:
        return []
    # Мелкий игровой текст OCR читает лучше, если картинку увеличить
    from PIL import Image
    import numpy as np
    img = Image.fromarray(np.ascontiguousarray(rgb))
    k = 1.0
    if img.height < 60:
        k = 60 / max(img.height, 1)
        img = img.resize((int(img.width * k), 60), Image.LANCZOS)
    try:
        data = asyncio.run(_recognize(engine, np.ascontiguousarray(np.asarray(img))))
    except Exception as exc:
        last_error = exc
        if raise_errors:
            raise
        return []
    for line in data["lines"]:
        line["words"] = [(t, tuple(v / k for v in rect)) for t, rect in line["words"]]
    return data["lines"]


def read_text(rgb, raise_errors=False):
    """rgb — HxWx3 uint8. Распознанный текст или "" (ошибку — в ocr.last_error)."""
    return "\n".join(line["text"] for line in read_lines(rgb, raise_errors))
