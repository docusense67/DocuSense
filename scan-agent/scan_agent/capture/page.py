"""Find the sheet in a camera frame and flatten it to an A4 page at 300 DPI.

The compartment camera sees the sheet on a plain, darker background. `detect_page` finds
it as the largest bright quadrilateral (Otsu on a downscaled copy, convex hull, polygon
fit, then lines fitted to the four sides for sub-pixel corners) and measures what the
quality check needs: coverage, rotation in the frame, whether an edge runs off the frame,
and the effective DPI. `correct_page` warps it flat with `cleanup.warp_to_page`, the same
helper the scanner path uses for a sheet on a dark bed, and `level_page` brings the paper
to scanner white for OCR. Pixels only: nothing here is a decision.
"""

import logging
import math
from dataclasses import dataclass

import cv2
import numpy as np

from scan_agent.config import A4_MM, A4_PX
from scan_agent.processing.cleanup import order_corners, to_gray, warp_to_page

log = logging.getLogger("scan_agent.page")

A4_IN = (A4_MM[0] / 25.4, A4_MM[1] / 25.4)  # 8.27 x 11.69 in
DETECT_MAX_SIDE = 1280  # detection runs on a copy this size: ~10 ms instead of ~200 ms
# Mean grey-level gap between the page and the background around it. Below it the
# "page" is only uneven light on an empty compartment.
MIN_PAGE_CONTRAST = 40.0
# Share of the page outline lying on the frame border above which an edge is cut off
# (a corner merely touching the border is a few pixels).
CUT_OFF_BORDER_SHARE = 0.02
# Paper grey level of a corrected camera page (a scanner gives ~240-255).
PAPER_LEVEL = 245
ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
             270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@dataclass(frozen=True)
class PageDetection:
    corners: np.ndarray | None  # TL, TR, BR, BL in frame pixels (after rotation); None = no page
    coverage: float  # page area / frame area
    cut_off: bool  # part of the sheet lies outside the frame
    skew_deg: float  # rotation of the sheet in the frame (+ = clockwise)
    effective_dpi: int  # frame pixels per inch across the sheet (assuming A4)
    contrast: float  # page minus background mean grey level
    sideways: bool = False  # the sheet lay across the frame: check ADGVS_CAMERA_ROTATE

    @property
    def found(self) -> bool:
        return self.corners is not None


NOT_FOUND = PageDetection(None, 0.0, False, 0.0, 0, 0.0)


def rotate_frame(image: np.ndarray, degrees: int) -> np.ndarray:
    """Turn the mounted camera's frame upright (clockwise degrees, multiples of 90)."""
    degrees %= 360
    if degrees not in ROTATIONS:
        raise ValueError(f"Camera rotation must be 0, 90, 180 or 270, not {degrees}")
    code = ROTATIONS[degrees]
    return image if code is None else cv2.rotate(image, code)


def _quad(hull: np.ndarray) -> np.ndarray:
    """The hull as four points: a polygon fit when one exists, else its min-area box."""
    perimeter = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(hull, eps * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype(np.float32)
    return cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)


def _side_lengths(c: np.ndarray) -> tuple[float, float]:
    width = (np.linalg.norm(c[1] - c[0]) + np.linalg.norm(c[2] - c[3])) / 2
    height = (np.linalg.norm(c[3] - c[0]) + np.linalg.norm(c[2] - c[1])) / 2
    return float(width), float(height)


def _intersect(l1: np.ndarray, l2: np.ndarray) -> np.ndarray | None:
    """Intersection of two lines given as (vx, vy, x0, y0)."""
    (vx1, vy1, x1, y1), (vx2, vy2, x2, y2) = l1, l2
    det = vx1 * vy2 - vy1 * vx2
    if abs(det) < 1e-6:
        return None
    s = ((x2 - x1) * vy2 - (y2 - y1) * vx2) / det
    return np.array([x1 + s * vx1, y1 + s * vy1], np.float32)


def _fit_sides(contour: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Corners from straight lines fitted to each side of the sheet's outline.

    The polygon fit lands on outline pixels near each corner, which smoothing rounds
    off (~3 px short at full resolution); lines fitted along the middle 80% of each side
    and intersected recover the true corners, and robust fitting ignores a dent (text or
    a finger at the edge). Outline pixels lie half a pixel inside the edge: moved out."""
    pts = contour.reshape(-1, 2).astype(np.float32)
    centre = quad.mean(axis=0)
    lines = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        side = b - a
        length = float(np.linalg.norm(side))
        if length < 10:
            return quad
        u = side / length
        normal = np.array([-u[1], u[0]], np.float32)
        along = (pts - a) @ u
        across = np.abs((pts - a) @ normal)
        near = pts[(along > 0.1 * length) & (along < 0.9 * length) & (across < 0.02 * length + 2)]
        if len(near) < 10:
            return quad
        vx, vy, x0, y0 = cv2.fitLine(near, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        outward = np.array([-vy, vx], np.float32)
        if outward @ (np.array([x0, y0], np.float32) - centre) < 0:
            outward = -outward
        lines.append(np.array([vx, vy, x0 + 0.5 * outward[0], y0 + 0.5 * outward[1]]))
    corners = [_intersect(lines[i - 1], lines[i]) for i in range(4)]
    if any(c is None for c in corners):
        return quad
    fitted = np.array(corners, np.float32)
    limit = 0.03 * float(np.linalg.norm(quad[2] - quad[0]))
    if np.linalg.norm(fitted - quad, axis=1).max() > limit:
        return quad  # the outline is not four straight sides: keep the polygon
    return fitted


def detect_page(gray: np.ndarray, *, min_coverage: float = 0.10,
                max_side: int = DETECT_MAX_SIDE, refine: bool = True) -> PageDetection:
    """Locate the sheet in an upright grayscale frame. Robust to perspective, rotation,
    text on the page and noise; needs a background darker than the paper. `refine`
    fits the sides as lines (sub-pixel corners); the stillness check skips it."""
    gray = to_gray(gray)
    h, w = gray.shape
    scale = min(1.0, max_side / max(h, w))
    small = gray if scale == 1.0 else cv2.resize(gray, (round(w * scale), round(h * scale)),
                                                 interpolation=cv2.INTER_AREA)
    sh, sw = small.shape
    smooth = cv2.GaussianBlur(small, (5, 5), 0)
    _, bright = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return NOT_FOUND
    sheet = max(contours, key=cv2.contourArea)
    if cv2.contourArea(sheet) < min_coverage * sw * sh:
        return NOT_FOUND
    hull = cv2.convexHull(sheet)
    quad = order_corners(_quad(hull))

    region = np.zeros_like(small)
    cv2.fillConvexPoly(region, np.round(quad).astype(np.int32), 255)
    inside = region > 0
    outside_share = 1.0 - inside.mean()
    contrast = float(smooth[inside].mean() - smooth[~inside].mean()) if outside_share > 0.02 \
        else 255.0  # the sheet fills the frame: no background to compare with
    if contrast < MIN_PAGE_CONTRAST:
        return NOT_FOUND

    # Cut off: the bright sheet runs along the frame border (Otsu mask, not the fitted
    # quad, which may have been extrapolated), or a fitted corner lies outside the frame.
    sheet_mask = np.zeros_like(small)
    cv2.drawContours(sheet_mask, [hull], -1, 255, cv2.FILLED)
    on_border = (np.count_nonzero(sheet_mask[0]) + np.count_nonzero(sheet_mask[-1])
                 + np.count_nonzero(sheet_mask[:, 0]) + np.count_nonzero(sheet_mask[:, -1]))
    perimeter = cv2.arcLength(quad.reshape(-1, 1, 2), True)
    if refine:
        quad = _fit_sides(sheet, quad)
    outside = bool(((quad < -1) | (quad > np.array([sw, sh]))).any())
    cut_off = bool(outside or on_border > CUT_OFF_BORDER_SHARE * perimeter)

    # Pixel centres -> pixel edges when mapping back to the full-resolution frame.
    corners = (quad + 0.5) / scale - 0.5
    top, bottom = corners[1] - corners[0], corners[2] - corners[3]
    skew = math.degrees((math.atan2(top[1], top[0]) + math.atan2(bottom[1], bottom[0])) / 2)
    width, height = _side_lengths(corners)
    sideways = bool(width > height)
    if sideways:  # portrait page lying across the upright frame: take its top as the right
        corners = np.roll(corners, -1, axis=0)
        width, height = height, width
    coverage = float(cv2.contourArea(corners.reshape(-1, 1, 2)) / (w * h))
    dpi = round(min(width / A4_IN[0], height / A4_IN[1]))
    return PageDetection(corners=corners, coverage=min(coverage, 1.0), cut_off=cut_off,
                         skew_deg=round(skew, 2), effective_dpi=dpi, contrast=round(contrast, 1),
                         sideways=sideways)


def page_size(detection: PageDetection, keep_aspect: bool = False) -> tuple[int, int]:
    """Output (w, h): A4 at 300 DPI, or 300 DPI-wide with the measured aspect ratio."""
    if not keep_aspect or not detection.found:
        return A4_PX
    width, height = _side_lengths(detection.corners)
    return A4_PX[0], max(1, round(A4_PX[0] * height / width))


def level_page(page: np.ndarray, paper: int = PAPER_LEVEL) -> np.ndarray:
    """Brighten a camera page so its paper sits at `paper`, like a scanner's output.

    The OCR steps assume white paper: `cleanup.remove_rules` paints erased rules 255, and
    on grey paper (a dim photo, paper at ~140) those white lines made Otsu split paper from
    rules instead of ink from paper, so the grades table read nothing. One global gain
    (paper = 95th percentile), never a darkening and at most 4x, so photos, seals and
    shading keep their look for the reviewer."""
    sample = page[::4, ::4]
    level = float(np.percentile(sample, 95))
    gain = min(4.0, paper / max(level, 1.0))
    if gain <= 1.0:
        return page
    return cv2.convertScaleAbs(page, alpha=gain)


def correct_page(frame: np.ndarray, *, rotate: int = 0, keep_aspect: bool = False,
                 min_coverage: float = 0.10) -> tuple[np.ndarray, PageDetection]:
    """Camera frame (BGR or gray) -> (gray page at 300 DPI, detection). Geometry only:
    the page keeps the camera's exposure (the quality check measures it); `level_page`
    then makes it scanner-like for OCR and upload.

    Without a detected sheet the whole frame is returned, scaled to the A4 page width, so
    the reviewer sees what the camera saw; the quality block reports page_not_found.
    (No deskew guess: on an empty compartment it only costs time.)"""
    gray = rotate_frame(to_gray(frame), rotate)
    detection = detect_page(gray, min_coverage=min_coverage)
    if not detection.found:
        h, w = gray.shape
        size = (A4_PX[0], max(1, round(h * A4_PX[0] / w)))
        return cv2.resize(gray, size, interpolation=cv2.INTER_AREA if w > size[0]
                          else cv2.INTER_CUBIC), detection
    if detection.sideways:
        log.warning("Page lies sideways in the frame: set ADGVS_CAMERA_ROTATE (90 or 270) "
                    "so the page is upright")
    page = warp_to_page(gray, detection.corners, page_size(detection, keep_aspect),
                        ordered=True)
    return page, detection
