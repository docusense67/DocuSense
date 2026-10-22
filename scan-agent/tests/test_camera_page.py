"""Page detection in camera frames: corner accuracy, cut-off edges, orientation, output size."""

import cv2
import numpy as np
import pytest

from scan_agent.capture.page import correct_page, detect_page, rotate_frame
from scan_agent.config import A4_PX
from scan_agent.processing.cleanup import order_corners, warp_to_page
from tests.camera_frames import camera_frame, default_page, empty_frame


@pytest.mark.parametrize("kw", [
    {},
    {"angle": 4, "tilt": 0.05},
    {"angle": -8, "tilt": 0.08, "shift": (0.02, 0.01), "size": (2600, 3000)},
    {"angle": 2, "background": 150, "noise": 6},
    {"size": (3300, 4300), "angle": -3, "tilt": 0.02},
], ids=["upright", "rotated", "strong-keystone", "light-bg-noisy", "high-res"])
def test_corners_found_to_a_pixel(kw):
    frame, truth = camera_frame(default_page(), **kw)
    found = detect_page(frame)
    assert found.found and not found.cut_off and not found.sideways
    assert np.abs(found.corners - truth).max() < 1.5  # px in the full-resolution frame
    assert found.skew_deg == pytest.approx(kw.get("angle", 0), abs=0.3)
    area = cv2.contourArea(truth.reshape(-1, 1, 2)) / (frame.shape[0] * frame.shape[1])
    assert found.coverage == pytest.approx(area, abs=0.005)
    assert found.contrast > 60  # page vs background; MIN_PAGE_CONTRAST is 40


def test_effective_dpi_is_measured_across_the_sheet():
    page = default_page()
    for dpi in (200, 300):
        height = dpi * 297 / 25.4  # A4 height in frame pixels
        frame, _ = camera_frame(page, size=(round(height * 0.85), round(height / 0.9)),
                                page_height=0.9)
        assert detect_page(frame).effective_dpi == pytest.approx(dpi, rel=0.01)


def test_corrected_page_is_a4_at_300_dpi_and_flat():
    frame, _ = camera_frame(default_page(), angle=5, tilt=0.06)
    page, found = correct_page(frame)
    assert page.shape == (A4_PX[1], A4_PX[0])
    # The warped page lines up with the original: same print in the same places.
    reference = cv2.resize(default_page(), A4_PX)
    small = (slice(None, None, 8), slice(None, None, 8))
    corr = np.corrcoef(page[small].ravel(), reference[small].ravel())[0, 1]
    assert corr > 0.9
    assert page[:40].mean() > 200 and page[-40:].mean() > 200  # no dark background left


def test_keep_aspect_keeps_a_letter_sheet_unstretched():
    letter = cv2.resize(default_page(), (2550, 3300))  # 8.5 x 11 in at 300 DPI
    frame, _ = camera_frame(letter)
    page, _ = correct_page(frame, keep_aspect=True)
    assert page.shape[1] == A4_PX[0]
    assert page.shape[0] == pytest.approx(A4_PX[0] * 11 / 8.5, rel=0.01)
    assert correct_page(frame)[0].shape == (A4_PX[1], A4_PX[0])  # default: A4


def test_sideways_page_and_mount_rotation():
    """A 16:9 camera sees A4 best with the sheet across the frame; ADGVS_CAMERA_ROTATE
    turns the frame so the page is upright."""
    frame, _ = camera_frame(default_page(), size=(4608, 2592), page_height=1.3, angle=90)
    raw = detect_page(frame)
    assert raw.found and raw.sideways  # warned, and still mapped to a portrait page
    assert raw.effective_dpi > 280
    upright = detect_page(rotate_frame(frame, 270))
    assert upright.found and not upright.sideways
    assert abs(upright.skew_deg) < 0.3
    page, _ = correct_page(frame, rotate=270)
    reference = cv2.resize(default_page(), A4_PX)
    small = (slice(None, None, 8), slice(None, None, 8))
    assert np.corrcoef(page[small].ravel(), reference[small].ravel())[0, 1] > 0.9
    # Rotated the wrong way the page comes out upside down: same shape, poor match.
    wrong, _ = correct_page(frame, rotate=90)
    assert np.corrcoef(wrong[small].ravel(), reference[small].ravel())[0, 1] < 0.5


def test_rotation_must_be_a_quarter_turn():
    with pytest.raises(ValueError):
        rotate_frame(np.zeros((4, 4), np.uint8), 45)
    assert rotate_frame(np.zeros((4, 6), np.uint8), 90).shape == (6, 4)


def test_no_page_in_an_empty_or_unevenly_lit_compartment():
    assert not detect_page(empty_frame()).found
    ramp = np.linspace(30, 75, 2200, dtype=np.float32)[None, :].repeat(2900, axis=0)
    uneven = np.clip(ramp + np.random.default_rng(1).normal(0, 3, ramp.shape), 0, 255)
    assert not detect_page(uneven.astype(np.uint8)).found
    page, found = correct_page(empty_frame())
    assert not found.found and found.coverage == 0.0
    assert page.shape[1] == A4_PX[0]  # the frame as seen, for the reviewer


def test_a_tiny_bright_object_is_not_a_page():
    frame = empty_frame()
    frame[1000:1300, 900:1100] = 230  # a label, a finger, a corner of paper: < 10%
    assert not detect_page(frame).found


def test_order_corners_handles_any_rotation_and_warp_respects_given_order():
    square = np.array([[0, 0], [100, 0], [100, 140], [0, 140]], np.float32)
    for angle in (0, 30, 44, -40):
        a = np.radians(angle)
        rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        pts = square @ rot.T + 500
        shuffled = pts[[2, 0, 3, 1]]
        assert np.allclose(order_corners(shuffled), pts, atol=1e-3)
    image = np.zeros((300, 300), np.uint8)
    image[:150] = 255  # top half white
    corners = np.array([[0, 0], [299, 0], [299, 299], [0, 299]], np.float32)
    assert warp_to_page(image, corners, (60, 60))[:10].mean() > 200
    flipped = warp_to_page(image, corners[[2, 3, 0, 1]], (60, 60), ordered=True)
    assert flipped[:10].mean() < 50  # upside down, as asked
