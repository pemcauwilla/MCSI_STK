import math
import socket
import sys
import time
import serial
import os
import subprocess
import argparse
from serial.tools import list_ports

from classes.sensorStates import *
from classes.KartState import *
from classes.actionDetector import *
from classes.config import *
from STK_Sender import *
from oscpy.server import OSCThreadServer

GREEN = '\033[92m'
WHITE = '\x1b[0m'
BLUE = '\033[94m'
YELLOW = '\033[93m'
RED = '\033[91m'

# config reseau
STK_SERVER_ADDRESS = ('localhost', 6006)
OSC_LISTEN_IP = '0.0.0.0'
OSC_LISTEN_PORT = 8000

# config liaison serie arduino
BAUDRATE = 115200
TIMEOUT = 0.1
SENSOR_TIMEOUT = 0.50

# parametres commande
EXPO = 2.0
LOOP_HZ = 120


def main():
    parser = argparse.ArgumentParser(description="STK Controller Client")
    parser.add_argument('-d', '--debug', action='store_true', help="Mode debug")
    parser.add_argument(
        '--steering',
        choices=['face', 'position'],
        default='face',
        help="Direction: 'face' (inclinaison) ou 'position' (position x)",
    )
    parser.add_argument(
        '--mode',
        choices=['auto', 'duo', 'solo'],
        default='auto',
        help="Mode: auto, duo ou solo",
    )
    parser.add_argument(
        '--color',
        choices=['red', 'green', 'blue', 'yellow', 'orange', 'custom', 'off'],
        default=COLOR_PRESET,
        help="Couleur de detection objet/tir",
    )
    args = parser.parse_args()
    debug = args.debug
    steering_mode = args.steering
    play_mode = args.mode
    color_mode = args.color

    sender = STKSender(STK_SERVER_ADDRESS, debug=debug)
    kart = KartState(sender, debug=debug)
    gyr = Vector3State()
    shake_detector = ShakeDetector()
    camera = CameraState()
    eye1 = EyesValues()
    eye2 = EyesValues()
    p1_eye1 = EyesValues()
    p1_eye2 = EyesValues()
    p1_camera = CameraState()
    p2_camera = CameraState()
    fire_trigger = TriggerState(cooldown=1.4)
    z_neutral_state = FloatState(default=DEFAULT_Z_NEUTRAL)
    osc = OSCThreadServer()
    steering_pwm = ContinuousCommand()

    osc.listen(address=OSC_LISTEN_IP, port=OSC_LISTEN_PORT, default=True)

    bind_all(
        osc,
        gyr,
        eye1,
        eye2,
        camera,
        p1_eye1=p1_eye1,
        p1_eye2=p1_eye2,
        p1_camera=p1_camera,
        p2_camera=p2_camera,
        fire_trigger=fire_trigger,
        z_neutral=z_neutral_state,
    )
    print()
    color_tag = f", Objet: {color_mode.upper()}" if color_mode != "off" else ""
    print(f'STK client demarre (Mode: {play_mode.upper()}, Direction: {steering_mode.upper()}{color_tag})')

    base_dir = os.path.dirname(os.path.abspath(__file__))
    extra_flags = {}
    if sys.platform == 'win32' and hasattr(subprocess, 'CREATE_NEW_CONSOLE'):
        extra_flags['creationflags'] = subprocess.CREATE_NEW_CONSOLE

    serveur = None
    # verifie si le serveur udp tourne deja
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    serveur_deja_actif = False
    try:
        test_sock.bind(STK_SERVER_ADDRESS)
        test_sock.close()
    except OSError:
        serveur_deja_actif = True

    if not serveur_deja_actif:
        if sys.platform.startswith('linux') and hasattr(os, 'geteuid') and os.geteuid() != 0:
            print(YELLOW + "\n[INFO] STK_input_server requiert root/sudo sous Linux pour emuler le clavier." + WHITE)
            print(YELLOW + "Lancer: sudo python STK_input_server.py dans un autre terminal si besoin.\n" + WHITE)
        cmd_serveur = [sys.executable, os.path.join(base_dir, 'STK_input_server.py')]
        if debug:
            cmd_serveur.append('-d')
        serveur = subprocess.Popen(cmd_serveur, cwd=base_dir, **extra_flags)
        time.sleep(1.0)
    else:
        print("Serveur UDP deja present sur port 6006.")

    cmd_tracking = [
        sys.executable,
        os.path.join(base_dir, 'face_tracking.py'),
        '--mode', play_mode,
        '--steering', steering_mode,
        '--color', color_mode,
    ]
    tracking = subprocess.Popen(cmd_tracking, cwd=base_dir, **extra_flags)

    muscle_contracte = False
    angle_repos = None
    debut_calib = None
    echantillons = []
    nitro_jusqua = None

    # recherche du port serie arduino
    serialPort = None
    port_detecte = None
    for p in list_ports.comports():
        if p.vid == 0x2341:
            port_detecte = p.device
            break

    if port_detecte is None:
        print(YELLOW + "Pas de carte Arduino detectee, poursuite en vision seule." + WHITE)
    else:
        try:
            serialPort = serial.Serial(port_detecte, BAUDRATE, timeout=0.01)
            serialPort.reset_input_buffer()
            print("Port Arduino:", port_detecte)
        except serial.SerialException as e:
            print(YELLOW + f"Port {port_detecte} indisponible ({e}), poursuite sans." + WHITE)
            serialPort = None

    try:
        while True:
            now = time.time()

            # arret nitro apres pulse
            if nitro_jusqua and now >= nitro_jusqua:
                kart.set_nitro(False)
                nitro_jusqua = None

            # tir si declenche par carte
            if fire_trigger.consume():
                kart.fire()

            # secousse gyro smartphone
            gx, gy, gz, gage = gyr.snapshot()
            if gx is not None and gage <= SENSOR_TIMEOUT:
                if shake_detector.updategyr(gx, now):
                    kart.rescue()

            # selection auto/duo/solo
            p1_e1x, p1_e1y, p1_age1 = p1_eye1.snapshot()
            p1_e2x, p1_e2y, p1_age2 = p1_eye2.snapshot()
            p2_cx, p2_cy, p2_cz, p2_cage = p2_camera.snapshot()

            is_duo_actif = (play_mode == 'duo') or (
                play_mode == 'auto' and p2_cz is not None and p2_cage <= SENSOR_TIMEOUT
            )

            # controle de la direction
            if steering_mode == 'face':
                if is_duo_actif and p1_e1x is not None and max(p1_age1, p1_age2) <= SENSOR_TIMEOUT:
                    e_x1, e_y1 = p1_e1x, p1_e1y
                    e_x2, e_y2 = p1_e2x, p1_e2y
                    yeux_ok = True
                else:
                    e_x1, e_y1, age1 = eye1.snapshot()
                    e_x2, e_y2, age2 = eye2.snapshot()
                    yeux_ok = (
                        e_x1 is not None and e_x2 is not None and max(age1, age2) <= SENSOR_TIMEOUT
                    )

                if yeux_ok:
                    angle = math.degrees(math.atan2(e_y2 - e_y1, e_x2 - e_x1))
                    if INVERT_TETE:
                        angle = -angle
                    if angle_repos is None:
                        if debut_calib is None:
                            debut_calib = now
                            role_str = "Joueur 1 (Direction)" if is_duo_actif else "Joueur Solo"
                            print(f"Calibrage: {role_str}, garder la tete droite 2s...")
                        echantillons.append(angle)
                        if now - debut_calib >= 2.0 and echantillons:
                            angle_repos = sum(echantillons) / len(echantillons)
                            print(f"Angle neutre: {angle_repos:+.1f} deg")
                        kart.set_steering('NONE')
                    else:
                        nx = normalize(angle - angle_repos, -ANGLE_TETE_MAX, ANGLE_TETE_MAX)
                        direction = zone(nx, DEAD_ZONE_X_FACE, 'LEFT', 'RIGHT')
                        niveau = intensity(nx, DEAD_ZONE_X_FACE) ** EXPO
                        if steering_pwm.pressed(niveau, now):
                            kart.set_steering(direction)
                        else:
                            kart.set_steering('NONE')
                else:
                    kart.set_steering('NONE')
            else:
                # mode position horizontale
                if is_duo_actif:
                    left_thresh_ratio = DUO_P1_LEFT_THRESHOLD
                    right_thresh_ratio = DUO_P1_RIGHT_THRESHOLD
                    e1x, e1y, a1 = p1_e1x, p1_e1y, p1_age1
                    e2x, e2y, a2 = p1_e2x, p1_e2y, p1_age2
                    c_x, c_y, c_z, c_age = p1_camera.snapshot()
                else:
                    left_thresh_ratio = SOLO_LEFT_THRESHOLD
                    right_thresh_ratio = SOLO_RIGHT_THRESHOLD
                    e1x, e1y, a1 = eye1.snapshot()
                    e2x, e2y, a2 = eye2.snapshot()
                    c_x, c_y, c_z, c_age = camera.snapshot()

                left_thresh_px = left_thresh_ratio * FRAME_WIDTH
                right_thresh_px = right_thresh_ratio * FRAME_WIDTH

                head_x = None
                if e1x is not None and e2x is not None and max(a1, a2) <= SENSOR_TIMEOUT:
                    head_x = (e1x + e2x) / 2.0
                elif c_x is not None and c_age <= SENSOR_TIMEOUT and c_z is not None and c_z > 0:
                    head_x = (FRAME_WIDTH / 2.0) + (c_x * 654.0 / c_z)

                if head_x is not None:
                    if head_x < left_thresh_px:
                        delta = left_thresh_px - head_x
                        niveau = min(1.0, max(0.0, delta / max(1.0, TURN_SPAN_PX))) ** EXPO
                        if steering_pwm.pressed(niveau, now):
                            kart.set_steering('LEFT')
                        else:
                            kart.set_steering('NONE')
                    elif head_x > right_thresh_px:
                        delta = head_x - right_thresh_px
                        niveau = min(1.0, max(0.0, delta / max(1.0, TURN_SPAN_PX))) ** EXPO
                        if steering_pwm.pressed(niveau, now):
                            kart.set_steering('RIGHT')
                        else:
                            kart.set_steering('NONE')
                    else:
                        kart.set_steering('NONE')
                else:
                    kart.set_steering('NONE')

            # acceleration et freinage par distance Z
            if is_duo_actif and p2_cz is not None and p2_cage <= SENSOR_TIMEOUT:
                cz = p2_cz
                cage = p2_cage
            else:
                cx, cy, cz, cage = camera.snapshot()

            if cz is not None and cage <= SENSOR_TIMEOUT:
                current_zn, _ = z_neutral_state.snapshot()
                delta_z = cz - current_zn
                if delta_z < -DEAD_ZONE_Z_CM:
                    kart.set_throttle('ACCELERATE')
                elif delta_z > DEAD_ZONE_Z_CM:
                    kart.set_throttle('BRAKE')
                else:
                    kart.set_throttle('NONE')
            else:
                kart.set_throttle('NONE')

            # lecture serie des capteurs arduino (vibration / emg)
            if serialPort is not None:
                ligne = serialPort.readline()
                if ligne.endswith(b'\n'):
                    nom = ligne.decode('utf-8', errors='replace').strip()
                    if nom == 'vibrationSensor':
                        kart.set_nitro(True)
                        nitro_jusqua = now + 0.15
                    elif nom == 'Contraction':
                        muscle_contracte = not muscle_contracte
                        kart.set_skidding(muscle_contracte)

            time.sleep(1.0 / LOOP_HZ)

    except KeyboardInterrupt:
        pass
    finally:
        kart.release_all()
        osc.stop()
        if serialPort is not None:
            serialPort.close()
        if serveur is not None:
            serveur.terminate()
        if tracking is not None:
            tracking.terminate()
        print()
        print('STK collab input stopped')


if __name__ == '__main__':
    main()
