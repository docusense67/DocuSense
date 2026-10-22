"""End-to-end screening on synthetic SAMPLE pages: type identification, unknown documents,
every tamper variant, clean pages, fields, similarity, timing, CLI, reference store.

Synthetic pages prove the code paths and the measurement logic, not accuracy on the
client's real documents (README, "Screening")."""

import json

import cv2
import numpy as np
import pytest

from scan_agent.__main__ import main
from scan_agent.config import MM_TO_PX
from scan_agent.screening import load_profiles, screen_page
from scan_agent.screening.references import ReferenceStore
from scan_agent.screening.synthetic import BUILDERS, TAMPERS, UNKNOWN_KINDS, render

from .conftest import REFS_PER_TYPE, STATS
from .contract import validate

CLEAN_SEEDS = (4000, 4001)
TAMPER_SEED = 4100
SERIOUS = 0.5  # the backend's default indicator threshold


@pytest.mark.parametrize("refs", [False, True], ids=["profiles", "profiles+refs"])
@pytest.mark.parametrize("seed", CLEAN_SEEDS)
@pytest.mark.parametrize("doc_type", list(BUILDERS))
def test_clean_page_is_identified_and_raises_nothing_serious(screened, doc_type, seed, refs):
    result = screened(doc_type, seed, refs=refs)
    validate(result)
    STATS["classified"].append(result["doc_type"]["predicted"] == doc_type)
    assert result["doc_type"]["predicted"] == doc_type
    assert result["doc_type"]["confidence"] >= 0.75
    serious = [(i["code"], i["severity"], i["detail"]) for i in result["indicators"]
               if i["severity"] >= SERIOUS]
    assert serious == []
    checks = result["checks"]
    assert checks["expected_headings_found"] == checks["expected_headings_total"]
    assert checks["expected_fields_found"] >= checks["expected_fields_total"] - 1
    assert checks["layout_score"] >= 0.9
    assert checks["font_consistency"] >= 0.8 and checks["alignment_score"] >= 0.8
    if refs:
        assert checks["seal_logo_score"] >= 0.6
        assert result["similarity"]["reference_count"] == REFS_PER_TYPE
        assert result["similarity"]["score"] >= 0.8
    else:
        assert checks["seal_logo_score"] is None  # no templates: not measured
        assert "similarity" not in result


@pytest.mark.parametrize("refs", [False, True], ids=["profiles", "profiles+refs"])
@pytest.mark.parametrize("kind", UNKNOWN_KINDS)
def test_out_of_scope_documents_are_unknown(screened, kind, refs):
    result = screened(kind, CLEAN_SEEDS[0], refs=refs)
    validate(result)
    STATS["unknown"].append(result["doc_type"]["predicted"] == "unknown")
    assert result["doc_type"]["predicted"] == "unknown"
    assert result["indicators"] == []


def test_this_systems_own_tor_and_coe_are_unknown(screened, tor_pages, coe_pages):
    for name, image in (("tor", tor_pages[0]), ("coe", coe_pages[0])):
        result = screened(name, 0, refs=True, image=image)
        validate(result)
        STATS["unknown"].append(result["doc_type"]["predicted"] == "unknown")
        assert result["doc_type"]["predicted"] == "unknown", name


def _overlaps(box_px, box_mm, margin_mm=10.0) -> bool:
    x, y, w, h = box_px
    tx, ty, tw, th = ((v - margin_mm) * MM_TO_PX if i < 2 else (v + 2 * margin_mm) * MM_TO_PX
                      for i, v in enumerate(box_mm))
    return x < tx + tw and tx < x + w and y < ty + th and ty < y + h


@pytest.mark.parametrize("tamper", list(TAMPERS))
@pytest.mark.parametrize("doc_type", list(BUILDERS))
def test_each_tamper_raises_its_indicator_where_it_was_made(screened, doc_type, tamper):
    sample = render(doc_type, TAMPER_SEED, tamper)
    result = screened(doc_type, TAMPER_SEED, tamper, refs=True, as_type=doc_type)
    hits = [i for i in result["indicators"] if i["code"] == TAMPERS[tamper]]
    assert hits, result["indicators"]
    best = max(hits, key=lambda i: i["severity"])
    assert best["severity"] >= SERIOUS, best
    assert _overlaps(best["box"], sample.tamper_box_mm), (best, sample.tamper_box_mm)


@pytest.mark.parametrize("tamper", [t for t in TAMPERS if t != "seal_missing"])
def test_tampers_are_found_from_the_profile_alone(screened, tamper):
    """Before any client sample is enrolled (no learned positions, occupancy or templates)."""
    result = screened("psa_birth_certificate", TAMPER_SEED, tamper, refs=False,
                      as_type="psa_birth_certificate")
    assert max((i["severity"] for i in result["indicators"] if i["code"] == TAMPERS[tamper]),
               default=0.0) >= SERIOUS


def test_fields_are_extracted_as_printed(screened):
    exact = total = 0
    for doc_type in BUILDERS:
        printed = render(doc_type, CLEAN_SEEDS[0]).values
        fields = screened(doc_type, CLEAN_SEEDS[0], refs=False)["fields"]
        exact += sum(fields.get(k, {}).get("value") == v for k, v in printed.items())
        total += len(printed)
    assert exact / total >= 0.9, f"{exact}/{total}"


def test_screening_time_per_page_is_within_budget():
    """Budget on the Pi 5 is ~5 s per page, and the Pi is ~2-3x slower than the dev PC."""
    result = screen_page(render("psa_birth_certificate", 4200).image, store=False,
                         timings_ms={"capture": 900, "quality": 40})
    assert result["timings_ms"]["capture"] == 900  # passed through from the capture code
    assert 0 < result["timings_ms"]["screening_ocr"] <= result["timings_ms"]["screening"]
    assert result["timings_ms"]["screening"] < 8000


def test_capture_and_quality_blocks_are_passed_through():
    quality = {"ok": True, "issues": [], "blur_score": 412.3}
    result = screen_page(render("blank", 1).image, store=False,
                         capture={"source": "file", "width": 2480, "height": 3508, "attempts": 1},
                         quality=quality)
    validate(result)
    assert result["quality"] == quality and result["capture"]["source"] == "file"


def test_cli_screen_prints_the_contract_json(tmp_path, capsys):
    path = tmp_path / "page.png"
    cv2.imwrite(str(path), render("jhs_completion_certificate", 4300).image)
    assert main(["screen", str(path), "--no-refs"]) == 0
    result = json.loads(capsys.readouterr().out)
    validate(result)
    assert result["doc_type"]["predicted"] == "jhs_completion_certificate"


def test_cli_enroll_builds_a_store_without_personal_data(tmp_path, capsys):
    sample = render("good_moral_certificate", 4400)
    path = tmp_path / "sample.png"
    cv2.imwrite(str(path), sample.image)
    refs = tmp_path / "refs"
    args = ["screening-enroll", "--type", "good_moral_certificate", "--refs", str(refs),
            str(path)]
    assert main(args) == 0
    assert main(args) == 0  # enrolling the same page again replaces it
    out = capsys.readouterr().out
    store = ReferenceStore(refs)
    assert store.count("good_moral_certificate") == 1
    folder = refs / "good_moral_certificate"
    assert (folder / "templates" / "deped_logo.png").exists()
    stored = "".join(p.read_text(encoding="utf-8") for p in folder.rglob("*.json")) + out
    for value in sample.values.values():
        assert value not in stored  # no field value in the index, features or report
    assert "sample.png" not in stored  # nor the file name (it may be a learner's name)
    image = cv2.imread(str(next((folder / "images").glob("*.png"))), cv2.IMREAD_GRAYSCALE)
    h, w = image.shape
    x, y, zw, zh = load_profiles()["good_moral_certificate"][0].field("student_name").value_zone
    crop = image[int(y * h) + 2:int((y + zh) * h) - 2, int(x * w) + 2:int((x + zw) * w) - 2]
    assert crop.min() >= 250  # the learner's name is whited out in the stored sample


def test_store_reports_no_similarity_for_a_type_without_references(tmp_path):
    from scan_agent.screening import similarity
    from scan_agent.screening.page import Page

    assert similarity(Page(np.full((3508, 2480), 240, np.uint8)), "jhs_report_card",
                      ReferenceStore(tmp_path)) is None
