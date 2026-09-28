import sys
import os
import time
import math
from typing import Tuple, Union
import numpy as np

from oscpy.client import OSCClient
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

fl = 654.0
screen_height = 21.0
user_ipd = 3.0

if len(sys.argv) >= 2:
    try:
        user_ipd = float(sys.argv[1])
    except ValueError:
        pass

address = 'localhost'
port = 8000
clientOSC = OSCClient(address, port)

cap = cv2.VideoCapture(0)
first_time = time.time() * 1000.0
frame_width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
frame_height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)


class TrackingResults:
    tracking_results = None

    def get_result(self, result: vision.FaceDetectorResult, output_image: mp.Image, timestamp_ms: int):
        self.tracking_results = result


res = TrackingResults()
model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blaze_face_short_range.tflite')
base_options = python.BaseOptions(model_asset_path=model_path)
options = vision.FaceDetectorOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.LIVE_STREAM,
    result_callback=res.get_result,
)
detector = vision.FaceDetector.create_from_options(options)

MARGIN = 10
ROW_SIZE = 10
FONT_SIZE = 1
FONT_THICKNESS = 2
TEXT_COLOR = (255, 0, 0)


def _normalized_to_pixel_coordinates(
    normalized_x: float, normalized_y: float, image_width: int, image_height: int
) -> Union[None, Tuple[int, int]]:
    def is_valid(value: float) -> bool:
        return (value > 0 or math.isclose(0, value)) and (value < 1 or math.isclose(1, value))

    if not (is_valid(normalized_x) and is_valid(normalized_y)):
        return None
    x_px = min(math.floor(normalized_x * image_width), image_width - 1)
    y_px = min(math.floor(normalized_y * image_height), image_height - 1)
    return x_px, y_px


def visualize(image, detection_result) -> np.ndarray:
    annotated_image = image.copy()
    height, width, _ = image.shape

    for detection in detection_result.detections:
        bbox = detection.bounding_box
        start_point = bbox.origin_x, bbox.origin_y
        end_point = bbox.origin_x + bbox.width, bbox.origin_y + bbox.height
        cv2.rectangle(annotated_image, start_point, end_point, TEXT_COLOR, 3)

        for i in range(min(2, len(detection.keypoints))):
            keypoint = detection.keypoints[i]
            keypoint_px = _normalized_to_pixel_coordinates(keypoint.x, keypoint.y, width, height)
            if keypoint_px is not None:
                cv2.circle(annotated_image, keypoint_px, 2, (0, 255, 0), 2)

        if len(detection.keypoints) >= 2:
            eye1 = _normalized_to_pixel_coordinates(detection.keypoints[0].x, detection.keypoints[0].y, width, height)
            eye2 = _normalized_to_pixel_coordinates(detection.keypoints[1].x, detection.keypoints[1].y, width, height)
            if eye1 is not None and eye2 is not None:
                center_x = int((eye1[0] + eye2[0]) / 2)
                center_y = int((eye1[1] + eye2[1]) / 2)
                cv2.circle(annotated_image, (center_x, center_y), 3, (0, 0, 255), -1)

        category = detection.categories[0]
        category_name = category.category_name or ''
        probability = round(category.score, 2)
        result_text = f'{category_name} ({probability})'
        text_location = (MARGIN + bbox.origin_x, MARGIN + ROW_SIZE + bbox.origin_y)
        cv2.putText(annotated_image, result_text, text_location, cv2.FONT_HERSHEY_PLAIN,
                    FONT_SIZE, TEXT_COLOR, FONT_THICKNESS)

    return annotated_image


def compute3DPos(ibe_x, ibe_y, rec_ipd):
    z = (fl * user_ipd * 2) / max(1.0, rec_ipd)
    cx = frame_width / 2.0
    cy = frame_height / 2.0
    x = ((ibe_x - cx) * z) / fl
    y = ((ibe_y - cy) * z) / fl
    if screen_height > 0:
        y = y - (screen_height / 2.0)
    return (x, y, z)


def runtracking():
    print("Tracking demarre (ESC pour quitter)...")
    while True:
        time.sleep(0.01)
        ret, img_bgr = cap.read()
        if not ret or img_bgr is None:
            continue

        frame_timestamp_ms = int(time.time() * 1000 - first_time)
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
        detector.detect_async(mp_image, frame_timestamp_ms)

        if res.tracking_results is not None and res.tracking_results.detections:
            biggest_face = max(
                res.tracking_results.detections,
                key=lambda det: det.bounding_box.width * det.bounding_box.height,
            )

            if len(biggest_face.keypoints) >= 2:
                eye1 = biggest_face.keypoints[0]
                eye2 = biggest_face.keypoints[1]

                eye1_px = _normalized_to_pixel_coordinates(eye1.x, eye1.y, int(frame_width), int(frame_height))
                eye2_px = _normalized_to_pixel_coordinates(eye2.x, eye2.y, int(frame_width), int(frame_height))

                if eye1_px is not None and eye2_px is not None:
                    eye_center_x = int((eye1_px[0] + eye2_px[0]) / 2)
                    eye_center_y = int((eye1_px[1] + eye2_px[1]) / 2)
                    distance_ipd = math.hypot(eye2_px[0] - eye1_px[0], eye2_px[1] - eye1_px[1])
                    pos_x, pos_y, pos_z = compute3DPos(eye_center_x, eye_center_y, distance_ipd)
                    clientOSC.send_message(b'/tracker/head/pos_xyz', [pos_x, pos_y, pos_z])

            annotated_image = mp_image.numpy_view()
            annotated_image = visualize(annotated_image, res.tracking_results)
            cv2.imshow('Tracking', cv2.cvtColor(annotated_image, cv2.COLOR_RGB2BGR))

        k = cv2.waitKey(20) & 0xFF
        if k == 27:
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    runtracking()