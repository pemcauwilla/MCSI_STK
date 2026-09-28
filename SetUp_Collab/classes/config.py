import math


def normalize(value, vmin, vmax):
    # normalise [vmin, vmax] vers [-1, 1]
    if vmax == vmin:
        return 0.0
    ratio = (value - vmin) / (vmax - vmin)
    centered = ratio * 2.0 - 1.0
    return max(-1.0, min(1.0, centered))


def zone(value, dead_zone, negative_label, positive_label):
    # seuillage avec zone morte
    if value > dead_zone:
        return positive_label
    if value < -dead_zone:
        return negative_label
    return 'NONE'


def intensity(value, dead_zone):
    # intensite [0, 1] au-dela de la zone morte
    magnitude = abs(value)
    if magnitude <= dead_zone or dead_zone >= 1.0:
        return 0.0
    return min(1.0, (magnitude - dead_zone) / (1.0 - dead_zone))


GRAVITY = 9.81

# resolution webcam
FRAME_WIDTH = 640
FRAME_HEIGHT = 360

# seuils direction
ANGLE_TETE_MAX = 20.0
DEAD_ZONE_ANGLE_DEG = 5.0
DEAD_ZONE_X_FACE = DEAD_ZONE_ANGLE_DEG / ANGLE_TETE_MAX
INVERT_TETE = False

# mode position horizontale
DELTA_X_MAX = 8.0
DEAD_ZONE_X_POS = 0.25
TURN_SPAN_PX = 60.0

# limites visuelles normalisees pour le hud
SOLO_LEFT_THRESHOLD = 0.44
SOLO_RIGHT_THRESHOLD = 0.56
DUO_P1_LEFT_THRESHOLD = 0.19
DUO_P1_RIGHT_THRESHOLD = 0.31

# seuils acceleration / freinage (distance z en cm)
DEFAULT_Z_NEUTRAL = 75.0
DEAD_ZONE_Z_CM = 7.0
DELTA_Z_MAX = 20.0
POS_Z_MIN = 60.0
POS_Z_MAX = 110.0
DEAD_ZONE_Z = 0.20

# detection carte couleur (tir)
COLOR_DETECTION_ENABLED = True
COLOR_PRESET = "green"

COLOR_CUSTOM_LOWER = (0, 100, 70)
COLOR_CUSTOM_UPPER = (10, 255, 255)

# filtres morpho et taille
COLOR_MIN_AREA = 300
COLOR_MAX_AREA = 45000
COLOR_MAX_ASPECT_RATIO = 3.5
COLOR_MIN_SOLIDITY = 0.65
COLOR_MIN_EXTENT = 0.35

# timings tir
COLOR_COOLDOWN_SECONDS = 1.6
COLOR_PULSE_DURATION = 0.40
COLOR_CONFIRM_FRAMES = 3


def gravity_deviation(ax, ay, az):
    return abs(math.sqrt(ax * ax + ay * ay + az * az) - GRAVITY)


def bind_all(
    osc,
    gyr,
    eye1,
    eye2,
    camera,
    p1_eye1=None,
    p1_eye2=None,
    p1_camera=None,
    p2_camera=None,
    fire_trigger=None,
    z_neutral=None,
):
    # routes osc pour smartphone / gyro
    osc.bind(b'/multisense/gyroscope/x', lambda *values: gyr.set_x(values[0]))
    osc.bind(b'/multisense/gyroscope/y', lambda *values: gyr.set_y(values[0]))
    osc.bind(b'/multisense/gyroscope/z', lambda *values: gyr.set_z(values[0]))

    # routes tracking solo
    osc.bind(b'/tracker/eyes1/pos_xyz', lambda *values: eye1.pos_x(values[0]))
    osc.bind(b'/tracker/eyes1/pos_xyz', lambda *values: eye1.pos_y(values[1]))
    osc.bind(b'/tracker/eyes2/pos_xyz', lambda *values: eye2.pos_x(values[0]))
    osc.bind(b'/tracker/eyes2/pos_xyz', lambda *values: eye2.pos_y(values[1]))

    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_x(values[0]))
    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_y(values[1]))
    osc.bind(b'/tracker/head/pos_xyz', lambda *values: camera.pos_z(values[2]))

    # routes tracking duo (p1 direction, p2 vitesse)
    if p1_eye1 is not None:
        osc.bind(b'/tracker/p1/eyes1/pos_xyz', lambda *values: p1_eye1.pos_x(values[0]))
        osc.bind(b'/tracker/p1/eyes1/pos_xyz', lambda *values: p1_eye1.pos_y(values[1]))
    if p1_eye2 is not None:
        osc.bind(b'/tracker/p1/eyes2/pos_xyz', lambda *values: p1_eye2.pos_x(values[0]))
        osc.bind(b'/tracker/p1/eyes2/pos_xyz', lambda *values: p1_eye2.pos_y(values[1]))
    if p1_camera is not None:
        osc.bind(b'/tracker/p1/head/pos_xyz', lambda *values: p1_camera.pos_x(values[0]))
        osc.bind(b'/tracker/p1/head/pos_xyz', lambda *values: p1_camera.pos_y(values[1]))
        osc.bind(b'/tracker/p1/head/pos_xyz', lambda *values: p1_camera.pos_z(values[2]))

    if p2_camera is not None:
        osc.bind(b'/tracker/p2/head/pos_xyz', lambda *values: p2_camera.pos_x(values[0]))
        osc.bind(b'/tracker/p2/head/pos_xyz', lambda *values: p2_camera.pos_y(values[1]))
        osc.bind(b'/tracker/p2/head/pos_xyz', lambda *values: p2_camera.pos_z(values[2]))

    if fire_trigger is not None:
        osc.bind(b'/tracker/fire', lambda *values: fire_trigger.trigger(values[0] if values else 1))

    if z_neutral is not None:
        osc.bind(b'/tracker/z_neutral', lambda *values: z_neutral.update(values[0] if values else DEFAULT_Z_NEUTRAL))