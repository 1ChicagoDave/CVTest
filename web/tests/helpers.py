"""Shared test helpers: synthetic camera frames and a fake Pixelblaze."""
import cv2
import numpy as np

from automap.detection import FRAME_HEIGHT, FRAME_WIDTH


def make_jpeg(led_xy=None, radius=14, brightness=255, noise=0):
    """Build a synthetic camera frame as JPEG bytes.

    led_xy: (x, y) of a lit LED to draw as a filled red circle, or None for a
    dark background frame. `noise` adds mild gaussian noise to mimic a real
    sensor (background subtraction should remove it).
    """
    img = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), np.uint8)
    if noise:
        img = cv2.add(img, np.random.normal(0, noise, img.shape).astype(np.uint8))
    if led_xy is not None:
        # Red channel is what the detector uses (BGR -> index 2).
        cv2.circle(img, led_xy, radius, (0, 0, brightness), -1)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


class FakePixelblaze:
    """In-memory stand-in for PixelblazeController."""

    def __init__(self, count):
        self._count = count
        self.current = -1
        self.history = []

    def pixel_count(self):
        return self._count

    def light(self, pixel):
        self.current = pixel
        self.history.append(pixel)

    def all_off(self):
        self.light(-1)

    def close(self):
        pass
