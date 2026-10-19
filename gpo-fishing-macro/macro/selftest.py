"""Самопроверка exe (GPO-Fishing-Macro.exe --selftest): Windows OCR, разбор уведомления, paho-mqtt.

Результат пишется в selftest.txt рядом с exe — у оконного exe нет консоли.
"""
import os
import traceback

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import config, ocr
from .catches import parse_name


def run():
    out = []
    ok = False
    try:
        img = Image.new("RGB", (520, 60), (40, 110, 190))
        font = ImageFont.truetype("arialbd.ttf", 30)
        ImageDraw.Draw(img).text((10, 12), "New Item <Golden Carp>", font=font, fill=(255, 255, 255),
                                 stroke_width=2, stroke_fill=(0, 0, 0))
        out.append(f"ocr.available: {ocr.available()}")
        text = ocr.read_text(np.asarray(img), raise_errors=True)
        out.append(f"ocr text: {text!r}")
        name = parse_name(text)
        out.append(f"parsed: {name!r}")
        ok = name == "Golden Carp"
        # Канал мини-приложения: paho-mqtt должен попасть в exe
        import paho.mqtt.client as mqtt
        out.append(f"paho-mqtt: {mqtt.__name__} ok")
    except Exception:
        out.append(traceback.format_exc())
    out.append("RESULT: " + ("OK" if ok else "FAIL"))
    with open(os.path.join(config.app_dir(), "selftest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return 0 if ok else 1
