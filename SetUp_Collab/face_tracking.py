import sys
import os
import time
import math
import argparse
from typing import Tuple, Union, Optional, List
import numpy as np

from oscpy.client import OSCClient
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classes.colorDetector import ColorCardDetector
from classes.config import (
    FRAME_WIDTH,
    FRAME_HEIGHT,
    ANGLE_TETE_MAX,
    DEAD_ZONE_ANGLE_DEG,
    DEFAULT_Z_NEUTRAL,
    DEAD_ZONE_Z_CM,
    POS_Z_MIN,
    POS_Z_MAX,
    SOLO_LEFT_THRESHOLD,
    SOLO_RIGHT_THRESHOLD,
    DUO_P1_LEFT_THRESHOLD,
    DUO_P1_RIGHT_THRESHOLD,
    COLOR_DETECTION_ENABLED,
    COLOR_PRESET,
    COLOR_MIN_AREA,
    COLOR_MAX_AREA,
    COLOR_MAX_ASPECT_RATIO,
    COLOR_MIN_SOLIDITY,
    COLOR_MIN_EXTENT,
    COLOR_COOLDOWN_SECONDS,
    COLOR_PULSE_DURATION,
    COLOR_CONFIRM_FRAMES,
)

fl = 654.0
screen_height = 21.0


def parse_arguments():
    parser = argparse.ArgumentParser(description="Tracking visage et controle STK")
    parser.add_argument(
        "ipd",
        nargs="?",
        type=float,
        default=3.0,
        help="Demi-ecart interpupillaire en cm (defaut: 3.0)",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "duo", "solo"],
        default="auto",
        help="Mode: auto, duo ou solo",
    )
    parser.add_argument(
        "--steering",
        choices=["face", "position"],
        default="face",
        help="Mode direction: face (inclinaison) ou position (translation x)",
    )
    parser.add_argument(
        "--color",
        choices=["red", "green", "blue", "yellow", "orange", "custom", "off"],
        default=COLOR_PRESET,
        help="Couleur de carte pour declencher le tir",
    )
    parser.add_argument("--color-min-area", type=int, default=COLOR_MIN_AREA)
    parser.add_argument("--color-cooldown", type=float, default=COLOR_COOLDOWN_SECONDS)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--no-gui", action="store_true")
    return parser.parse_args()


def compute3DPos(ibe_x: float, ibe_y: float, rec_ipd: float, user_ipd: float = 3.0) -> Tuple[float, float, float]:
    # projection 2d -> 3d a partir de l'ecart interpupillaire
    z = (fl * user_ipd * 2.0) / max(1.0, rec_ipd)
    cx = FRAME_WIDTH / 2.0
    cy = FRAME_HEIGHT / 2.0
    x = ((ibe_x - cx) * z) / fl
    y = ((ibe_y - cy) * z) / fl
    if screen_height > 0:
        y = y - (screen_height / 2.0)
    return (x, y, z)


def _normalized_to_pixel_coordinates(
    normalized_x: float, normalized_y: float, image_width: int, image_height: int
) -> Union[None, Tuple[int, int]]:
    def is_valid_normalized_value(value: float) -> bool:
        return (value > 0 or math.isclose(0, value)) and (value < 1 or math.isclose(1, value))

    if not (is_valid_normalized_value(normalized_x) and is_valid_normalized_value(normalized_y)):
        return None
    x_px = min(math.floor(normalized_x * image_width), image_width - 1)
    y_px = min(math.floor(normalized_y * image_height), image_height - 1)
    return x_px, y_px


class FaceData:
    def __init__(self, det, width: int, height: int, user_ipd: float = 3.0):
        self.bbox = det.bounding_box
        self.origin_x = self.bbox.origin_x
        self.origin_y = self.bbox.origin_y
        self.width = self.bbox.width
        self.height = self.bbox.height
        self.center_x_px = self.origin_x + self.width // 2
        self.center_y_px = self.origin_y + self.height // 2

        kps = det.keypoints
        self.eye1_px = None
        self.eye2_px = None
        if len(kps) >= 2:
            p0 = _normalized_to_pixel_coordinates(kps[0].x, kps[0].y, width, height)
            p1 = _normalized_to_pixel_coordinates(kps[1].x, kps[1].y, width, height)
            if p0 is not None and p1 is not None:
                # tri gauche/droite pour mode miroir
                if p0[0] <= p1[0]:
                    self.eye1_px, self.eye2_px = p0, p1
                else:
                    self.eye1_px, self.eye2_px = p1, p0

        self.nose_px = _normalized_to_pixel_coordinates(kps[2].x, kps[2].y, width, height) if len(kps) > 2 else None
        self.mouth_px = _normalized_to_pixel_coordinates(kps[3].x, kps[3].y, width, height) if len(kps) > 3 else None

        if self.eye1_px and self.eye2_px:
            dx = self.eye2_px[0] - self.eye1_px[0]
            dy = self.eye2_px[1] - self.eye1_px[1]
            self.ipd_px = math.hypot(dx, dy)
            self.eye_center_x = (self.eye1_px[0] + self.eye2_px[0]) / 2.0
            self.eye_center_y = (self.eye1_px[1] + self.eye2_px[1]) / 2.0
            self.center_x_px = int(self.eye_center_x)
            self.center_y_px = int(self.eye_center_y)
            self.pos_x, self.pos_y, self.pos_z = compute3DPos(
                self.eye_center_x, self.eye_center_y, max(1.0, self.ipd_px), user_ipd
            )
            self.angle = math.degrees(math.atan2(dy, max(1e-4, dx)))
        else:
            self.ipd_px = 0.0
            self.pos_x, self.pos_y, self.pos_z = 0.0, 0.0, 85.0
            self.angle = 0.0

    @property
    def is_valid(self) -> bool:
        return self.eye1_px is not None and self.eye2_px is not None and self.ipd_px > 10.0


class TrackingResults:
    def __init__(self):
        self.tracking_results = None

    def get_result(self, result: vision.FaceDetectorResult, output_image: mp.Image, timestamp_ms: int):
        self.tracking_results = result


def draw_hud(
    frame: np.ndarray,
    p1: Optional[FaceData],
    p2: Optional[FaceData],
    active_mode: str,
    fps: float,
    steering_mode: str = "face",
    color_detector: Optional[ColorCardDetector] = None,
    card_triggered: bool = False,
    card_detected: bool = False,
    card_bbox: Optional[Tuple[int, int, int, int]] = None,
    z_neutral: float = DEFAULT_Z_NEUTRAL,
    calib_z_msg: Optional[str] = None,
):
    h, w, _ = frame.shape

    # barre info superieure
    cv2.rectangle(frame, (0, 0), (w, 32), (18, 18, 18), -1)
    steer_tag = "Inclinaison" if steering_mode == "face" else "Position"
    mode_text = (
        f"MODE: DUO (P1: Direction [{steer_tag}] | P2: Vitesse)"
        if active_mode == "duo"
        else f"MODE: SOLO (Direction [{steer_tag}] + Vitesse)"
    )
    mode_color = (0, 255, 200) if active_mode == "duo" else (0, 255, 120)
    cv2.putText(frame, mode_text, (15, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.52, mode_color, 2, cv2.LINE_AA)
    cv2.putText(frame, f"FPS: {fps:.0f}", (w - 95, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

    # separation centrale duo
    if active_mode == "duo":
        for y_dot in range(35, h - 35, 16):
            cv2.line(frame, (w // 2, y_dot), (w // 2, y_dot + 8), (70, 70, 70), 1, cv2.LINE_AA)

    # hud direction p1
    if steering_mode == "position":
        if active_mode == "duo":
            left_line_x = int(DUO_P1_LEFT_THRESHOLD * w)
            right_line_x = int(DUO_P1_RIGHT_THRESHOLD * w)
        else:
            left_line_x = int(SOLO_LEFT_THRESHOLD * w)
            right_line_x = int(SOLO_RIGHT_THRESHOLD * w)

        if p1 is not None and p1.is_valid:
            if p1.center_x_px < left_line_x:
                left_col = (0, 255, 120)
                right_col = (0, 200, 200)
                steer_lbl = f"<-- GAUCHE ({p1.center_x_px}px)"
                steer_clr = (0, 255, 120)
            elif p1.center_x_px > right_line_x:
                left_col = (0, 200, 200)
                right_col = (0, 255, 120)
                steer_lbl = f"DROITE --> ({p1.center_x_px}px)"
                steer_clr = (0, 255, 120)
            else:
                left_col = (0, 200, 200)
                right_col = (0, 200, 200)
                steer_lbl = f"NEUTRE ({p1.center_x_px}px)"
                steer_clr = (180, 180, 180)
        else:
            left_col = (0, 200, 200)
            right_col = (0, 200, 200)
            steer_lbl = "NEUTRE"
            steer_clr = (180, 180, 180)

        cv2.line(frame, (left_line_x, 35), (left_line_x, h - 35), left_col, 2, cv2.LINE_AA)
        cv2.line(frame, (right_line_x, 35), (right_line_x, h - 35), right_col, 2, cv2.LINE_AA)
        cv2.putText(frame, "<-- GAUCHE", (max(5, left_line_x - 70), 50), cv2.FONT_HERSHEY_SIMPLEX, 0.40, left_col, 1, cv2.LINE_AA)
        cv2.putText(frame, "DROITE -->", (min(w - 75, right_line_x + 6), 50), cv2.FONT_HERSHEY_SIMPLEX, 0.40, right_col, 1, cv2.LINE_AA)
        mid_x = (left_line_x + right_line_x) // 2
        cv2.putText(frame, "NEUTRE", (mid_x - 22, h - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1, cv2.LINE_AA)

        if p1 is not None and p1.is_valid:
            bx, by, bw, bh = p1.origin_x, p1.origin_y, p1.width, p1.height
            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (255, 200, 0), 2)
            cv2.putText(frame, "P1: POSITION" if active_mode == "duo" else "SOLO", (bx, max(45, by - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 1, cv2.LINE_AA)
            cv2.circle(frame, (p1.center_x_px, p1.center_y_px), 5, (0, 255, 255), -1)
            cv2.putText(frame, steer_lbl, (bx, by + bh + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, steer_clr, 2, cv2.LINE_AA)
    else:
        if p1 is not None and p1.is_valid:
            bx, by, bw, bh = p1.origin_x, p1.origin_y, p1.width, p1.height
            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (255, 200, 0), 2)
            cv2.putText(frame, "P1: INCLINAISON" if active_mode == "duo" else "SOLO", (bx, max(45, by - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 1, cv2.LINE_AA)

            cv2.circle(frame, p1.eye1_px, 3, (0, 255, 255), -1)
            cv2.circle(frame, p1.eye2_px, 3, (0, 255, 255), -1)
            cv2.line(frame, p1.eye1_px, p1.eye2_px, (0, 255, 200), 2, cv2.LINE_AA)

            if p1.angle < -DEAD_ZONE_ANGLE_DEG:
                steer_lbl = f"<-- GAUCHE ({p1.angle:+.1f} deg)"
                steer_clr = (0, 255, 120)
            elif p1.angle > DEAD_ZONE_ANGLE_DEG:
                steer_lbl = f"DROITE --> ({p1.angle:+.1f} deg)"
                steer_clr = (0, 255, 120)
            else:
                steer_lbl = f"NEUTRE ({p1.angle:+.1f} deg)"
                steer_clr = (180, 180, 180)

            cv2.putText(frame, steer_lbl, (bx, by + bh + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, steer_clr, 2, cv2.LINE_AA)

    # hud vitesse p2 en duo
    if active_mode == "duo" and p2 is not None and p2.is_valid:
        bx, by, bw, bh = p2.origin_x, p2.origin_y, p2.width, p2.height
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (0, 165, 255), 2)
        cv2.putText(frame, f"P2: VITESSE (Ref:{z_neutral:.0f}cm)", (bx, max(45, by - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1, cv2.LINE_AA)

        cv2.circle(frame, p2.eye1_px, 3, (0, 200, 255), -1)
        cv2.circle(frame, p2.eye2_px, 3, (0, 200, 255), -1)
        if p2.nose_px:
            cv2.circle(frame, p2.nose_px, 3, (255, 255, 0), -1)

        diff_z = p2.pos_z - z_neutral
        if diff_z < -DEAD_ZONE_Z_CM:
            spd_lbl = f"ACCELERER ^ ({p2.pos_z:.0f}cm, {-diff_z:.0f}cm plus pres)"
            spd_clr = (0, 255, 120)
        elif diff_z > DEAD_ZONE_Z_CM:
            spd_lbl = f"FREINER v ({p2.pos_z:.0f}cm, {diff_z:.0f}cm plus loin)"
            spd_clr = (0, 0, 255)
        else:
            spd_lbl = f"ROUE LIBRE ({p2.pos_z:.0f}cm | Neutre)"
            spd_clr = (180, 180, 180)

        cv2.putText(frame, spd_lbl, (bx, by + bh + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, spd_clr, 2, cv2.LINE_AA)

    elif active_mode == "solo" and p1 is not None and p1.is_valid:
        diff_z = p1.pos_z - z_neutral
        if diff_z < -DEAD_ZONE_Z_CM:
            spd_lbl = f"ACCEL ({p1.pos_z:.0f}cm, {-diff_z:.0f}cm plus pres)"
            spd_clr = (0, 255, 120)
        elif diff_z > DEAD_ZONE_Z_CM:
            spd_lbl = f"FREIN ({p1.pos_z:.0f}cm, {diff_z:.0f}cm plus loin)"
            spd_clr = (0, 0, 255)
        else:
            spd_lbl = f"ROUE LIBRE ({p1.pos_z:.0f}cm | Neutre)"
            spd_clr = (180, 180, 180)

        cv2.putText(frame, spd_lbl, (p1.origin_x, p1.origin_y + p1.height + 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, spd_clr, 2, cv2.LINE_AA)

    if color_detector and color_detector.enabled:
        color_detector.draw_hud_overlay(frame, card_triggered, card_detected, card_bbox, show_reticle=True)

    if calib_z_msg:
        cv2.rectangle(frame, (10, 36), (w - 10, 68), (30, 30, 30), -1)
        cv2.rectangle(frame, (10, 36), (w - 10, 68), (0, 255, 200), 2)
        cv2.putText(frame, calib_z_msg, (18, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 255, 200), 2, cv2.LINE_AA)

    cv2.putText(
        frame,
        "ESC: Quitter | Z: Calib Distance | C: Calib Couleur | R: Reset Couleur",
        (15, h - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        (160, 160, 160),
        1,
        cv2.LINE_AA,
    )


def runtracking():
    args = parse_arguments()
    user_ipd = args.ipd
    mode = args.mode
    steering_mode = args.steering
    osc_host = args.host
    osc_port = args.port
    no_gui = args.no_gui

    color_enabled = (args.color != "off" and COLOR_DETECTION_ENABLED)
    color_detector = ColorCardDetector(
        enabled=color_enabled,
        preset=args.color,
        min_area=args.color_min_area,
        max_area=COLOR_MAX_AREA,
        max_aspect_ratio=COLOR_MAX_ASPECT_RATIO,
        min_solidity=COLOR_MIN_SOLIDITY,
        min_extent=COLOR_MIN_EXTENT,
        cooldown_seconds=args.color_cooldown,
        pulse_duration=COLOR_PULSE_DURATION,
        confirm_frames=COLOR_CONFIRM_FRAMES,
    )

    clientOSC = OSCClient(osc_host, osc_port)

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    if not cap.isOpened():
        print("Erreur: camera introuvable")
        return

    res = TrackingResults()
    model_asset_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blaze_face_short_range.tflite")
    base_options = python.BaseOptions(model_asset_path=model_asset_path)
    options = vision.FaceDetectorOptions(
        base_options=base_options,
        running_mode=vision.RunningMode.LIVE_STREAM,
        result_callback=res.get_result,
    )
    detector = vision.FaceDetector.create_from_options(options)

    first_time = time.time() * 1000.0
    prev_time = time.time()
    fps = 30.0

    z_neutral = DEFAULT_Z_NEUTRAL
    calib_z_msg = None
    calib_z_until = 0.0
    initial_z_samples = []
    initial_calib_done = False
    start_tracking_time = time.time()

    print("Tracking pret. Touche ESC pour quitter.")

    try:
        while True:
            ret, img_bgr = cap.read()
            if not ret or img_bgr is None:
                time.sleep(0.01)
                continue

            # miroir horizontal
            img_bgr = cv2.flip(img_bgr, 1)

            now = time.time()
            dt = now - prev_time
            prev_time = now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)

            # detection couleur et tir
            card_triggered, card_banner, card_detected, card_bbox, card_color = color_detector.detect(img_bgr, now)
            if card_triggered:
                clientOSC.send_message(b"/tracker/fire", [1])

            frame_timestamp_ms = int(now * 1000 - first_time)

            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
            detector.detect_async(mp_image, frame_timestamp_ms)

            valid_faces: List[FaceData] = []
            if res.tracking_results is not None and res.tracking_results.detections:
                for det in res.tracking_results.detections:
                    face = FaceData(det, FRAME_WIDTH, FRAME_HEIGHT, user_ipd)
                    if face.is_valid:
                        valid_faces.append(face)

            # tri de gauche a droite sur l'ecran
            valid_faces.sort(key=lambda f: f.center_x_px)

            p1: Optional[FaceData] = None
            p2: Optional[FaceData] = None

            if mode == "duo" or (mode == "auto" and len(valid_faces) >= 2):
                active_mode = "duo"
                if len(valid_faces) >= 2:
                    p1 = valid_faces[0]
                    p2 = valid_faces[1]
                elif len(valid_faces) == 1:
                    p1 = valid_faces[0]
            else:
                active_mode = "solo"
                if len(valid_faces) >= 1:
                    p1 = valid_faces[0]

            # calibrage Z neutre au lancement (2.5s)
            speed_target = p2 if (active_mode == "duo" and p2) else p1
            if not initial_calib_done and speed_target and speed_target.is_valid:
                initial_z_samples.append(speed_target.pos_z)
                if (now - start_tracking_time) > 2.5 and len(initial_z_samples) >= 8:
                    z_neutral = round(float(np.mean(initial_z_samples)), 1)
                    initial_calib_done = True
                    calib_z_msg = f"Distance neutre fixee a {z_neutral:.0f} cm"
                    calib_z_until = now + 2.8
                    clientOSC.send_message(b"/tracker/z_neutral", [float(z_neutral)])

            clientOSC.send_message(b"/tracker/z_neutral", [float(z_neutral)])

            if active_mode == "duo" and p1 and p2:
                # p1 direction
                clientOSC.send_message(b"/tracker/p1/eyes1/pos_xyz", [p1.eye1_px[0], p1.eye1_px[1]])
                clientOSC.send_message(b"/tracker/p1/eyes2/pos_xyz", [p1.eye2_px[0], p1.eye2_px[1]])
                clientOSC.send_message(b"/tracker/p1/head/pos_xyz", [p1.pos_x, p1.pos_y, p1.pos_z])

                # p2 vitesse
                clientOSC.send_message(b"/tracker/p2/head/pos_xyz", [p2.pos_x, p2.pos_y, p2.pos_z])
                clientOSC.send_message(b"/tracker/p2/eyes1/pos_xyz", [p2.eye1_px[0], p2.eye1_px[1]])
                clientOSC.send_message(b"/tracker/p2/eyes2/pos_xyz", [p2.eye2_px[0], p2.eye2_px[1]])

                # compatibilite
                clientOSC.send_message(b"/tracker/eyes1/pos_xyz", [p1.eye1_px[0], p1.eye1_px[1]])
                clientOSC.send_message(b"/tracker/eyes2/pos_xyz", [p1.eye2_px[0], p1.eye2_px[1]])
                clientOSC.send_message(b"/tracker/head/pos_xyz", [p2.pos_x, p2.pos_y, p2.pos_z])

            elif p1 is not None:
                # solo
                clientOSC.send_message(b"/tracker/eyes1/pos_xyz", [p1.eye1_px[0], p1.eye1_px[1]])
                clientOSC.send_message(b"/tracker/eyes2/pos_xyz", [p1.eye2_px[0], p1.eye2_px[1]])
                clientOSC.send_message(b"/tracker/head/pos_xyz", [p1.pos_x, p1.pos_y, p1.pos_z])

                clientOSC.send_message(b"/tracker/p1/eyes1/pos_xyz", [p1.eye1_px[0], p1.eye1_px[1]])
                clientOSC.send_message(b"/tracker/p1/eyes2/pos_xyz", [p1.eye2_px[0], p1.eye2_px[1]])
                clientOSC.send_message(b"/tracker/p1/head/pos_xyz", [p1.pos_x, p1.pos_y, p1.pos_z])
                clientOSC.send_message(b"/tracker/p2/head/pos_xyz", [p1.pos_x, p1.pos_y, p1.pos_z])

            if not no_gui:
                active_calib_msg = calib_z_msg if now < calib_z_until else None
                draw_hud(
                    img_bgr,
                    p1,
                    p2,
                    active_mode,
                    fps,
                    steering_mode=steering_mode,
                    color_detector=color_detector,
                    card_triggered=card_banner,
                    card_detected=card_detected,
                    card_bbox=card_bbox,
                    z_neutral=z_neutral,
                    calib_z_msg=active_calib_msg,
                )
                cv2.imshow("SuperTuxKart Controller", img_bgr)

                key = cv2.waitKey(1) & 0xFF
                if key == 27:
                    break
                elif (key == ord('c') or key == ord('C')) and color_detector.enabled:
                    ok, msg = color_detector.sample_from_center(img_bgr)
                    print(f"\n[Calib Couleur] {msg}")
                elif (key == ord('r') or key == ord('R')) and color_detector.enabled:
                    msg = color_detector.reset_to_preset(args.color)
                    print(f"\n[Reset Couleur] {msg}")
                elif key == ord('z') or key == ord('Z'):
                    if speed_target and speed_target.is_valid:
                        z_neutral = round(speed_target.pos_z, 1)
                        calib_z_msg = f"Distance neutre calibree: {z_neutral:.0f} cm"
                        calib_z_until = now + 2.8
                        clientOSC.send_message(b"/tracker/z_neutral", [float(z_neutral)])
                        print(f"\n[Calib Vitesse] {calib_z_msg}")
                    else:
                        calib_z_msg = "Joueur non detecte"
                        calib_z_until = now + 2.0

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("\nFace tracking arrete.")


if __name__ == "__main__":
    runtracking()