"""Low-level measurements on the page image: printed text height / baseline / left edge,
seal and logo template matching, and local background-tone anomalies."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from scan_agent.config import MM_TO_PX

from .page import Box


@dataclass(frozen=True)
class TextMetrics:
    height: float    # px: 90th percentile glyph height (cap / ascender height)
    baseline: float  # px: median glyph bottom
    left: float      # px: leftmost glyph edge
    glyphs: int


def text_metrics(gray: np.ndarray, box: Box) -> TextMetrics | None:
    """Glyph statistics of the text inside `box` (page px), from connected components of
    the thresholded crop. Word boxes from OCR depend on which letters a word has
    (descenders, capitals); glyph components do not, so heights and baselines of different
    words are comparable."""
    x0, y0, x1, y1 = box
    pad = max(3, (y1 - y0) // 4)
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(gray.shape[1], x1 + pad), min(gray.shape[0], y1 + pad)
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0 or crop.shape[0] < 6 or crop.shape[1] < 6:
        return None
    smooth = cv2.GaussianBlur(crop, (3, 3), 0)
    level, ink = cv2.threshold(smooth, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if float(smooth.max()) - float(smooth.min()) < 50:
        return None
    # Underlines and box rules are longer than any glyph: drop them.
    rule_len = max(15, int(1.5 * crop.shape[0]))
    rules = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (rule_len, 1)))
    ink = cv2.subtract(ink, cv2.dilate(rules, np.ones((3, 3), np.uint8)))
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    comps = stats[1:]
    comps = comps[(comps[:, cv2.CC_STAT_AREA] >= 6) & (comps[:, cv2.CC_STAT_HEIGHT] >= 4)]
    # Components touching the crop's top/bottom border belong to neighbouring lines.
    inside = (comps[:, cv2.CC_STAT_TOP] > 0) & (
        comps[:, cv2.CC_STAT_TOP] + comps[:, cv2.CC_STAT_HEIGHT] < crop.shape[0])
    comps = comps[inside]
    if len(comps) < 2:
        return None
    heights = comps[:, cv2.CC_STAT_HEIGHT].astype(float)
    tall = heights >= 0.45 * np.percentile(heights, 90)  # drop dots, commas, hyphens
    comps, heights = comps[tall], heights[tall]
    if len(comps) < 2:
        return None
    bottoms = comps[:, cv2.CC_STAT_TOP] + comps[:, cv2.CC_STAT_HEIGHT]
    # "higher": with few glyphs ("Name:") interpolation would land between x-height and
    # cap height; the 90th-percentile glyph itself is a capital or ascender.
    return TextMetrics(height=float(np.percentile(heights, 90, method="higher")),
                       baseline=float(np.median(bottoms)) + y0,
                       left=float(comps[:, cv2.CC_STAT_LEFT].min()) + x0,
                       glyphs=len(comps))


# -- seals and logos ------------------------------------------------------------------
MATCH_SCALE = 0.25  # template matching at 75 DPI: seals are large, and it is 16x cheaper
MATCH_SIZES = (0.9, 1.0, 1.1)


def match_template(gray: np.ndarray, template: np.ndarray, search: Box) -> tuple[float, Box]:
    """Best normalized cross-correlation (0..1) of the template inside `search`, over a few
    print scales; and where it matched (page px)."""
    x0, y0, x1, y1 = search
    region = gray[y0:y1, x0:x1]
    if region.size == 0:
        return 0.0, search
    small = cv2.resize(region, None, fx=MATCH_SCALE, fy=MATCH_SCALE, interpolation=cv2.INTER_AREA)
    best, best_box = 0.0, search
    for size in MATCH_SIZES:
        f = MATCH_SCALE * size
        tmpl = cv2.resize(template, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        th, tw = tmpl.shape
        if th < 8 or tw < 8 or th > small.shape[0] or tw > small.shape[1]:
            continue
        if float(tmpl.std()) < 1.0:
            continue
        result = cv2.matchTemplate(small, tmpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        if np.isfinite(score) and score > best:
            best = float(score)
            bx, by = x0 + loc[0] / MATCH_SCALE, y0 + loc[1] / MATCH_SCALE
            best_box = (round(bx), round(by), round(bx + tw / MATCH_SCALE),
                        round(by + th / MATCH_SCALE))
    return max(0.0, min(1.0, best)), best_box


def crop_ink(gray: np.ndarray, box: Box, pad_px: int = 6) -> np.ndarray | None:
    """The tight crop of the printed mark inside `box` (to cut a seal template)."""
    x0, y0, x1, y1 = box
    region = gray[y0:y1, x0:x1]
    if region.size == 0:
        return None
    ink = cv2.threshold(cv2.GaussianBlur(region, (5, 5), 0), 0, 255,
                        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if count < 2:
        return None
    big = stats[1:][np.argmax(stats[1:, cv2.CC_STAT_AREA])]
    if big[cv2.CC_STAT_AREA] < 0.02 * region.size:
        return None
    left, top, w, h = (int(v) for v in big[:4])
    left, top = max(0, left - pad_px), max(0, top - pad_px)
    return region[top:top + h + 2 * pad_px, left:left + w + 2 * pad_px].copy()


# -- background tone (pasted / retouched regions) -------------------------------------
TONE_CELL_MM = 6.0
TONE_SMOOTH_CELLS = 11  # background model: median over 66 mm, larger than a pasted patch
TONE_MIN_CELLS = 3


@dataclass(frozen=True)
class ToneMap:
    residual: np.ndarray  # grey levels, per cell (paper level minus smooth background)
    valid: np.ndarray     # cells with enough bare paper to measure
    cell_px: int


def tone_map(gray: np.ndarray, exclude: list[Box]) -> ToneMap:
    """Paper level per 6 mm cell (95th percentile, i.e. the paper between the print) minus
    the lighting (a robust quadratic surface) and a local background (median over 11x11
    cells, which follows paper tint but not a patch). A region pasted from another sheet,
    covered with correction paper or retouched digitally keeps a different paper level and
    stands out."""
    half = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    cell = max(4, round(TONE_CELL_MM * MM_TO_PX / 2))
    ny, nx = half.shape[0] // cell, half.shape[1] // cell
    blocks = half[:ny * cell, :nx * cell].reshape(ny, cell, nx, cell).transpose(0, 2, 1, 3)
    blocks = blocks.reshape(ny, nx, cell * cell)
    k = int(0.95 * (cell * cell - 1))
    paper = np.partition(blocks, k, axis=2)[:, :, k].astype(np.float32)
    ink_frac = (blocks < (paper[:, :, None] - 60)).mean(axis=2)
    valid = ink_frac < 0.35
    valid[[0, -1], :] = False
    valid[:, [0, -1]] = False
    for x0, y0, x1, y1 in exclude:
        cx0, cy0 = max(0, x0 // (2 * cell)), max(0, y0 // (2 * cell))
        cx1, cy1 = min(nx, -(-x1 // (2 * cell))), min(ny, -(-y1 // (2 * cell)))
        valid[cy0:cy1, cx0:cx1] = False
    if valid.sum() < 20:
        return ToneMap(np.zeros_like(paper), valid, cell * 2)
    # Lighting first: a robust quadratic surface (camera fall-off, a lamp to one side),
    # which a median filter cannot follow into the page corners.
    yy, xx = np.mgrid[0:ny, 0:nx].astype(np.float32)
    xx, yy = xx / max(1, nx - 1) - 0.5, yy / max(1, ny - 1) - 0.5
    basis = np.stack([np.ones_like(xx), xx, yy, xx * xx, xx * yy, yy * yy], axis=-1)
    use = valid.copy()
    surface = np.full_like(paper, float(np.median(paper[valid])))
    for _ in range(3):
        coef, *_ = np.linalg.lstsq(basis[use], paper[use], rcond=None)
        surface = basis @ coef
        dev = np.abs(paper - surface)
        cut = max(2.0, 3.0 * float(np.median(dev[valid])))
        use = valid & (dev <= cut)
        if use.sum() < 20:
            break
    detrended = paper - surface
    filled = np.where(valid, detrended, 0.0)
    smooth = cv2.medianBlur(np.clip(filled + 128, 0, 255).astype(np.uint8), TONE_SMOOTH_CELLS)
    residual = detrended - (smooth.astype(np.float32) - 128)
    residual[~valid] = 0.0
    return ToneMap(residual.astype(np.float32), valid, cell * 2)


@dataclass(frozen=True)
class ToneAnomaly:
    box: Box
    level: float  # mean |residual| over the region, grey levels
    cells: int


def tone_anomalies(tones: ToneMap, tolerance: float,
                   expected: np.ndarray | None = None) -> list[ToneAnomaly]:
    """Connected regions of at least 3 cells whose paper level differs from the smooth
    background by more than 3/4 of the tolerance (below that, camera noise and print
    density make 3-4 level blobs on clean pages). `expected` is the mean residual of the
    reference samples (printed tints), resized to this grid and subtracted first."""
    residual = tones.residual
    if expected is not None and expected.size:
        residual = residual - cv2.resize(expected.astype(np.float32), residual.shape[::-1],
                                         interpolation=cv2.INTER_LINEAR)
    candidate = (tones.valid & (np.abs(residual) > 0.75 * tolerance)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
    found = []
    for i in range(1, count):
        cells = int(stats[i, cv2.CC_STAT_AREA])
        if cells < TONE_MIN_CELLS:
            continue
        level = float(np.abs(residual[labels == i]).mean())
        left, top, w, h = (int(v) * tones.cell_px for v in stats[i, :4])
        found.append(ToneAnomaly((left, top, left + w, top + h), level, cells))
    return found
