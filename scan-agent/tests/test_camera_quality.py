"""Camera capture QA (contract `quality` block): every issue code fires on its defect, clean
frames pass, and a page photographed in perspective OCRs like the flat original.

The default thresholds in scan_agent/config.py (QualityThresholds) were chosen from these
synthetic frames and the TOR fixture; the comments there give the measured numbers."""

import json

import numpy as np
import pytest

from scan_agent.capture.base import CapturedPage
from scan_agent.capture.page import level_page
from scan_agent.config import QualityThresholds
from scan_agent.pipeline import analyze_page, extract_document
from scan_agent.quality import ISSUES, assess, inspect
from tests.camera_frames import (
    blur,
    camera_frame,
    default_page,
    empty_frame,
    exposure,
    glare,
    sparse_page,
)
from tests.conftest import ZONES

T = QualityThresholds()  # the defaults, whatever the environment says
CONTRACT_KEYS = ["ok", "issues", "blur_score", "brightness", "glare_fraction", "skew_deg",
                 "coverage", "effective_dpi"]


def frame(**kw) -> np.ndarray:
    return camera_frame(default_page(), **kw)[0]


def test_quality_block_matches_the_contract():
    q = assess(frame(), thresholds=T)
    assert list(q) == CONTRACT_KEYS
    assert q["ok"] is True and q["issues"] == []
    assert all(type(q[k]) is float for k in ("blur_score", "brightness", "glare_fraction",
                                              "skew_deg", "coverage"))
    assert type(q["effective_dpi"]) is int
    assert json.loads(json.dumps(q)) == q  # plain JSON types, no numpy scalars
    assert 0.0 <= q["brightness"] <= 1.0 and 0.0 <= q["coverage"] <= 1.0
    assert set(ISSUES) == {"blur", "too_dark", "too_bright", "glare", "skew",
                           "page_not_found", "page_cut_off", "low_resolution"}


@pytest.mark.parametrize("kw", [
    {},
    {"angle": 4, "tilt": 0.05},
    {"angle": -6, "shift": (0.02, 0.01), "size": (2400, 2900)},
    {"background": 120},  # mid-grey compartment floor
    {"background": 170},  # light grey
    {"size": (3300, 4300)},  # ~330 DPI, Camera Module 3 class
], ids=["upright", "rotated-keystone", "shifted", "grey-bg", "light-bg", "high-res"])
def test_clean_frames_pass(kw):
    q = assess(frame(**kw), thresholds=T)
    assert q["ok"], q
    assert q["blur_score"] > 3 * T.blur_min


def test_a_sparse_page_is_not_mistaken_for_blur():
    """The Laplacian variance falls with less print on the page: a sharp page with six
    lines must still pass."""
    q = assess(camera_frame(sparse_page())[0], thresholds=T)
    assert q["ok"], q


@pytest.mark.parametrize(("make", "issues"), [
    (lambda: blur(frame(), 1.5), ["blur"]),
    (lambda: exposure(frame(), 0.38), ["too_dark"]),
    (lambda: exposure(frame(), 1.3), ["too_bright", "glare"]),  # paper clipped white
    (lambda: glare(frame(), (1100, 1300), 200), ["glare"]),
    (lambda: frame(angle=13, page_height=0.86, size=(2600, 2900)), ["skew"]),
    (lambda: empty_frame(), ["too_dark", "page_not_found"]),
    (lambda: frame(shift=(0.12, 0.0)), ["page_cut_off"]),
    (lambda: frame(size=(1500, 1980)), ["low_resolution"]),  # ~150 DPI
], ids=["blur", "too_dark", "too_bright", "glare", "skew", "page_not_found", "page_cut_off",
        "low_resolution"])
def test_each_issue_fires_on_its_defect(make, issues):
    q = assess(make(), thresholds=T)
    assert q["issues"] == issues
    assert q["ok"] is False


@pytest.mark.parametrize("shift", [(0.12, 0.0), (-0.12, 0.0), (0.0, 0.1), (0.0, -0.1)],
                         ids=["right", "left", "bottom", "top"])
def test_cut_off_on_any_edge(shift):
    assert "page_cut_off" in assess(frame(shift=shift), thresholds=T)["issues"]


def test_mild_blur_and_a_small_highlight_are_tolerated():
    """Sigma 0.7 px still reads perfectly; a 1.4 cm hotspot covers no field."""
    assert assess(blur(frame(), 0.7), thresholds=T)["ok"]
    assert assess(glare(frame(), (1100, 1300), 60), thresholds=T)["ok"]


def test_dim_but_usable_light_passes():
    q = assess(exposure(frame(), 0.6), thresholds=T)
    assert q["ok"], q
    assert 0.45 < q["brightness"] < 0.6


def test_thresholds_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("ADGVS_QUALITY_BLUR_MIN", "100000")
    monkeypatch.setenv("ADGVS_QUALITY_DPI_MIN", "250")
    env = QualityThresholds.from_env()
    assert env.blur_min == 100000.0 and env.dpi_min == 250 and type(env.dpi_min) is int
    assert env.glare_max == T.glare_max
    assert assess(frame())["issues"] == ["blur", "low_resolution"]  # 218 DPI < 250
    monkeypatch.setenv("ADGVS_QUALITY_GLARE_MAX", "a lot")
    with pytest.raises(ValueError, match="ADGVS_QUALITY_GLARE_MAX"):
        QualityThresholds.from_env()


def test_level_page_whitens_dim_paper_only():
    page = default_page()
    dim = (page * 0.5).astype(np.uint8)
    levelled = level_page(dim)
    assert abs(np.percentile(levelled, 95) - 245) <= 3
    assert levelled.min() < 40  # print stays dark
    bright = np.full((100, 100), 250, np.uint8)
    assert level_page(bright) is bright  # never darkened


# ---- OCR sanity: a corrected photo reads like the flat page --------------------------------


def lookup(token, number):
    """Like conftest's `lookup` fixture (template zones only), usable at module scope."""
    doc_type = "COE" if (number or "").startswith("COE") else "TOR"
    return {"found": True, "document_number": number, "document_type": doc_type,
            "field_zones": ZONES[doc_type]}


def extract(image) -> dict:
    analyzed = analyze_page(CapturedPage(sequence=1, image=image))
    payload = extract_document([analyzed], 1, lookup).payload
    rows = [(r["subject_code"], r["units"], r["grade"]) for r in payload["table"]["rows"]]
    return {"fields": {k: v["value"] for k, v in payload["fields"].items()}, "rows": rows,
            "qr": payload["qr_token"], "number": payload["document_number_read"]}


@pytest.fixture(scope="module")
def flat_tor(tor_pages):
    flat = (tor_pages[0].astype(np.float32) * 0.89 + 3).astype(np.uint8)  # paper ~230
    reading = extract(flat)
    assert reading["rows"] and reading["qr"]  # the flat page itself reads
    return flat, reading


@pytest.mark.parametrize(("pose", "rotate"), [
    ({"size": (2700, 3500), "angle": 3, "tilt": 0.04}, 0),
    # Camera Module 3 framing: the sheet lies across a 16:9 frame, the mount turns it 90.
    ({"size": (4608, 2592), "page_height": 1.30, "angle": 90.0 + 2, "tilt": 0.03}, 270),
], ids=["keystone", "across-16x9"])
def test_corrected_photo_ocrs_like_the_flat_original(flat_tor, pose, rotate):
    flat, want = flat_tor
    photo = blur(camera_frame(flat, **pose)[0], 0.6)
    result = inspect(photo, rotate=rotate, thresholds=T)
    assert result.quality["ok"], result.quality
    assert result.quality["effective_dpi"] >= 250
    assert extract(result.page) == want
