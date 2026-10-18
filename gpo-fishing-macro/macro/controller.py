"""PD-регулятор: решает, держать ли ЛКМ, чтобы зона шла за рыбой."""


class PDController:
    def __init__(self, kp=0.9, kd=0.3, clamp=1.0, approach_damping=2.0, chase_damping=0.5):
        self.kp = kp
        self.kd = kd
        self.clamp = clamp
        self.approach_damping = approach_damping
        self.chase_damping = chase_damping
        self.reset()

    def reset(self):
        self._prev_error = None
        self._acc = 0.0

    def update(self, zone_center, fish_y, bar_height, dt):
        """Возвращает True, если ЛКМ должна быть зажата в этом кадре.

        Ошибка положительна, когда рыба выше центра зоны (в экранных координатах y меньше),
        и измеряется в долях высоты трека, чтобы коэффициенты не зависели от разрешения.
        """
        error = (zone_center - fish_y) / max(bar_height, 1)
        if self._prev_error is None or dt <= 0:
            derivative = 0.0
        else:
            derivative = (error - self._prev_error) / dt
        self._prev_error = error

        # Полоса едет к рыбе (ошибка уменьшается) — тормозим сильнее, чтобы не перелететь;
        # уезжает от рыбы — демпфируем слабее, чтобы быстрее догнать
        if error * derivative < 0:
            kd = self.kd * self.approach_damping
        elif error * derivative > 0:
            kd = self.kd * self.chase_damping
        else:
            kd = self.kd
        u = self.kp * error + kd * derivative
        u = max(-self.clamp, min(self.clamp, u))

        # Сигма-дельта ШИМ: доля кадров с зажатой кнопкой = (u + clamp) / (2 * clamp)
        duty = (u + self.clamp) / (2 * self.clamp) if self.clamp > 0 else (1.0 if u > 0 else 0.0)
        self._acc += duty
        if self._acc >= 1.0:
            self._acc -= 1.0
            return True
        return False
