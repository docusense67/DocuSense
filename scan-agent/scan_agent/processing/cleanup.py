"""Image clean-up before OCR (Section 10 step 4): crop to the sheet, deskew, normalize size."""

import cv2
import numpy as np

from scan_agent.config import A4_PX, MM_TO_PX

MAX_SKEW_DEG = 10.0
MIN_SKEW_DEG = 0.1


def to_gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def order_corners(points) -> np.ndarray:
    """Four points as top-left, top-right, bottom-right, bottom-left (float32).

    Sorted by angle around their centre, so it also holds for a strongly rotated or
    perspective-distorted quadrilateral (a camera frame), not only a near-upright one."""
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    centre = pts.mean(axis=0)
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - centre[1], pts[:, 0] - centre[0]))]
    return np.roll(pts, -int(np.argmin(pts.sum(axis=1))), axis=0)


def warp_to_page(gray: np.ndarray, corners, size: tuple[int, int] = A4_PX, *,
                 ordered: bool = False) -> np.ndarray:
    """Map the quadrilateral `corners` flat onto a `size` (w, h) image: crop, deskew and
    perspective correction in one step. Corners in any order are sorted geometrically;
    `ordered=True` keeps the given page TL, TR, BR, BL (e.g. a sheet lying sideways)."""
    w, h = size
    target = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    source = (np.asarray(corners, dtype=np.float32).reshape(4, 2) if ordered
              else order_corners(corners))
    matrix = cv2.getPerspectiveTransform(source, target)
    return cv2.warpPerspective(gray, matrix, (w, h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def find_sheet(gray: np.ndarray) -> np.ndarray | None:
    """If the paper sits on a darker scanner bed, return it warped flat to A4 (crop + deskew
    in one step). Return None when the sheet fills the frame (typical ADF output).
    Camera frames use `scan_agent.capture.page.detect_page` (perspective, cut-off edges)."""
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    sheet = max(contours, key=cv2.contourArea)
    ratio = cv2.contourArea(sheet) / gray.size
    if not 0.5 < ratio < 0.97:
        return None
    return warp_to_page(gray, cv2.boxPoints(cv2.minAreaRect(sheet)))


def skew_angle(gray: np.ndarray) -> float:
    """Estimate text skew from the minimum-area rectangle around dark pixels."""
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(ink > 0))
    if len(coords) < 500:
        return 0.0
    angle = cv2.minAreaRect(coords[:, ::-1].astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    if angle < -45:
        angle += 90
    return float(angle)


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    angle = skew_angle(gray)
    if abs(angle) < MIN_SKEW_DEG or abs(angle) > MAX_SKEW_DEG:
        return gray, 0.0
    h, w = gray.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return rotated, angle


def normalize_page(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Gray, flattened to the sheet, deskewed, A4 at 300 DPI so mm zones map to pixels."""
    gray = to_gray(image)
    sheet = find_sheet(gray)
    if sheet is not None:
        return sheet, 0.0
    gray, angle = deskew(gray)
    if gray.shape[::-1] != A4_PX:
        gray = cv2.resize(gray, A4_PX, interpolation=cv2.INTER_AREA)
    return gray, angle


def crop_mm(page: np.ndarray, zone: dict, pad_mm: float = 0.0) -> np.ndarray:
    x0 = max(0, round((zone["x"] - pad_mm) * MM_TO_PX))
    y0 = max(0, round((zone["y"] - pad_mm) * MM_TO_PX))
    x1 = min(page.shape[1], round((zone["x"] + zone["w"] + pad_mm) * MM_TO_PX))
    y1 = min(page.shape[0], round((zone["y"] + zone["h"] + pad_mm) * MM_TO_PX))
    return page[y0:y1, x0:x1]


# Below this gap between the mean ink and mean paper levels, a region holds no print (only
# scanner noise); thresholding it would turn the noise into speckles that OCR reads as text.
MIN_INK_CONTRAST = 50


def binarize(region: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Black print on pure white, optionally enlarged first (cubic, before thresholding so
    the edges stay smooth).

    Tesseract's LSTM recognizer reads the grey image, not its own thresholded copy, so
    scanner noise, JPEG artefacts and the white patches left by `remove_rules` all reach
    it. On grade cells that turns 1.75 into "1/5" or "15" and drops decimal points;
    thresholding here first removes them."""
    if scale != 1.0:
        region = cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    smooth = cv2.GaussianBlur(region, (3, 3), 0)
    level, binary = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hist = np.bincount(smooth.ravel(), minlength=256).astype(np.float64)
    cut = int(level) + 1
    ink, paper = hist[:cut], hist[cut:]
    if not ink.sum() or not paper.sum():
        return np.full_like(region, 255)
    levels = np.arange(256)
    if (levels[cut:] @ paper) / paper.sum() - (levels[:cut] @ ink) / ink.sum() < MIN_INK_CONTRAST:
        return np.full_like(region, 255)
    return binary


def remove_rules(region: np.ndarray, min_len_px: int = 60) -> np.ndarray:
    """Erase table borders. Without this Tesseract drops whole bordered columns."""
    smooth = cv2.GaussianBlur(region, (3, 3), 0)  # noise would break rules into fragments
    ink = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (min_len_px, 1)))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len_px)))
    # Widen the mask a little so anti-aliased rule edges don't survive as "|" glyphs.
    mask = cv2.dilate(horizontal | vertical, np.ones((5, 5), np.uint8))
    cleaned = region.copy()
    cleaned[mask > 0] = 255
    return cleaned
