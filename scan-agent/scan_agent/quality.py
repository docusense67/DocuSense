"""Image-quality measurement for camera captures: the contract's `quality` block.

    from scan_agent.quality import assess
    assess(frame)  # -> {"ok": True, "issues": [], "blur_score": 412.3, "brightness": 0.81, ...}

What is measured where:
- brightness and glare_fraction describe the exposure, so they are measured on the page as
  the camera saw it (perspective-corrected, before `level_page` brightens the paper);
- blur_score is the variance of the Laplacian on the normalized page (corrected + levelled,
  the image OCR reads and the backend receives), as the contract defines it;
- skew_deg, coverage and effective_dpi come from the page detection in the frame.

The agent measures and reports (docs/intake/screening-contract.md). `ok`/`issues` only
drive the agent's own capture QA (recapture up to 3 times, then submit with ok=false); the
backend decides what an unusable capture means for screening.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from scan_agent.capture.page import PageDetection, correct_page, level_page
from scan_agent.config import QualityThresholds

# Contract order (docs/intake/screening-contract.md, quality.issues).
ISSUES = ("blur", "too_dark", "too_bright", "glare", "skew", "page_not_found",
          "page_cut_off", "low_resolution")
# Issues a new exposure can fix (motion, focus, metering, a reflection that moves with
# the auto-exposure). The sheet does not move in the compartment between attempts, so
# recapturing for its position (skew, cut off, missing, too small) would only cost time.
TRANSIENT_ISSUES = frozenset({"blur", "too_dark", "too_bright", "glare"})


def _small(image: np.ndarray) -> np.ndarray:
    # 4x4 averaging: fast, and a lone hot pixel or a white gap between letters is not a
    # clipped highlight.
    return cv2.resize(image, (max(1, image.shape[1] // 4), max(1, image.shape[0] // 4)),
                      interpolation=cv2.INTER_AREA)


def measure(raw: np.ndarray, page: np.ndarray, glare_level: int = 250) -> dict:
    """Pixel statistics: exposure on the `raw` corrected page, sharpness on the levelled
    `page` (both grayscale, same geometry)."""
    small = _small(raw)
    return {"blur_score": round(float(cv2.Laplacian(page, cv2.CV_64F).var()), 1),
            "brightness": round(float(small.mean()) / 255.0, 3),
            "glare_fraction": round(float(np.count_nonzero(small >= glare_level)) / small.size,
                                    4)}


def assess_page(raw: np.ndarray, page: np.ndarray, detection: PageDetection,
                thresholds: QualityThresholds | None = None) -> dict:
    """The quality block for a frame already corrected (`raw`) and levelled (`page`)."""
    t = thresholds or QualityThresholds.from_env()
    m = measure(raw, page, t.glare_level)
    found = detection.found
    checks = {
        "blur": m["blur_score"] < t.blur_min,
        "too_dark": m["brightness"] < t.brightness_min,
        "too_bright": m["brightness"] > t.brightness_max,
        "glare": m["glare_fraction"] > t.glare_max,
        "skew": found and abs(detection.skew_deg) > t.skew_max_deg,
        "page_not_found": not found,
        "page_cut_off": found and detection.cut_off,
        "low_resolution": found and detection.effective_dpi < t.dpi_min,
    }
    issues = [code for code in ISSUES if checks[code]]
    return {"ok": not issues, "issues": issues, **m,
            "skew_deg": round(detection.skew_deg, 1),
            "coverage": round(detection.coverage, 3),
            "effective_dpi": int(detection.effective_dpi)}


@dataclass(frozen=True)
class Inspection:
    page: np.ndarray  # normalized page: gray, A4 at 300 DPI, paper levelled (what is sent)
    detection: PageDetection
    quality: dict  # the contract's quality block


def inspect(frame: np.ndarray, *, rotate: int = 0, keep_aspect: bool = False,
            thresholds: QualityThresholds | None = None) -> Inspection:
    """Camera frame (BGR or gray) -> normalized page + its quality block."""
    t = thresholds or QualityThresholds.from_env()
    raw, detection = correct_page(frame, rotate=rotate, keep_aspect=keep_aspect,
                                  min_coverage=t.coverage_min)
    page = level_page(raw)
    return Inspection(page, detection, assess_page(raw, page, detection, t))


def assess(image: np.ndarray, *, rotate: int = 0,
           thresholds: QualityThresholds | None = None) -> dict:
    """A camera frame (BGR or gray) -> the contract `quality` block."""
    return inspect(image, rotate=rotate, thresholds=thresholds).quality
