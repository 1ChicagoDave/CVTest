"""Camera-independent LED detection.

This is the same algorithm CVTest.py uses, extracted so it can run on frames
that arrive over a WebSocket (decoded from JPEG) instead of being pulled from
cv2.VideoCapture. The numeric parameters and the threshold/contour pipeline are
kept identical to the desktop tool so results match.

Note: the area thresholds (min_area / max_area) are tuned for 640x480 frames,
so the browser front-end downscales captures to that size before sending.
"""
import cv2
import numpy as np

# Default frame size the detector is tuned for (matches CVTest.py).
FRAME_WIDTH = 640
FRAME_HEIGHT = 480


def frame_to_grayscale(jpeg_bytes: bytes, sigma: float = 2.25) -> np.ndarray:
    """Decode a JPEG frame and turn it into the blurred, single-channel image
    the detector works on.

    Mirrors CVTest.get_frame: take the red channel only (better than perceptual
    luma for this purpose) and Gaussian blur to suppress video noise.
    """
    arr = np.frombuffer(jpeg_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # BGR
    if img is None:
        raise ValueError("could not decode frame as an image")
    red = img[:, :, 2]  # BGR -> red channel
    return cv2.GaussianBlur(red, (5, 5), sigma)


def estimate_threshold(grayscale: np.ndarray, threshold_pct: float) -> int:
    """Pick a brightness threshold from the frame's dynamic range.

    Identical to CVTest.estimate_threshold.
    """
    min_val, max_val, _, _ = cv2.minMaxLoc(grayscale)
    value_range = max_val - min_val
    return int(max_val - (value_range * threshold_pct))


class LedDetector:
    """Finds the lit LED in a (background, lit) pair of frames.

    threshold_pct is adaptive and persists across pixels, exactly as in the
    desktop tool, so the detector tunes itself to the scene as the run proceeds.
    """

    def __init__(
        self,
        threshold_pct: float = 0.60,
        threshold_adjust_delta: float = 0.025,
        min_area: int = 200,
        max_area: int = 2000,
        max_retries: int = 10,
    ):
        self.threshold_pct = threshold_pct
        self.threshold_adjust_delta = threshold_adjust_delta
        self.min_area = min_area
        self.max_area = max_area
        self.max_retries = max_retries

    def detect_pixel(self, background: np.ndarray, grayscale: np.ndarray):
        """Run the full retry / threshold-adjust loop on a fixed pair of frames.

        Because the frames don't change between retries (in the web flow we only
        capture once per pixel), the whole retry loop is pure CPU work here.

        Returns (center, info) where center is an (x, y) tuple or None if the
        LED couldn't be located, and info is a small dict of diagnostics.
        """
        # Background subtraction: removes ambient light and always-on LEDs
        # (e.g. the Pixelblaze's own power indicator).
        diff = cv2.absdiff(background, grayscale)

        for retry in range(self.max_retries):
            thresh = estimate_threshold(grayscale, self.threshold_pct)
            _, binary = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)

            contours, _ = cv2.findContours(
                binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            if len(contours) == 0:
                # Nothing bright enough; lower the bar and retry.
                self.threshold_pct += self.threshold_adjust_delta
                continue

            largest = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest)

            if area < self.min_area:
                self.threshold_pct += self.threshold_adjust_delta
                continue
            if area > self.max_area:
                # Too big: likely a reflection or ambient bloom. Tighten.
                self.threshold_pct -= self.threshold_adjust_delta
                continue

            x, y, w, h = cv2.boundingRect(largest)
            short, long = min(w, h), max(w, h)
            aspect_ratio = float(short) / float(long) if long else 0.0
            if aspect_ratio < 0.5:
                # Long and skinny -> probably not a single LED blob.
                self.threshold_pct += self.threshold_adjust_delta
                continue

            center = (x + w // 2, y + h // 2)
            return center, {"area": float(area), "retries": retry, "threshold": thresh}

        return None, {"area": None, "retries": self.max_retries, "threshold": None}


def rotate_led_centers(led_centers, x_center, y_center, radians):
    """Rotate a list of [x, y] centers about (x_center, y_center).

    Identical to CVTest.rotate_led_centers, including preserving [-1, -1] markers
    for LEDs that couldn't be found.
    """
    new_centers = []
    for led in led_centers:
        if led[0] < 0 and led[1] < 0:
            new_centers.append([-1, -1])
            continue
        x = led[0] - x_center
        y = led[1] - y_center
        x_new = int(x * np.cos(radians) - y * np.sin(radians) + x_center)
        y_new = int(x * np.sin(radians) + y * np.cos(radians) + y_center)
        new_centers.append([x_new, y_new])
    return new_centers


def map_center(led_centers):
    """Average of all *found* LED centers (ignoring [-1, -1] markers), used as
    the pivot for rotation. Matches the center computation in CVTest.main.
    """
    x_total = y_total = 0
    count = 0
    for led in led_centers:
        if led[0] < 0 and led[1] < 0:
            continue
        x_total += led[0]
        y_total += led[1]
        count += 1
    if count == 0:
        return 0, 0
    return x_total // count, y_total // count
