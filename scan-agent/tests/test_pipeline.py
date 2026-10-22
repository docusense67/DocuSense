"""OCR pipeline against real rendered TOR/COE samples (300 DPI), incl. degraded and tampered."""

import random

import cv2
import numpy as np

from scan_agent.capture.base import CapturedPage
from scan_agent.pipeline import analyze_page, extract_document, process_stack, split_documents
from scan_agent.simulate import LEVELS
from scan_agent.simulate import degrade as degrade_scan
from tests.conftest import snapshot

FIELDS_TOR = ["student_name", "student_number", "program", "document_number", "date_issued",
              "gwa"]
FIELDS_COE = ["student_name", "student_number", "program", "year_level", "semester",
              "academic_year", "document_number", "date_issued", "purpose"]


def pages(images, start=1):
    return [CapturedPage(sequence=i, image=img) for i, img in enumerate(images, start=start)]


def extract(images, lookup):
    analyzed = [analyze_page(p) for p in pages(images)]
    return extract_document(analyzed, 1, lookup).payload


def test_tor_identity_from_qr_and_footer(tor_pages):
    snap = snapshot("TOR-2026-000001")
    page = analyze_page(pages(tor_pages)[0])
    assert page.qr_payload == f"ADGVS1:{snap['qr_token']}"
    assert (page.footer.document_number, page.footer.page, page.footer.pages) == (
        "TOR-2026-000001", 1, 1)
    assert page.footer.ref == snap["data_hash"][:12]


def test_tor_fields_and_grades_match_snapshot(tor_pages, lookup):
    snap = snapshot("TOR-2026-000001")
    payload = extract(tor_pages, lookup)
    assert payload["identity_source"] == "qr"
    for name in FIELDS_TOR:
        assert payload["fields"][name]["value"] == snap["snapshot"][name], name
        assert payload["fields"][name]["confidence"] >= 80, name

    expected = [(s["subject_code"], s["units"], s["grade"])
                for t in snap["snapshot"]["terms"] for s in t["subjects"]]
    rows = payload["table"]["rows"]
    assert [(r["subject_code"], r["units"], r["grade"]) for r in rows] == expected
    assert all(r["term"] == "1st Semester, AY 2025-2026" for r in rows)
    # Each row reports where it sits (page mm) so the dashboard can crop it; rows go down.
    tops = [r["box"]["y"] for r in rows]
    assert all(r["box"]["page"] == 1 for r in rows) and tops == sorted(tops)
    assert all(84 <= y <= 275 for y in tops)  # inside the grades_table zone
    assert payload["table"]["term_gwas"][0]["gwa"] == snap["snapshot"]["terms"][0]["gwa"]


def test_coe_fields_match_snapshot(coe_pages, lookup):
    snap = snapshot("COE-2026-000001")
    payload = extract(coe_pages, lookup)
    for name in FIELDS_COE:
        assert payload["fields"][name]["value"] == snap["snapshot"][name], name
    assert payload["table"] is None


def test_multi_page_tor_keeps_terms_across_page_breaks(long_tor_pages, lookup):
    payload = extract(long_tor_pages, lookup)
    rows = payload["table"]["rows"]
    assert len(rows) == 48  # 8 terms x 6 subjects, spread over 3 pages
    per_term: dict[str, int] = {}
    for row in rows:
        per_term[row["term"]] = per_term.get(row["term"], 0) + 1
    assert len(per_term) == 8 and set(per_term.values()) == {6}
    assert [p["footer"]["page"] for p in payload["pages"]] == [1, 2, 3]


def test_scanner_noise_does_not_change_table_readings(long_tor_pages, lookup):
    """Regression for the CI ocr-accuracy run (85% of grades read on clean simulated scans):
    Tesseract's LSTM reads the grey image, so noise and JPEG artefacts turned 1.75 into
    "1/5" or "15" and dropped rows. With the table thresholded first, a clean simulated
    scan reads exactly like the noiseless render."""
    def table(images):
        return [(r["term"], r["subject_code"], r["units"], r["grade"])
                for r in extract(images, lookup)["table"]["rows"]]

    rng = random.Random(3)
    scanned = [degrade_scan(page, LEVELS["clean"], rng) for page in long_tor_pages]
    assert table(scanned) == table(long_tor_pages)


def test_bulk_stack_splits_into_documents(coe_pages, long_tor_pages, tor_pages, lookup):
    stack = coe_pages + long_tor_pages + tor_pages
    docs = process_stack(pages(stack), lookup)
    assert [len(d.pages) for d in docs] == [1, 3, 1]
    assert [d.position for d in docs] == [1, 2, 3]
    assert [d.payload["document_number_read"] for d in docs] == [
        "COE-2026-000001", "TOR-SAMPLE-LONG", "TOR-2026-000001"]


def test_missing_middle_page_is_visible_to_validator(long_tor_pages):
    analyzed = [analyze_page(p) for p in pages([long_tor_pages[0], long_tor_pages[2]])]
    groups = split_documents(analyzed)
    assert len(groups) == 1
    assert [p.footer.page for p in groups[0]] == [1, 3]  # backend sees "page 2 of 3 missing"


def degrade(image: np.ndarray, angle: float = 1.5) -> np.ndarray:
    """Simulate a real scan: dark scanner bed border, slight rotation, noise."""
    h, w = image.shape
    canvas = np.full((h + 120, w + 120), 40, np.uint8)
    canvas[60:60 + h, 60:60 + w] = image
    matrix = cv2.getRotationMatrix2D((canvas.shape[1] / 2, canvas.shape[0] / 2), angle, 1.0)
    rotated = cv2.warpAffine(canvas, matrix, canvas.shape[::-1], borderValue=40)
    noise = np.random.default_rng(0).normal(0, 6, rotated.shape)
    return np.clip(rotated + noise, 0, 255).astype(np.uint8)


def test_skewed_noisy_scan_still_reads(tor_pages, lookup):
    snap = snapshot("TOR-2026-000001")
    payload = extract([degrade(tor_pages[0])], lookup)
    assert payload["identity_source"] == "qr"
    for name in ("student_name", "student_number", "document_number", "gwa"):
        assert payload["fields"][name]["value"] == snap["snapshot"][name], name
    grades = {r["subject_code"]: r["grade"] for r in payload["table"]["rows"]}
    assert grades == {s["subject_code"]: s["grade"]
                      for s in snap["snapshot"]["terms"][0]["subjects"]}


def test_overprinted_grade_is_read_as_printed(tor_pages, lookup):
    """UC6: someone covers IT101's 1.25 and prints 1.00. OCR must report what is on paper."""
    image = tor_pages[0].copy()
    mm = 300 / 25.4
    # Grade column is x 146-166 mm; IT101 is the 3rd subject row of the table.
    row_y = 84 + 7.4 + 6.4 + 2 * 7.4  # header, term row, two subject rows (approx. mm)
    x0, x1 = int(148 * mm), int(164 * mm)
    y0, y1 = int((row_y + 1.2) * mm), int((row_y + 6.0) * mm)
    image[y0:y1, x0:x1] = 255
    cv2.putText(image, "1.00", (x0 + 40, y1 - 12), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    grades = {r["subject_code"]: r["grade"]
              for r in extract([image], lookup)["table"]["rows"]}
    assert grades.get("IT101") == "1.00"


def test_layout_anchors_are_measured_where_the_template_prints_them(tor_pages, lookup):
    """Level 2 layout check inputs: the QR and the table header, in page millimetres."""
    payload = extract([degrade(tor_pages[0])], lookup)
    qr = payload["pages"][0]["qr_box"]
    assert abs(qr["x"] + qr["w"] / 2 - 177) < 2 and abs(qr["y"] + qr["h"] / 2 - 31) < 2
    header = payload["table"]["header_box"]
    assert header["page"] == 1 and abs(header["y"] - 84) < 3
