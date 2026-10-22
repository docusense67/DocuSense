"""Synthetic camera frames for the capture tests: a SAMPLE page photographed on a plain
compartment background, with the defects the quality check must catch."""

from functools import cache

import cv2
import numpy as np

from scan_agent.config import A4_MM

FONT = cv2.FONT_HERSHEY_SIMPLEX
WORDS = ("SAMPLE", "REPORT", "CARD", "LEARNER", "SCHOOL", "GRADE", "SECTION", "FINAL",
         "RATING", "REMARKS", "PASSED", "MATHEMATICS", "SCIENCE", "ENGLISH", "FILIPINO")


def synthetic_page(dpi: int = 300, *, lines: int = 34, paper: int = 228, ink: int = 30,
                   seed: int = 0) -> np.ndarray:
    """A4 grayscale page marked SAMPLE: a heading, text lines and a ruled table."""
    rng = np.random.default_rng(seed)
    s = dpi / 300
    w, h = round(A4_MM[0] / 25.4 * dpi), round(A4_MM[1] / 25.4 * dpi)
    page = np.full((h, w), paper, np.uint8)
    cv2.putText(page, "SAMPLE DOCUMENT - NOT VALID", (round(330 * s), round(330 * s)), FONT,
                2.2 * s, ink, max(1, round(5 * s)), cv2.LINE_AA)
    y = 520 * s
    for i in range(lines):
        words = [WORDS[k] for k in rng.integers(0, len(WORDS), 5)]
        cv2.putText(page, " ".join(words) + f" {i + 1:02d}", (round(240 * s), round(y)), FONT,
                    1.3 * s, ink, max(1, round(3 * s)), cv2.LINE_AA)
        y += 62 * s
        if i == lines // 2:  # a ruled table in the middle of the text
            top = y
            for r in range(6):
                cv2.line(page, (round(240 * s), round(top + r * 60 * s)),
                         (round(2240 * s), round(top + r * 60 * s)), ink, max(1, round(3 * s)))
            for x in (240, 900, 1600, 2240):
                cv2.line(page, (round(x * s), round(top)), (round(x * s), round(top + 300 * s)),
                         ink, max(1, round(3 * s)))
            y = top + 380 * s
        if y > h - 200 * s:
            break
    return page


@cache
def default_page() -> np.ndarray:
    page = synthetic_page()
    page.flags.writeable = False  # shared between tests
    return page


@cache
def sparse_page() -> np.ndarray:
    page = synthetic_page(lines=6, seed=1)
    page.flags.writeable = False
    return page


def empty_frame(size: tuple[int, int] = (2200, 2900), background: int = 45,
                noise: float = 3.0) -> np.ndarray:
    rng = np.random.default_rng(0)
    frame = np.full(size[::-1], background, np.float32) + rng.normal(0, noise, size[::-1])
    return np.clip(frame, 0, 255).astype(np.uint8)


def camera_frame(page: np.ndarray, *, size: tuple[int, int] = (2200, 2900),
                 page_height: float = 0.88, angle: float = 0.0, tilt: float = 0.0,
                 shift: tuple[float, float] = (0.0, 0.0), background: int = 45,
                 noise: float = 3.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Photograph `page` on a plain background: frame of `size` (w, h), the sheet
    `page_height` of the frame height, rotated by `angle` degrees, its top edge narrowed by
    `tilt` (keystone, camera not square to the sheet) and moved by `shift` (fractions of
    the frame). Returns (frame, true corners TL, TR, BR, BL in frame pixels)."""
    fw, fh = size
    ph, pw = page.shape
    height = page_height * fh
    width = height * pw / ph
    cx, cy = fw / 2 + shift[0] * fw, fh / 2 + shift[1] * fh
    inset = tilt * width / 2
    quad = np.array([[-width / 2 + inset, -height / 2], [width / 2 - inset, -height / 2],
                     [width / 2, height / 2], [-width / 2, height / 2]], np.float64)
    a = np.radians(angle)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    corners = (quad @ rot.T + [cx, cy]).astype(np.float32)
    src = np.array([[0, 0], [pw - 1, 0], [pw - 1, ph - 1], [0, ph - 1]], np.float32)
    matrix = cv2.getPerspectiveTransform(src, corners)
    frame = cv2.warpPerspective(page, matrix, (fw, fh), flags=cv2.INTER_AREA,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=background)
    if noise:
        rng = np.random.default_rng(seed)
        frame = np.clip(frame + rng.normal(0, noise, frame.shape), 0, 255).astype(np.uint8)
    return frame, corners


def blur(frame: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(frame, (0, 0), sigma)


def exposure(frame: np.ndarray, gain: float) -> np.ndarray:
    return np.clip(frame.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def glare(frame: np.ndarray, centre: tuple[int, int], radius: int) -> np.ndarray:
    """A specular highlight: a clipped white blob with a soft rim."""
    out = frame.astype(np.float32)
    yy, xx = np.ogrid[:frame.shape[0], :frame.shape[1]]
    d = np.sqrt((xx - centre[0]) ** 2 + (yy - centre[1]) ** 2) / radius
    boost = np.clip(1.6 - d, 0, 1) * 255
    return np.clip(np.maximum(out, boost), 0, 255).astype(np.uint8)
