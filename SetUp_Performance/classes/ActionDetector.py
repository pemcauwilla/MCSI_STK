from collections import deque

TAP_MIN_DURATION = 0.08
TAP_MAX_DURATION = 0.40
DOUBLE_TAP_DELAY = 0.30

JOYSTICK_RADIUS = 0.50

CAMERA_X_SHAKE_THRESHOLD = 3
CAMERA_SHAKE_MIN_SAMPLES = 2
CAMERA_SHAKE_MIN_REVERSALS = 1

GYR_X_SHAKE_THRESHOLD = 4.0
SHAKE_WINDOW = 0.60
SHAKE_MIN_SAMPLES = 3
SHAKE_MIN_REVERSALS = 2
RESCUE_COOLDOWN = 2.00
SHAKE_LOCKOUT = 1.00

CONTINUOUS_PERIOD = 0.05


class ContinuousCommand:
    def __init__(self):
        self._phase = 0.0
        self._last_time = None

    def pressed(self, level, now):
        if self._last_time is None:
            self._last_time = now
        dt = now - self._last_time
        self._last_time = now

        if level <= 0.0:
            self._phase = 0.0
            return False
        if level >= 1.0:
            return True

        self._phase = (self._phase + dt) % CONTINUOUS_PERIOD
        return self._phase < level * CONTINUOUS_PERIOD


class TapDetector:
    def __init__(self):
        self._touching = False
        self._touch_start = 0.0
        self._last_tap_end = None
        self._second_tap = False

    def update(self, touching, now):
        double_tap = False

        if touching and not self._touching:
            self._touch_start = now
            self._second_tap = (
                self._last_tap_end is not None and now - self._last_tap_end <= DOUBLE_TAP_DELAY
            )
        elif not touching and self._touching:
            duration = now - self._touch_start
            if TAP_MIN_DURATION <= duration <= TAP_MAX_DURATION:
                if self._second_tap:
                    double_tap = True
                    self._last_tap_end = None
                else:
                    self._last_tap_end = now
            else:
                self._last_tap_end = None
            self._second_tap = False

        self._touching = touching
        maintien = touching and (now - self._touch_start) > TAP_MAX_DURATION
        return double_tap, maintien


class ShakeDetector:
    def __init__(self):
        self._history = deque()
        self._last_rescue = 0.0
        self._previous_cx = None

    def updategyr(self, gx, now):
        if gx is None:
            return False

        self._history.append((now, gx))
        while self._history and now - self._history[0][0] > SHAKE_WINDOW:
            self._history.popleft()

        if now - self._last_rescue < RESCUE_COOLDOWN:
            return False

        signes = [1 if v >= 0 else -1 for _, v in self._history if v is not None and abs(v) > GYR_X_SHAKE_THRESHOLD]
        if len(signes) < SHAKE_MIN_SAMPLES:
            return False

        inversions = sum(1 for a, b in zip(signes, signes[1:]) if a != b)
        if inversions < SHAKE_MIN_REVERSALS:
            return False

        self._last_rescue = now
        self._history.clear()
        return True

    def updatecamera(self, cx, now):
        if self._previous_cx is None:
            self._previous_cx = cx
            return False

        dx = cx - self._previous_cx
        self._previous_cx = cx

        self._history.append((now, dx))
        while self._history and now - self._history[0][0] > SHAKE_WINDOW:
            self._history.popleft()

        if now - self._last_rescue < RESCUE_COOLDOWN:
            return False

        signes = [1 if v > 0 else -1 for _, v in self._history if abs(v) > CAMERA_X_SHAKE_THRESHOLD]
        if len(signes) < CAMERA_SHAKE_MIN_SAMPLES:
            return False

        inversions = sum(1 for a, b in zip(signes, signes[1:]) if a != b)
        if inversions < CAMERA_SHAKE_MIN_REVERSALS:
            return False

        self._last_rescue = now
        self._history.clear()
        return True


class HoldCommand:
    def __init__(self, sender, press_cmd, release_cmd, hold):
        self.sender = sender
        self.press_cmd = press_cmd
        self.release_cmd = release_cmd
        self.hold = hold
        self._release_at = None

    def trigger(self, now):
        if self._release_at is not None:
            self.sender.send(self.release_cmd)
        self.sender.send(self.press_cmd)
        self._release_at = now + self.hold

    def update(self, now):
        if self._release_at is None:
            return
        if now < self._release_at:
            return
        self.sender.send(self.release_cmd)
        self._release_at = None

    def release_now(self):
        if self._release_at is not None:
            self.sender.send(self.release_cmd)
            self._release_at = None


class DragTracker:
    def __init__(self):
        self._touching = False
        self._origin_x = 0.0
        self._origin_y = 0.0

    def update(self, touching, px, py):
        if touching and not self._touching:
            self._origin_x, self._origin_y = px, py
        self._touching = touching

        if not touching:
            return 0.0, 0.0

        dx = (px - self._origin_x) / JOYSTICK_RADIUS
        dy = (py - self._origin_y) / JOYSTICK_RADIUS
        return (max(-1.0, min(1.0, dx)), max(-1.0, min(1.0, dy)))