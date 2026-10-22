import numpy as np
import pytest

from scan_agent.processing.cleanup import binarize
from scan_agent.processing.ocr import correct


def test_binarize_keeps_print_and_whitens_paper_noise():
    rng = np.random.default_rng(0)
    region = np.clip(rng.normal(235, 6, (60, 200)), 0, 255).astype(np.uint8)
    region[20:40, 50:150] = 30  # a dark stroke
    out = binarize(region)
    assert set(np.unique(out)) == {0, 255}
    assert (out[22:38, 52:148] == 0).all() and (out[:15] == 255).all()


@pytest.mark.parametrize("paper", [235, 120])
def test_binarize_blank_region_stays_blank(paper):
    """Otsu would split pure noise in two and hand Tesseract a field of speckles."""
    rng = np.random.default_rng(1)
    region = np.clip(rng.normal(paper, 6, (80, 300)), 0, 255).astype(np.uint8)
    assert (binarize(region) == 255).all()
    assert (binarize(region, scale=1.5) == 255).all()


def test_binarize_scale_enlarges():
    region = np.full((40, 100), 240, np.uint8)
    region[10:30, 20:80] = 20
    assert binarize(region, scale=1.5).shape == (60, 150)


@pytest.mark.parametrize(("name", "read", "fixed"), [
    ("year_level", "Ath", "4th"),
    ("year_level", "lst", "1st"),
    ("year_level", "2nd", "2nd"),
    ("semester", "Ist", "1st"),
    ("student_name", "Ill", "III"),
    ("student_name", "Il", "II"),
    ("student_name", "JR.", "JR."),
    ("student_name", "LI", "LI"),  # an upper-case L is a letter, not a misread I
])
def test_format_corrections(name, read, fixed):
    assert correct(name, read) == fixed


def test_semester_is_never_fourth():
    assert correct("semester", "Ath") == "Ath"
