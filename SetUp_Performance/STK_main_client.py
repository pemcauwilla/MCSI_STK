import math
import socket
import sys
import time

from oscpy.server import OSCThreadServer
from classes.SensorState import *
from classes.KartState import *
from classes.ActionDetector import *
from classes.Calibration import run_calibration

GREEN = '\033[92m'
WHITE = '\x1b[0m'
BLUE = '\033[94m'
YELLOW = '\033[93m'
RED = '\033[91m'

STK_SERVER_ADDRESS = ('localhost', 6006)
OSC_LISTEN_IP = '0.0.0.0'
OSC_LISTEN_PORT = 8000

# seuils pad
PAD_X_MIN, PAD_X_MAX = -1.0, 1.0
PAD_Y_MIN, PAD_Y_MAX = -1.0, 1.0
INVERT_Y = True

# seuils accelerometre
ACC_X_MIN, ACC_X_MAX = -1.5, 1.5
ACC_Y_MIN, ACC_Y_MAX = -7, -3
INVERT_ACC_Y = True

DEAD_ZONE_X = 0.20
DEAD_ZONE_Y = 0.20

TOUCH_TIMEOUT = 0.05
SENSOR_TIMEOUT = 0.50

STEERING_SOURCE = 'tilt'
TOUCH_MODE = 'joystick'

FIRE_HOLD = 0.12
RESCUE_HOLD = 0.12
NITRO_HOLD = 0.12

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

SKID_KICKOFF = 0.15
SKID_LEVEL_MIN = 0.3
SKID_LEVEL_MAX = 0.7
LOOP_HZ = 120


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

    osc.bind(b'/multisense/accelerometer/x', lambda *values: acc.set_x(values[0]))
    osc.bind(b'/multisense/accelerometer/y', lambda *values: acc.set_y(values[0]))
    osc.bind(b'/multisense/accelerometer/z', lambda *values: acc.set_z(values[0]))

    osc.bind(b'/multisense/gyroscope/x', lambda *values: gyr.set_x(values[0]))
    osc.bind(b'/multisense/gyroscope/y', lambda *values: gyr.set_y(values[0]))
    osc.bind(b'/multisense/gyroscope/z', lambda *values: gyr.set_z(values[0]))

    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_x(values[0]))
    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_y(values[1]))
    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_z(values[2]))


class STKSender:
    def __init__(self, address, debug=False):
        self.address = address
        self.debug = debug
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, command):
        self.socket.sendto(command.encode('utf-8'), self.address)
        if self.debug:
            print(YELLOW + '\t' + command + WHITE)


def main():
    debug = '-d' in sys.argv or '--debug' in sys.argv

    if '--calib' in sys.argv:
        run_calibration(bind_fn=bind_all)
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
    if debug:
        print(GREEN + '(Debug mode)' + WHITE)
    else:
        print()

    period = 1.0 / LOOP_HZ
    shake_lock_until = 0.0
    skid_started_at = 0.0

    try:
        while True:
            now = time.time()
            px, py, page = pad.snapshot()
            ax, ay, az, aage = acc.snapshot()
            gx, gy, gz, gage = gyr.snapshot()
            cx, cy, cz, cage = camera.snapshot()

            touching = px is not None and page <= TOUCH_TIMEOUT
            acc_ok = ax is not None and aage <= SENSOR_TIMEOUT
            gyr_ok = gx is not None and gage <= SENSOR_TIMEOUT
            camera_ok = cx is not None and cage <= SENSOR_TIMEOUT
            double_tap, maintien = tap_detector.update(touching, now)

            acc_calme = acc_ok and gravity_deviation(ax, ay, az) <= MOTION_TOLERANCE

            # tir sur double tap
            if double_tap and px > 0:
                fire_button.trigger(now)
                if debug:
                    print(GREEN + '\tdouble-tap -> FIRE' + WHITE)

            # derapage / nitro
            skid = maintien and px > 0
            if skid and not kart.skidding_on:
                skid_started_at = now
            kart.set_skidding(skid)
            kart.set_nitro(maintien and px < 0)

            fire_button.update(now)

            # secousse sauvetage
            gyro_shake = gyr_ok and shake_detector.updategyr(gx, now)
            camera_shake = camera_ok and shake_detector.updatecamera(cx, now)
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

            if kart.skidding_on:
                niveau = SKID_LEVEL_MIN + niveau * (SKID_LEVEL_MAX - SKID_LEVEL_MIN)

            amorce = kart.skidding_on and (now - skid_started_at) < SKID_KICKOFF

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
        print('\nSTK client stopped')


if __name__ == '__main__':
    main()
