"""Section 15 decision rules. Altered critical fields must never come out VALID."""

import copy

import pytest

from app.models.enums import GeneratedDocumentStatus as DS
from app.models.enums import ValidationResult as VR
from app.services.validation import compare, evaluate, normalize_text

SNAPSHOT = {
    "document_type": "TOR",
    "document_number": "TOR-2026-000001",
    "student_name": "MARIA R. SANTOS",
    "student_number": "2025-00001",
    "program": "Bachelor of Science in Information Technology",
    "date_issued": "October 5, 2026",
    "gwa": "1.44",
    "terms": [{
        "semester": "1st Semester", "academic_year": "2025-2026", "gwa": "1.44",
        "subjects": [
            {"subject_code": "GE101", "subject_title": "Understanding the Self", "units": "3",
             "grade": "1.75", "remarks": "Passed"},
            {"subject_code": "IT101", "subject_title": "Introduction to Computing", "units": "3",
             "grade": "1.25", "remarks": "Passed"},
        ],
    }],
}
HASH = "9f1a9ad6e5f2" + "0" * 52
TERM = "1st Semester, AY 2025-2026"


def good_extract() -> dict:
    fields = {k: {"value": SNAPSHOT[k], "confidence": 96.0}
              for k in ("document_number", "student_name", "student_number", "program",
                        "date_issued", "gwa")}
    rows = [{"term": TERM, "subject_code": s["subject_code"], "subject_title": "x",
             "units": s["units"], "grade": s["grade"], "remarks": "Passed",
             "confidence": {"subject_code": 95, "units": 95, "grade": 95}}
            for s in SNAPSHOT["terms"][0]["subjects"]]
    return {
        "identity_source": "qr",
        "pages": [{"sequence": 1, "qr_present": True,
                   "footer": {"document_number": "TOR-2026-000001", "page": 1, "pages": 1,
                              "ref": HASH[:12]}}],
        "fields": fields,
        "table": {"rows": rows, "term_gwas": [{"term": TERM, "gwa": "1.44", "confidence": 95}],
                  "unparsed": []},
    }


def run(extracted=None, **overrides):
    args = dict(identity="qr", document_found=True, document_status=DS.RELEASED,
                issued_page_count=1, snapshot=SNAPSHOT, data_hash=HASH,
                extracted=extracted or good_extract(), current_records=None)
    args.update(overrides)
    return evaluate(**args)


def test_clean_scan_is_valid():
    outcome = run()
    assert outcome.result == VR.VALID, outcome.reasons
    assert all(f.status == "match" for f in outcome.fields)


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"document_found": False}, "not issued by this institution"),
    ({"document_status": DS.REVOKED}, "revoked"),
    ({"document_status": DS.GENERATED}, "never released"),
])
def test_level0_identity_failures_are_invalid(overrides, reason):
    outcome = run(**overrides)
    assert outcome.result == VR.INVALID and reason in outcome.reasons[0]


def test_qr_unreadable_is_at_best_review():
    extracted = good_extract()
    extracted["pages"][0]["qr_present"] = False
    assert run(extracted, identity="number").result == VR.NEEDS_REVIEW


def test_qr_belonging_to_no_issued_document_is_never_valid():
    outcome = run(identity="foreign_qr")
    assert outcome.result == VR.NEEDS_REVIEW
    assert "possible forgery" in outcome.reasons[0]


def test_agent_claims_about_identity_are_ignored():
    extracted = good_extract()
    extracted["identity_source"] = "qr"  # the agent says so; the backend decided otherwise
    assert run(extracted, identity="number").result == VR.NEEDS_REVIEW


def test_uc6_altered_grade_with_high_confidence_is_invalid():
    extracted = good_extract()
    extracted["table"]["rows"][1]["grade"] = "1.00"
    outcome = run(extracted)
    assert outcome.result == VR.INVALID
    bad = next(f for f in outcome.fields if f.field.startswith("grade IT101"))
    assert (bad.printed, bad.official, bad.status) == ("1.00", "1.25", "mismatch")


def test_altered_grade_with_low_confidence_needs_review():
    extracted = good_extract()
    extracted["table"]["rows"][1].update(grade="1.00", confidence={"grade": 55})
    assert run(extracted).result == VR.NEEDS_REVIEW


def test_low_confidence_on_matching_critical_field_needs_review():
    extracted = good_extract()
    extracted["fields"]["student_number"]["confidence"] = 70
    outcome = run(extracted)
    assert outcome.result == VR.NEEDS_REVIEW
    assert "low OCR confidence" in " ".join(outcome.reasons)


def test_name_normalization_and_similarity():
    assert normalize_text("María  R. Santos") == normalize_text("MARIA R SANTOS")
    assert normalize_text("PEÑA") == "pena"
    extracted = good_extract()
    extracted["fields"]["student_name"]["value"] = "MARÍA R SANTOS"
    assert run(extracted).result == VR.VALID
    extracted["fields"]["student_name"]["value"] = "MARIA R. SANTOZ"  # 1 char off -> review
    assert run(extracted).result == VR.NEEDS_REVIEW
    extracted["fields"]["student_name"]["value"] = "JUAN DELA CRUZ"
    assert run(extracted).result == VR.INVALID


def test_student_number_ignores_spacing_and_dashes():
    assert compare("id", "2025 00001", "2025-00001")[0] == "match"


def test_important_field_mismatch_needs_review_not_invalid():
    extracted = good_extract()
    extracted["fields"]["program"]["value"] = "Bachelor of Science in Computer Science"
    assert run(extracted).result == VR.NEEDS_REVIEW


def test_missing_page_is_invalid_with_page_named():
    outcome = run(issued_page_count=2)
    assert outcome.result == VR.INVALID
    assert "page 2 of 2 missing" in outcome.reasons[0]


def test_record_changed_since_issue_needs_review():
    current = copy.deepcopy(SNAPSHOT)
    current["terms"][0]["subjects"][1]["grade"] = "1.00"  # corrected after issue
    outcome = run(current_records=current)
    assert outcome.result == VR.NEEDS_REVIEW
    assert "updated since" in outcome.reasons[0]


def test_extra_and_missing_rows_need_review():
    extracted = good_extract()
    extracted["table"]["rows"].append({**extracted["table"]["rows"][0], "subject_code": "CS999"})
    assert run(extracted).result == VR.NEEDS_REVIEW
    extracted = good_extract()
    extracted["table"]["rows"].pop()
    assert run(extracted).result == VR.NEEDS_REVIEW


def test_footer_reference_mismatch_needs_review():
    extracted = good_extract()
    extracted["pages"][0]["footer"]["ref"] = "000000000000"
    assert run(extracted).result == VR.NEEDS_REVIEW


CRITICAL_ALTERATIONS = [
    ("fields", "document_number", "TOR-2026-000009"),
    ("fields", "student_number", "2025-00099"),
    ("fields", "student_name", "PEDRO PENDUKO"),
    ("row", 0, "1.00"),
    ("row", 1, "3.00"),
    ("row", 1, "INC"),
]


@pytest.mark.parametrize(("where", "key", "value"), CRITICAL_ALTERATIONS)
@pytest.mark.parametrize("confidence", [99, 60])
def test_altered_critical_field_is_never_valid(where, key, value, confidence):
    """Section 21 acceptance criterion: zero false VALIDs on altered critical fields."""
    extracted = good_extract()
    if where == "fields":
        extracted["fields"][key].update(value=value, confidence=confidence)
    else:
        extracted["table"]["rows"][key].update(grade=value, confidence={"grade": confidence})
    assert run(extracted).result != VR.VALID


# ---- Level 2 (basic): layout anchors ----------------------------------------------------

ZONES = {"unit": "mm", "fields": {"qr_code": {"x": 164, "y": 18, "w": 26, "h": 26},
                                  "grades_table": {"x": 20, "y": 84, "w": 170, "h": 191}}}


def measured(qr=(166.7, 20.7, 20.5), header_y=84.6) -> dict:
    """What the agent reports for a genuine scan (values from real 300 DPI scans)."""
    extracted = good_extract()
    x, y, size = qr
    extracted["pages"][0]["qr_box"] = {"x": x, "y": y, "w": size, "h": size}
    extracted["table"]["header_box"] = {"page": 1, "x": 20, "y": header_y, "w": 170, "h": 5.8}
    return extracted


def test_layout_matching_the_template_is_valid():
    assert run(measured(), template_zones=ZONES).result == VR.VALID


@pytest.mark.parametrize(("extracted", "reason"), [
    (measured(qr=(120, 20.7, 20.5)), "QR code is 47 mm from where it is printed"),
    (measured(qr=(172.5, 26.5, 9.0)), "QR code is a different size than printed"),  # same centre
    (measured(header_y=110), "grades table starts 26 mm from where it is printed"),
])
def test_layout_that_moved_needs_review(extracted, reason):
    outcome = run(extracted, template_zones=ZONES)
    assert outcome.result == VR.NEEDS_REVIEW
    assert any(reason in r for r in outcome.reasons), outcome.reasons


def test_layout_is_not_judged_on_anchors_the_scan_did_not_measure():
    # Older agents report no positions; the template may have no QR zone.
    assert run(good_extract(), template_zones=ZONES).result == VR.VALID
    assert run(measured(qr=(120, 20.7, 20.5)), template_zones={"fields": {}}).result == VR.VALID


def test_grade_read_off_the_grading_scale_is_reviewed_not_failed():
    """'2.75' misread as '2.15': 2.15 cannot be printed on a genuine TOR, so it is an OCR
    error. Review it rather than fail a genuine document; it is never VALID either."""
    extracted = good_extract()
    extracted["table"]["rows"][1]["grade"] = "1.15"  # IT101 is 1.25
    outcome = run(extracted)
    assert outcome.result == VR.NEEDS_REVIEW
    assert any("not on the grading scale" in r for r in outcome.reasons)


def test_grade_altered_to_another_scale_value_is_still_invalid():
    extracted = good_extract()
    extracted["table"]["rows"][1]["grade"] = "1.00"
    assert run(extracted).result == VR.INVALID
