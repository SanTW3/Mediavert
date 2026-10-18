"""Окно отладки: что видит макрос в выбранной области и что он там распознал."""
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from . import screen
from .detector import detect


class Preview(tk.Toplevel):
    def __init__(self, root, cfg, engine):
        super().__init__(root)
        self.title("Окно отладки")
        self.attributes("-topmost", True)
        self.cfg = cfg
        self.engine = engine
        self.image_label = tk.Label(self, bg="black")
        self.image_label.pack(padx=6, pady=6)
        self.info = tk.Label(self, font=("Consolas", 9), justify="left")
        self.info.pack(padx=6, pady=(0, 6), anchor="w")
        self._photo = None
        self._tick()

    def _frame(self):
        # Пока макрос работает — берём его кадр, чтобы не захватывать экран дважды
        if self.engine.running and self.engine.last_detection:
            return self.engine.last_detection
        area = self.cfg.get("area")
        if not area:
            return None, None
        cfg = self.cfg
        rgb = screen.grab(area)
        return rgb, detect(rgb, cfg["zone_color"], int(cfg["zone_tolerance"]),
                           int(cfg["fish_white_min"]), int(cfg["min_zone_pixels"]),
                           cfg["bar_mode"], int(cfg["dark_max"]), float(cfg["gap_mult"]),
                           cfg["progress_color"], bool(cfg["require_progress"]))

    def _tick(self):
        if not self.winfo_exists():
            return
        try:
            rgb, det = self._frame()
        except Exception as exc:
            rgb, det = None, None
            self.info.config(text=f"Ошибка захвата: {exc}")
        if rgb is not None:
            img = Image.fromarray(rgb)
            draw = ImageDraw.Draw(img)
            if det:
                draw.rectangle([det.bar_x0 - 3, det.zone_top, det.bar_x1 + 3, det.zone_bottom],
                               outline=(255, 0, 255), width=2)
                draw.line([0, det.bar_top, img.width, det.bar_top], fill=(255, 255, 0))
                draw.line([0, det.bar_bottom, img.width, det.bar_bottom], fill=(255, 255, 0))
                if det.fish_y is not None:
                    draw.line([0, det.fish_y, img.width, det.fish_y], fill=(255, 0, 0), width=2)
                fish = f"{det.fish_y:.0f}" if det.fish_y is not None else "не найдена"
                self.info.config(text=f"Мини-игра: НАЙДЕНА\nПолоса игрока (пурпурная): {det.zone_top}-{det.zone_bottom}\n"
                                      f"Черта рыбы (красная): {fish}\nТрек (жёлтый): {det.bar_top}-{det.bar_bottom}")
            else:
                self.info.config(text="Мини-игра: не найдена\n(проверьте область и цвет трека)")
            scale = max(1, min(3, 400 // max(img.height, 1)))
            img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
            self._photo = ImageTk.PhotoImage(img)
            self.image_label.config(image=self._photo)
        elif not self.cfg.get("area"):
            self.info.config(text="Область не выбрана — нажмите F2")
        self.after(100, self._tick)
