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

        # Держим ЛКМ, пока регулятор тянет полосу вверх, иначе отпускаем.
        # Никакого ШИМ: частые нажатия-отпускания игра воспринимает как спам кликов
        return u > 0
