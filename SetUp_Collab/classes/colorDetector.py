import time
from typing import Optional, Tuple, List, Dict
import cv2
import numpy as np


class ColorCardDetector:
    # presets de couleur (plages hsv opencv)
    PRESETS: Dict[str, List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]] = {
        "red": [
            ((0, 100, 70), (10, 255, 255)),
            ((168, 100, 70), (180, 255, 255)),
        ],
        "green": [
            ((36, 70, 60), (86, 255, 255)),
        ],
        "blue": [
            ((95, 90, 60), (135, 255, 255)),
        ],
        "yellow": [
            ((20, 100, 80), (35, 255, 255)),
        ],
        "orange": [
            ((10, 110, 80), (22, 255, 255)),
        ],
    }

    DISPLAY_COLORS = {
        "red": (0, 0, 255),
        "green": (0, 255, 0),
        "blue": (255, 120, 0),
        "yellow": (0, 255, 255),
        "orange": (0, 140, 255),
        "custom": (255, 0, 255),
    }

    def __init__(
        self,
        enabled: bool = True,
        preset: str = "red",
        custom_ranges: Optional[List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]] = None,
        min_area: int = 300,
        max_area: Optional[int] = 45000,
        max_aspect_ratio: float = 3.5,
        min_solidity: float = 0.65,
        min_extent: float = 0.35,
        cooldown_seconds: float = 1.2,
        pulse_duration: float = 0.20,
        confirm_frames: int = 2,
    ):
        self.enabled = enabled
        self.preset = preset.lower()
        self.custom_ranges = custom_ranges
        self.min_area = min_area
        self.max_area = max_area
        self.max_aspect_ratio = max_aspect_ratio
        self.min_solidity = min_solidity
        self.min_extent = min_extent
        self.cooldown_seconds = cooldown_seconds
        self.pulse_duration = pulse_duration
        self.confirm_frames = confirm_frames

        self._kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

        self.last_trigger_time: float = -999.0
        self.trigger_until: float = -999.0
        self.consecutive_detections: int = 0
        self.was_detected: bool = False
        self.last_detected_bbox: Optional[Tuple[int, int, int, int]] = None

        self.calibration_message: Optional[str] = None
        self.calibration_msg_until: float = 0.0

    def get_hsv_ranges(self) -> List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]:
        if self.preset == "custom" and self.custom_ranges:
            return self.custom_ranges
        if self.preset in self.PRESETS:
            return self.PRESETS[self.preset]
        return self.PRESETS["red"]

    def set_custom_range(self, lower: Tuple[int, int, int], upper: Tuple[int, int, int]):
        self.preset = "custom"
        self.custom_ranges = [(lower, upper)]

    def sample_from_center(self, frame: np.ndarray, box_size: int = 80) -> Tuple[bool, str]:
        # calibrage rapide a partir de la zone centrale
        if frame is None:
            return False, "Image non disponible"

        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2
        half = box_size // 2
        y1, y2 = max(0, cy - half), min(h, cy + half)
        x1, x2 = max(0, cx - half), min(w, cx + half)

        patch = frame[y1:y2, x1:x2]
        if patch.size == 0:
            return False, "Zone invalide"

        patch_hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        valid = patch_hsv[(patch_hsv[:, :, 1] >= 45) & (patch_hsv[:, :, 2] >= 35)]
        if len(valid) < 60:
            msg = "Couleur trop faible ou zone vide"
            self.calibration_message = msg
            self.calibration_msg_until = time.time() + 3.0
            return False, msg

        med_h = int(np.median(valid[:, 0]))
        med_s = int(np.median(valid[:, 1]))
        med_v = int(np.median(valid[:, 2]))

        # rejet si trop proche de la peau
        is_skin = (med_h <= 22 or med_h >= 162) and (30 <= med_s <= 160) and (med_v >= 65)
        if is_skin:
            msg = "Couleur rejetee: trop proche de la peau"
            self.calibration_message = msg
            self.calibration_msg_until = time.time() + 4.0
            return False, msg

        s_min = max(60, int(med_s - 50))
        v_min = max(40, int(med_v - 65))
        h_tol = 14

        ranges = []
        h_low = med_h - h_tol
        h_high = med_h + h_tol
        if h_low < 0:
            ranges.append(((0, s_min, v_min), (h_high, 255, 255)))
            ranges.append(((180 + h_low, s_min, v_min), (180, 255, 255)))
        elif h_high > 180:
            ranges.append(((h_low, s_min, v_min), (180, 255, 255)))
            ranges.append(((0, s_min, v_min), (h_high - 180, 255, 255)))
        else:
            ranges.append(((h_low, s_min, v_min), (h_high, 255, 255)))

        self.custom_ranges = ranges
        self.preset = "custom"
        msg = f"Calibre: H={med_h} S={med_s} V={med_v}"
        self.calibration_message = msg
        self.calibration_msg_until = time.time() + 3.0
        return True, msg

    def reset_to_preset(self, preset_name: str = "green") -> str:
        self.preset = preset_name
        self.custom_ranges = None
        self.consecutive_detections = 0
        self.was_detected = False
        msg = f"Preset actif: {preset_name.upper()}"
        self.calibration_message = msg
        self.calibration_msg_until = time.time() + 3.0
        return msg

    def detect(
        self, frame: Optional[np.ndarray], now: Optional[float] = None
    ) -> Tuple[bool, bool, bool, Optional[Tuple[int, int, int, int]], str]:
        if frame is None or not self.enabled:
            return False, False, False, None, self.preset

        current_time = now if now is not None else time.time()

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ranges = self.get_hsv_ranges()

        combined_mask = None
        for lower, upper in ranges:
            lower_np = np.array(lower, dtype=np.uint8)
            upper_np = np.array(upper, dtype=np.uint8)
            mask = cv2.inRange(hsv, lower_np, upper_np)
            combined_mask = mask if combined_mask is None else cv2.bitwise_or(combined_mask, mask)

        if combined_mask is None:
            return False, False, False, None, self.preset

        # suppression bruit et bouchage trous
        cleaned = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, self._kernel_open, iterations=1)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, self._kernel_close, iterations=1)

        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detected = False
        bbox: Optional[Tuple[int, int, int, int]] = None
        candidates = []

        for c in contours:
            area = cv2.contourArea(c)
            if area < self.min_area:
                continue
            if self.max_area is not None and area > self.max_area:
                continue

            x, y, w, h = cv2.boundingRect(c)
            aspect = max(w, h) / max(1.0, min(w, h))
            if aspect > self.max_aspect_ratio:
                continue

            extent = area / max(1e-4, w * h)
            if extent < self.min_extent:
                continue

            hull = cv2.convexHull(c)
            hull_area = cv2.contourArea(hull)
            solidity = area / max(1e-4, hull_area)
            if solidity < self.min_solidity:
                continue

            score = solidity * extent * area
            candidates.append({"bbox": (int(x), int(y), int(w), int(h)), "score": score})

        if candidates:
            best = max(candidates, key=lambda it: it["score"])
            detected = True
            bbox = best["bbox"]
            self.consecutive_detections += 1
        else:
            self.consecutive_detections = 0

        # persistance minimale pour valider
        confirmed_detected = detected and (self.consecutive_detections >= self.confirm_frames)

        # declenchement coup par coup
        cooldown_passed = (current_time - self.last_trigger_time) >= self.cooldown_seconds
        trigger_now = False

        if confirmed_detected:
            if not self.was_detected and cooldown_passed:
                self.last_trigger_time = current_time
                self.trigger_until = current_time + self.pulse_duration
                trigger_now = True
            self.was_detected = True
            self.last_detected_bbox = bbox
        else:
            self.was_detected = False
            self.last_detected_bbox = None

        banner_active = current_time < self.trigger_until
        return trigger_now, banner_active, confirmed_detected, bbox, self.preset

    def draw_hud_overlay(
        self,
        frame: np.ndarray,
        banner_active: bool,
        detected: bool,
        bbox: Optional[Tuple[int, int, int, int]],
        show_reticle: bool = True,
    ):
        h, w = frame.shape[:2]
        display_color = self.DISPLAY_COLORS.get(self.preset, (0, 0, 255))

        # reticule central d'aide au calibrage
        if show_reticle:
            rcx, rcy = w // 2, h // 2
            half = 42
            rx1, ry1 = rcx - half, rcy - half
            rx2, ry2 = rcx + half, rcy + half
            corner = 12
            col_ret = (160, 160, 160)
            cv2.line(frame, (rx1, ry1), (rx1 + corner, ry1), col_ret, 1)
            cv2.line(frame, (rx1, ry1), (rx1, ry1 + corner), col_ret, 1)
            cv2.line(frame, (rx2, ry1), (rx2 - corner, ry1), col_ret, 1)
            cv2.line(frame, (rx2, ry1), (rx2, ry1 + corner), col_ret, 1)
            cv2.line(frame, (rx1, ry2), (rx1 + corner, ry2), col_ret, 1)
            cv2.line(frame, (rx1, ry2), (rx1, ry2 - corner), col_ret, 1)
            cv2.line(frame, (rx2, ry2), (rx2 - corner, ry2), col_ret, 1)
            cv2.line(frame, (rx2, ry2), (rx2, ry2 - corner), col_ret, 1)
            cv2.putText(
                frame, "CALIB (C)", (rx1 - 4, ry1 - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, col_ret, 1, cv2.LINE_AA
            )

        # boite objet detecte
        if detected and bbox is not None:
            bx, by, bw, bh = bbox
            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), display_color, 2)
            cv2.putText(
                frame, f"CARTE [{self.preset.upper()}]", (bx, max(20, by - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.48, display_color, 2, cv2.LINE_AA
            )

        # notification tir
        if banner_active:
            banner_w = 260
            bx1 = (w - banner_w) // 2
            bx2 = bx1 + banner_w
            cv2.rectangle(frame, (bx1, 38), (bx2, 70), (0, 0, 220), -1)
            cv2.rectangle(frame, (bx1, 38), (bx2, 70), (255, 255, 255), 2)
            cv2.putText(
                frame, "OBJET LANCE ! (FIRE)", (bx1 + 18, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA
            )

        # message de calibrage
        now = time.time()
        if self.calibration_message and now < self.calibration_msg_until:
            cv2.rectangle(frame, (10, h - 35), (w - 10, h - 8), (20, 20, 20), -1)
            cv2.putText(
                frame, self.calibration_message, (15, h - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1, cv2.LINE_AA
            )
