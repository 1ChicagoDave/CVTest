"""Unit tests for the extracted detection logic."""
import math

import numpy as np
import pytest

from automap.detection import (
    LedDetector,
    frame_to_grayscale,
    map_center,
    rotate_led_centers,
)
from .helpers import make_jpeg


def test_frame_to_grayscale_shape_and_red_channel():
    gray = frame_to_grayscale(make_jpeg(led_xy=(320, 240)))
    assert gray.ndim == 2  # single channel
    assert gray.shape == (480, 640)
    # The lit (red) blob should be the brightest region.
    assert gray.max() > 100


def test_detect_pixel_finds_known_center():
    detector = LedDetector()
    background = frame_to_grayscale(make_jpeg(led_xy=None))
    lit = frame_to_grayscale(make_jpeg(led_xy=(200, 150)))
    center, info = detector.detect_pixel(background, lit)
    assert center is not None
    # Blur + thresholding shifts the centroid slightly; allow a few px.
    assert abs(center[0] - 200) <= 4
    assert abs(center[1] - 150) <= 4
    assert info["area"] is not None


def test_detect_pixel_returns_none_when_dark():
    detector = LedDetector()
    background = frame_to_grayscale(make_jpeg(led_xy=None))
    lit = frame_to_grayscale(make_jpeg(led_xy=None))  # nothing lit
    center, info = detector.detect_pixel(background, lit)
    assert center is None
    assert info["retries"] == detector.max_retries


def test_detect_survives_background_noise_and_always_on_led():
    # An always-on LED present in BOTH frames must be subtracted out, and the
    # real (changed) LED still found.
    detector = LedDetector()
    always_on = (500, 400)
    bg = frame_to_grayscale(make_jpeg(led_xy=always_on, noise=4))
    # lit frame has the always-on LED plus the target one.
    lit_img = make_jpeg(led_xy=always_on, noise=4)
    # draw the target by compositing a second frame's blob via detection input:
    lit = frame_to_grayscale(make_jpeg(led_xy=(150, 300), noise=4))
    # Put the always-on blob into the lit frame too so it cancels in absdiff.
    lit = np.maximum(lit, frame_to_grayscale(make_jpeg(led_xy=always_on)))
    center, _ = detector.detect_pixel(bg, lit)
    assert center is not None
    assert abs(center[0] - 150) <= 6
    assert abs(center[1] - 300) <= 6


def test_rotate_preserves_missing_markers():
    rotated = rotate_led_centers([[10, 0], [-1, -1]], 0, 0, math.pi / 2)
    assert rotated[1] == [-1, -1]
    # (10,0) rotated 90deg about origin -> ~(0,10)
    assert abs(rotated[0][0] - 0) <= 1
    assert abs(rotated[0][1] - 10) <= 1


def test_rotate_full_turn_is_identity():
    pts = [[10, 20], [30, 40]]
    rotated = rotate_led_centers(pts, 15, 30, 2 * math.pi)
    for (ox, oy), (nx, ny) in zip(pts, rotated):
        assert abs(ox - nx) <= 1 and abs(oy - ny) <= 1


def test_map_center_ignores_missing():
    assert map_center([[0, 0], [10, 10], [-1, -1]]) == (5, 5)
    assert map_center([[-1, -1]]) == (0, 0)
