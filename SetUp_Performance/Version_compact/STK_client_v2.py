import math
import socket
import sys
import threading
import time
import subprocess
import os
from collections import deque
from oscpy.server import OSCThreadServer

GREEN = '\033[92m'
WHITE = '\x1b[0m'
BLUE = '\033[94m'
YELLOW = '\033[93m'
RED = '\033[91m'

STK_SERVER_ADDRESS = ('localhost', 6006)
OSC_LISTEN_IP = '0.0.0.0'
OSC_LISTEN_PORT = 8000

PAD_X_MIN, PAD_X_MAX = -1.0, 1.0
PAD_Y_MIN, PAD_Y_MAX = -1.0, 1.0
INVERT_Y = True

ACC_X_MIN, ACC_X_MAX = -3.0, 3.0
ACC_Y_MIN, ACC_Y_MAX = -7.0, -3.0
INVERT_ACC_Y = True

DEAD_ZONE_X = 0.20
DEAD_ZONE_Y = 0.20

TOUCH_TIMEOUT = 0.40
SENSOR_TIMEOUT = 0.50

STEERING_SOURCE = 'tilt'
TOUCH_MODE = 'joystick'
JOYSTICK_RADIUS = 0.50

FIRE_HOLD = 0.12
RESCUE_HOLD = 0.12
NITRO_HOLD = 0.12
TAP_MIN_DURATION = 0.03
TAP_MAX_DURATION = 0.25
DOUBLE_TAP_DELAY = 0.30

CAMERA_X_SHAKE_THRESHOLD = 3
CAMERA_SHAKE_MIN_SAMPLES = 2
CAMERA_SHAKE_MIN_REVERSALS = 1
GYR_X_SHAKE_THRESHOLD = 4.0
SHAKE_WINDOW = 0.60
SHAKE_MIN_SAMPLES = 3
SHAKE_MIN_REVERSALS = 2
RESCUE_COOLDOWN = 2.00
SHAKE_LOCKOUT = 1.00

GRAVITY = 9.81
MOTION_TOLERANCE = 3.00

CONTINUOUS_PERIOD = 0.05
SKID_KICKOFF = 0.15
SKID_INTO_MAX = 0.7
LOOP_HZ = 120


class STKSender:
    def __init__(self, address, debug=False):
        self.address = address
        self.debug = debug
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, command):
        self.socket.sendto(command.encode('utf-8'), self.address)
        if self.debug:
            print(YELLOW + '\t' + command + WHITE)


class KartState:
    STEERING_COMMANDS = {
        'LEFT': ('P_LEFT', 'R_LEFT'),
        'RIGHT': ('P_RIGHT', 'R_RIGHT'),
        'NONE': (None, None),
    }
    THROTTLE_COMMANDS = {
        'ACCELERATE': ('P_ACCELERATE', 'R_ACCELERATE'),
        'BRAKE': ('P_BRAKE', 'R_BRAKE'),
        'NONE': (None, None),
    }

    def __init__(self, sender, debug=False):
        self.sender = sender
        self.debug = debug
        self.steering = 'NONE'
        self.throttle = 'NONE'
        self.skidding_on = False
        self.nitro_on = False

    def _switch(self, table, current, target):
        if target == current:
            return current
        release_old = table[current][1]
        if release_old:
            self.sender.send(release_old)
        press_new = table[target][0]
        if press_new:
            self.sender.send(press_new)
        if self.debug:
            print(BLUE + f'\tetat : {current} -> {target}' + WHITE)
        return target

    def set_steering(self, target):
        self.steering = self._switch(self.STEERING_COMMANDS, self.steering, target)

    def set_throttle(self, target):
        self.throttle = self._switch(self.THROTTLE_COMMANDS, self.throttle, target)

    def fire(self):
        self.sender.send('FIRE')

    def rescue(self):
        self.sender.send('RESCUE')

    def set_skidding(self, active):
        if active == self.skidding_on:
            return
        self.sender.send('P_SKIDDING' if active else 'R_SKIDDING')
        self.skidding_on = active
        if self.debug:
            print(BLUE + '\tderapage : {}'.format('ON' if active else 'OFF') + WHITE)

    def set_nitro(self, active):
        if active == self.nitro_on:
            return
        self.sender.send('P_NITRO' if active else 'R_NITRO')
        self.nitro_on = active
        if self.debug:
            print(GREEN + '\tdouble-tap -> NITRO' + WHITE)

    def release_all(self):
        self.set_steering('NONE')
        self.set_throttle('NONE')
        self.set_skidding(False)

    def __str__(self):
        if self.steering == 'NONE' and self.throttle == 'NONE':
            return 'immobile'
        return f'{self.steering} + {self.throttle}'


class PadState:
    def __init__(self):
        self._lock = threading.Lock()
        self._x = None
        self._y = None
        self._pressed = False
        self._last_update = 0.0

    def set_x(self, value):
        with self._lock:
            self._x = value
            self._pressed = True
            self._last_update = time.time()

    def set_y(self, value):
        with self._lock:
            self._y = value
            self._pressed = True
            self._last_update = time.time()

    def set_touch_up(self):
        with self._lock:
            self._pressed = False

    def snapshot(self):
        with self._lock:
            if self._x is None or self._y is None:
                return None, None, False, float('inf')
            return self._x, self._y, self._pressed, time.time() - self._last_update


class Vector3State:
    def __init__(self):
        self._lock = threading.Lock()
        self._x = None
        self._y = None
        self._z = None
        self._last_update = 0.0

    def set_x(self, value):
        with self._lock:
            self._x = value
            self._last_update = time.time()

    def set_y(self, value):
        with self._lock:
            self._y = value
            self._last_update = time.time()

    def set_z(self, value):
        with self._lock:
            self._z = value
            self._last_update = time.time()

    def snapshot(self):
        with self._lock:
            if self._x is None or self._y is None or self._z is None:
                return None, None, None, float('inf')
            return self._x, self._y, self._z, time.time() - self._last_update


class CameraState:
    def __init__(self):
        self._lock = threading.Lock()
        self._x = None
        self._y = None
        self._z = None
        self._last_update = 0.0

    def pos_x(self, value):
        with self._lock:
            self._x = value
            self._last_update = time.time()

    def pos_y(self, value):
        with self._lock:
            self._y = value
            self._last_update = time.time()

    def pos_z(self, value):
        with self._lock:
            self._z = value
            self._last_update = time.time()

    def snapshot(self):
        with self._lock:
            if self._x is None or self._y is None or self._z is None:
                return None, None, None, float('inf')
            return self._x, self._y, self._z, time.time() - self._last_update


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


def normalize(value, vmin, vmax):
    if vmax == vmin:
        return 0.0
    ratio = (value - vmin) / (vmax - vmin)
    centered = ratio * 2.0 - 1.0
    return max(-1.0, min(1.0, centered))


def zone(value, dead_zone, negative_label, positive_label):
    if value > dead_zone:
        return positive_label
    if value < -dead_zone:
        return negative_label
    return 'NONE'


def intensity(value, dead_zone):
    magnitude = abs(value)
    if magnitude <= dead_zone or dead_zone >= 1.0:
        return 0.0
    return min(1.0, (magnitude - dead_zone) / (1.0 - dead_zone))


def gravity_deviation(ax, ay, az):
    return abs(math.sqrt(ax * ax + ay * ay + az * az) - GRAVITY)


def bind_all(osc, pad, acc, gyr, camera):
    osc.bind(b'/multisense/pad/x', lambda *values: pad.set_x(values[0]))
    osc.bind(b'/multisense/pad/y', lambda *values: pad.set_y(values[0]))
    osc.bind(b'/multisense/pad/touchUP', lambda *values: pad.set_touch_up())

    osc.bind(b'/multisense/accelerometer/x', lambda *values: acc.set_x(values[0]))
    osc.bind(b'/multisense/accelerometer/y', lambda *values: acc.set_y(values[0]))
    osc.bind(b'/multisense/accelerometer/z', lambda *values: acc.set_z(values[0]))

    osc.bind(b'/multisense/gyroscope/x', lambda *values: gyr.set_x(values[0]))
    osc.bind(b'/multisense/gyroscope/y', lambda *values: gyr.set_y(values[0]))
    osc.bind(b'/multisense/gyroscope/z', lambda *values: gyr.set_z(values[0]))

    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_x(values[0]))
    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_y(values[1]))
    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_z(values[2]))


def run_calibration():
    pad = PadState()
    acc = Vector3State()
    gyr = Vector3State()
    bounds = {
        'xmin': float('inf'), 'xmax': float('-inf'),
        'ymin': float('inf'), 'ymax': float('-inf'),
        'gyr': 0.0,
    }

    osc = OSCThreadServer()
    osc.listen(address=OSC_LISTEN_IP, port=OSC_LISTEN_PORT, default=True)
    osc.bind(b'/multisense/pad/x', lambda *v: pad.set_x(v[0]))
    osc.bind(b'/multisense/pad/y', lambda *v: pad.set_y(v[0]))
    osc.bind(b'/multisense/pad/touchUP', lambda *v: pad.set_touch_up())
    osc.bind(b'/multisense/accelerometer/x', lambda *v: acc.set_x(v[0]))
    osc.bind(b'/multisense/accelerometer/y', lambda *v: acc.set_y(v[0]))
    osc.bind(b'/multisense/accelerometer/z', lambda *v: acc.set_z(v[0]))
    osc.bind(b'/multisense/gyroscope/x', lambda *v: gyr.set_x(v[0]))

    print('\nMode calibration - Ctrl+C pour arreter')
    try:
        while True:
            x, y, pressed, page = pad.snapshot()
            ax, ay, az, aage = acc.snapshot()
            gx, gy, gz, gage = gyr.snapshot()

            ligne = ''
            if x is not None:
                bounds['xmin'] = min(bounds['xmin'], x)
                bounds['xmax'] = max(bounds['xmax'], x)
                bounds['ymin'] = min(bounds['ymin'], y)
                bounds['ymax'] = max(bounds['ymax'], y)
                etat_touch = 'touche ' if (pressed and page < TOUCH_TIMEOUT) else 'relache'
                ligne += f"pad {x:+.2f} {y:+.2f} [{bounds['xmin']:+.2f},{bounds['xmax']:+.2f}] {etat_touch} | "
            if ax is not None:
                ligne += f"acc {ax:+.2f} {ay:+.2f} {az:+.2f} | "
            if gx is not None:
                bounds['gyr'] = max(bounds['gyr'], abs(gx))
                ligne += f"gyr x {gx:+6.2f} rad/s pic {bounds['gyr']:5.2f}"
            if ligne:
                print('\r' + ligne + '   ', end='')
            time.sleep(1.0 / 30)
    except KeyboardInterrupt:
        print('\n\nValeurs observees :')
        print(f"  PAD_X: {bounds['xmin']:.3f}, {bounds['xmax']:.3f}")
        print(f"  PAD_Y: {bounds['ymin']:.3f}, {bounds['ymax']:.3f}")
        print(f"  GYR_X: {bounds['gyr'] * 0.7:.1f}")
    finally:
        osc.stop()


def main():
    debug = '-d' in sys.argv or '--debug' in sys.argv

    if '--calib' in sys.argv:
        run_calibration()
        return

    sender = STKSender(STK_SERVER_ADDRESS, debug=debug)
    kart = KartState(sender, debug=debug)
    pad = PadState()
    acc = Vector3State()
    gyr = Vector3State()
    camera = CameraState()
    tap_detector = TapDetector()
    shake_detector = ShakeDetector()
    drag = DragTracker()
    steering_pwm = ContinuousCommand()
    fire_button = HoldCommand(sender, 'P_FIRE', 'R_FIRE', FIRE_HOLD)
    rescue_button = HoldCommand(sender, 'P_RESCUE', 'R_RESCUE', RESCUE_HOLD)

    osc = OSCThreadServer()
    osc.listen(address=OSC_LISTEN_IP, port=OSC_LISTEN_PORT, default=True)
    bind_all(osc, pad, acc, gyr, camera)

    print()
    print('STK client v2 started ', end='')

    ici = os.path.dirname(os.path.abspath(__file__))
    extra_flags = {}
    if sys.platform == 'win32' and hasattr(subprocess, 'CREATE_NEW_CONSOLE'):
        extra_flags['creationflags'] = subprocess.CREATE_NEW_CONSOLE

    serveur = subprocess.Popen(
        [sys.executable, os.path.join(ici, 'STK_input_server.py'), '-d'],
        cwd=ici, **extra_flags,
    )
    time.sleep(1.0)
    tracking = subprocess.Popen(
        [sys.executable, os.path.join(ici, 'face_tracking.py')],
        cwd=ici, **extra_flags,
    )

    if debug:
        print(GREEN + '(Debug mode)' + WHITE)
    else:
        print()

    period = 1.0 / LOOP_HZ
    shake_lock_until = 0.0
    skid_started_at = 0.0
    skid_direction = 'NONE'

    try:
        while True:
            now = time.time()
            px, py, pressed, page = pad.snapshot()
            ax, ay, az, aage = acc.snapshot()
            gx, gy, gz, gage = gyr.snapshot()
            cx, cy, cz, cage = camera.snapshot()

            touching = pressed and page <= TOUCH_TIMEOUT
            acc_ok = ax is not None and aage <= SENSOR_TIMEOUT
            gyr_ok = gx is not None and gage <= SENSOR_TIMEOUT
            camera_ok = cx is not None and cage <= SENSOR_TIMEOUT
            double_tap, maintien = tap_detector.update(touching, now)

            acc_calme = acc_ok and gravity_deviation(ax, ay, az) <= MOTION_TOLERANCE

            if double_tap and px > 0:
                fire_button.trigger(now)
                if debug:
                    print(GREEN + '\tdouble-tap -> FIRE' + WHITE)

            skid = maintien and px > 0
            if skid and not kart.skidding_on:
                skid_started_at = now
            kart.set_skidding(skid)
            kart.set_nitro(maintien and px < 0)

            fire_button.update(now)

            gyro_shake = gyr_ok and shake_detector.updategyr(gx, now)
            camera_shake = camera_ok and shake_detector.updatecamera(cx, now) and cz > -90
            if gyro_shake or camera_shake:
                rescue_button.trigger(now)
                shake_lock_until = now + SHAKE_LOCKOUT
                if debug:
                    print(GREEN + '\tsecousse -> RESCUE' + WHITE)
            rescue_button.update(now)

            if touching:
                px_n = normalize(px, PAD_X_MIN, PAD_X_MAX)
                py_n = normalize(py, PAD_Y_MIN, PAD_Y_MAX)
            else:
                px_n = py_n = 0.0

            dx, dy = drag.update(touching, px_n, py_n)

            if now < shake_lock_until:
                nx = ny = 0.0
            elif STEERING_SOURCE == 'pad' and touching:
                if TOUCH_MODE == 'joystick':
                    nx, ny = dx, dy
                else:
                    nx, ny = px_n, py_n
                if INVERT_Y:
                    ny = -ny
            elif STEERING_SOURCE == 'tilt' and acc_calme:
                nx = normalize(ax, ACC_X_MIN, ACC_X_MAX)
                ny = normalize(ay, ACC_Y_MIN, ACC_Y_MAX)
                if INVERT_ACC_Y:
                    ny = -ny
            else:
                nx = ny = 0.0

            direction = zone(nx, DEAD_ZONE_X, 'LEFT', 'RIGHT')
            niveau = intensity(nx, DEAD_ZONE_X)

            amorce = kart.skidding_on and (now - skid_started_at) < SKID_KICKOFF
            if amorce and direction != 'NONE':
                skid_direction = direction

            if kart.skidding_on and direction == skid_direction:
                niveau = min(niveau, SKID_INTO_MAX)

            if amorce or steering_pwm.pressed(niveau, now):
                kart.set_steering(direction)
            else:
                kart.set_steering('NONE')

            kart.set_throttle(zone(ny, DEAD_ZONE_Y, 'BRAKE', 'ACCELERATE'))
            time.sleep(period)

    except KeyboardInterrupt:
        pass
    finally:
        kart.release_all()
        fire_button.release_now()
        rescue_button.release_now()
        osc.stop()
        if serveur is not None:
            serveur.terminate()
        if tracking is not None:
            tracking.terminate()
        print('\nSTK client v2 stopped')


if __name__ == '__main__':
    main()
