"""S-04 screening decision (services/intake.py): every rule, every boundary, tolerant parsing.

Contract defaults: manual_review if quality.ok is false, the type is unknown, type confidence
< 0.75, any indicator severity >= 0.5, layout_score < 0.70, similarity < 0.60 (only when
measured) or fewer headings found than expected. Otherwise passed_screening (Tray 1).
"""

import copy
import json

import pytest

from app.models.enums import IntakeDecision, IntakeDocType
from app.services import intake
from app.services.intake import (
    DOC_TYPE_LOW_CONFIDENCE,
    DOC_TYPE_UNKNOWN,
    HEADINGS_MISSING,
    LAYOUT_SCORE_LOW,
    QUALITY_NOT_OK,
    SEVERE_INDICATOR,
    SIMILARITY_LOW,
    InvalidScreeningResult,
    Thresholds,
    decide,
    load_result,
    parse,
)

PASSED = IntakeDecision.PASSED_SCREENING
REVIEW = IntakeDecision.MANUAL_REVIEW


def clean(**changes) -> dict:
    """A SAMPLE result that passes every rule (the contract example, minus the indicator)."""
    result = {
        "schema": 1,
        "agent_version": "0.3.0",
        "capture": {"source": "file", "width": 4608, "height": 2592, "attempts": 1},
        "quality": {"ok": True, "issues": [], "blur_score": 412.3, "brightness": 0.71,
                    "glare_fraction": 0.002, "skew_deg": 0.8, "coverage": 0.93,
                    "effective_dpi": 312},
        "doc_type": {"predicted": "psa_birth_certificate", "confidence": 0.91,
                     "scores": {"psa_birth_certificate": 0.91, "jhs_report_card": 0.05}},
        "checks": {"expected_headings_found": 4, "expected_headings_total": 4,
                   "expected_fields_found": 9, "expected_fields_total": 10,
                   "layout_score": 0.88, "seal_logo_score": 0.80, "font_consistency": 0.93,
                   "alignment_score": 0.90},
        "indicators": [],
        "similarity": {"score": 0.84, "reference_count": 12, "method": "orb+layout"},
        "fields": {"last_name": {"value": "SAMPLE", "confidence": 0.93}},
        "timings_ms": {"capture": 900, "quality": 40, "screening": 2100},
    }
    for path, value in changes.items():
        target = result
        *parents, leaf = path.split("__")
        for key in parents:
            target = target[key]
        if value is DELETE:
            del target[leaf]
        else:
            target[leaf] = value
    return result


DELETE = object()


def indicator(severity, code="altered_region"):
    return {"code": code, "severity": severity, "detail": "SAMPLE", "box": [1, 2, 3, 4]}


# ---- the happy path -------------------------------------------------------------------------

def test_clean_result_passes_screening_to_tray_1():
    assert decide(clean()) == (PASSED, 1, [])


def test_defaults_are_the_contract_defaults():
    assert intake.DEFAULTS == Thresholds(0.75, 0.5, 0.70, 0.60)


# ---- each rule alone, with its reason code --------------------------------------------------

@pytest.mark.parametrize(("changes", "reason"), [
    ({"quality__ok": False}, QUALITY_NOT_OK),
    ({"doc_type__predicted": "unknown", "doc_type__confidence": 0.95}, DOC_TYPE_UNKNOWN),
    ({"doc_type__confidence": 0.5}, DOC_TYPE_LOW_CONFIDENCE),
    ({"indicators": [indicator(0.9)]}, SEVERE_INDICATOR),
    ({"checks__layout_score": 0.4}, LAYOUT_SCORE_LOW),
    ({"similarity__score": 0.3}, SIMILARITY_LOW),
    ({"checks__expected_headings_found": 3}, HEADINGS_MISSING),
])
def test_each_rule_sends_the_document_to_manual_review(changes, reason):
    assert decide(clean(**changes)) == (REVIEW, 2, [reason])


# ---- boundaries -----------------------------------------------------------------------------

@pytest.mark.parametrize(("confidence", "fires"), [
    (0.75, False), (0.7499, True), (0.76, False), (0.0, True), (1.0, False), (1, False)])
def test_doc_type_confidence_boundary(confidence, fires):
    _, _, reasons = decide(clean(doc_type__confidence=confidence))
    assert (DOC_TYPE_LOW_CONFIDENCE in reasons) is fires


@pytest.mark.parametrize(("severity", "fires"), [
    (0.5, True), (0.4999, False), (0.0, False), (1.0, True), (0, False)])
def test_indicator_severity_boundary(severity, fires):
    _, _, reasons = decide(clean(indicators=[indicator(0.1), indicator(severity)]))
    assert (SEVERE_INDICATOR in reasons) is fires


def test_several_severe_indicators_give_one_reason():
    assert decide(clean(indicators=[indicator(0.6), indicator(0.9, "missing_field")]))[2] == [
        SEVERE_INDICATOR]


def test_mild_indicators_alone_still_pass():
    assert decide(clean(indicators=[indicator(0.2), indicator(0.49)])) == (PASSED, 1, [])


@pytest.mark.parametrize(("score", "fires"), [(0.70, False), (0.6999, True), (0.71, False)])
def test_layout_score_boundary(score, fires):
    assert (LAYOUT_SCORE_LOW in decide(clean(checks__layout_score=score))[2]) is fires


@pytest.mark.parametrize(("score", "fires"), [(0.60, False), (0.5999, True), (0.61, False)])
def test_similarity_boundary(score, fires):
    assert (SIMILARITY_LOW in decide(clean(similarity__score=score))[2]) is fires


@pytest.mark.parametrize(("found", "total", "fires"), [
    (4, 4, False), (3, 4, True), (0, 4, True), (0, 0, False), (5, 4, False)])
def test_headings_rule(found, total, fires):
    result = clean(checks__expected_headings_found=found, checks__expected_headings_total=total)
    assert (HEADINGS_MISSING in decide(result)[2]) is fires


# ---- optional blocks = not measured ---------------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"similarity": DELETE},
    {"similarity": None},
    {"similarity__score": None},
    {"similarity__score": DELETE},
    {"fields": DELETE},
    {"checks__seal_logo_score": None},
    {"checks__seal_logo_score": DELETE},
    {"timings_ms": DELETE, "capture": DELETE, "agent_version": DELETE},
])
def test_missing_optional_blocks_are_not_measured_not_failed(changes):
    assert decide(clean(**changes)) == (PASSED, 1, [])


def test_unknown_keys_are_ignored():
    result = clean()
    result["future_block"] = {"anything": [1, 2]}
    result["quality"]["new_metric"] = "x"
    result["checks"]["stamp_score"] = 0.01
    result["indicators"] = [{**indicator(0.1), "extra": True}]
    assert decide(result) == (PASSED, 1, [])


def test_an_unrecognised_type_name_counts_as_unknown():
    screening = parse(clean(doc_type__predicted="passport"))
    assert screening.doc_type == IntakeDocType.UNKNOWN and screening.predicted == "passport"
    assert decide(clean(doc_type__predicted="passport"))[2] == [DOC_TYPE_UNKNOWN]


@pytest.mark.parametrize("doc_type", [t for t in IntakeDocType if t != IntakeDocType.UNKNOWN])
def test_every_known_type_can_pass(doc_type):
    assert parse(clean(doc_type__predicted=doc_type.value)).doc_type == doc_type
    assert decide(clean(doc_type__predicted=doc_type.value))[0] == PASSED


@pytest.mark.parametrize("predicted", ["unknown", "passport"])
def test_an_unknown_page_without_checks_goes_to_manual_review(predicted):
    """The station leaves `checks` out when it can't tell the type (no profile to check)."""
    result = clean(doc_type__predicted=predicted, doc_type__confidence=0.3, checks=DELETE,
                   similarity=DELETE, fields=DELETE)
    assert decide(result) == (REVIEW, 2, [DOC_TYPE_UNKNOWN, DOC_TYPE_LOW_CONFIDENCE])


def test_a_known_type_without_checks_is_refused():
    with pytest.raises(InvalidScreeningResult):
        decide(clean(checks=DELETE))


# ---- several rules, settings ----------------------------------------------------------------

def test_every_rule_that_fires_is_listed_in_rule_order():
    result = clean(quality__ok=False, doc_type__predicted="unknown", doc_type__confidence=0.1,
                   indicators=[indicator(0.7)], checks__layout_score=0.1,
                   similarity__score=0.1, checks__expected_headings_found=0)
    assert decide(result) == (REVIEW, 2, [
        QUALITY_NOT_OK, DOC_TYPE_UNKNOWN, DOC_TYPE_LOW_CONFIDENCE, SEVERE_INDICATOR,
        LAYOUT_SCORE_LOW, SIMILARITY_LOW, HEADINGS_MISSING])


def test_thresholds_come_from_settings():
    strict = Thresholds(min_doc_type_confidence=0.95, max_indicator_severity=0.1,
                        min_layout_score=0.9, min_similarity_score=0.9)
    result = clean(indicators=[indicator(0.2)])
    assert decide(result) == (PASSED, 1, [])
    assert decide(result, strict)[2] == [DOC_TYPE_LOW_CONFIDENCE, SEVERE_INDICATOR,
                                         LAYOUT_SCORE_LOW, SIMILARITY_LOW]
    lenient = Thresholds(0.1, 1.0, 0.1, 0.1)
    assert decide(clean(doc_type__confidence=0.2, checks__layout_score=0.2,
                        similarity__score=0.2, indicators=[indicator(0.99)]), lenient)[0] == PASSED


def test_decide_does_not_modify_the_result():
    result = clean(indicators=[indicator(0.7)])
    before = copy.deepcopy(result)
    decide(result)
    assert result == before


def test_every_reason_has_plain_language_text_without_forbidden_words():
    codes = {v for k, v in vars(intake).items() if k.isupper() and isinstance(v, str)
             and v.islower() and "_" in v}
    assert codes <= set(intake.REASON_TEXT)
    for text in intake.REASON_TEXT.values():
        assert not {"authentic", "genuine", "verified", "fake"} & set(text.lower().split())


# ---- malformed input -> InvalidScreeningResult (HTTP 422) -----------------------------------

@pytest.mark.parametrize("changes", [
    {"schema": DELETE}, {"schema": 2}, {"schema": "1"}, {"schema": True},
    {"quality": DELETE}, {"quality": None}, {"quality": []},
    {"quality__ok": DELETE}, {"quality__ok": "true"}, {"quality__ok": 1}, {"quality__ok": None},
    {"doc_type": DELETE}, {"doc_type": "psa_birth_certificate"},
    {"doc_type__predicted": DELETE}, {"doc_type__predicted": ""}, {"doc_type__predicted": 3},
    {"doc_type__confidence": DELETE}, {"doc_type__confidence": "0.9"},
    {"doc_type__confidence": True}, {"doc_type__confidence": 1.2},
    {"doc_type__confidence": -0.1}, {"doc_type__confidence": float("nan")},
    {"doc_type__confidence": float("inf")},
    {"checks": DELETE}, {"checks__layout_score": DELETE}, {"checks__layout_score": None},
    {"checks__layout_score": 88}, {"checks__expected_headings_found": DELETE},
    {"checks__expected_headings_found": -1}, {"checks__expected_headings_found": 3.0},
    {"checks__expected_headings_total": True}, {"checks__expected_headings_total": "4"},
    {"indicators": DELETE}, {"indicators": None}, {"indicators": {}},
    {"indicators": ["altered_region"]}, {"indicators": [{"code": "altered_region"}]},
    {"indicators": [indicator("high")]}, {"indicators": [indicator(1.5)]},
    {"similarity": 0.8}, {"similarity__score": "0.8"}, {"similarity__score": 2},
])
def test_malformed_results_are_refused(changes):
    with pytest.raises(InvalidScreeningResult):
        decide(clean(**changes))


@pytest.mark.parametrize("value", [None, [], "result", 1])
def test_a_result_that_is_not_an_object_is_refused(value):
    with pytest.raises(InvalidScreeningResult):
        decide(value)


def test_load_result_parses_a_json_object():
    assert load_result(json.dumps(clean())) == clean()


@pytest.mark.parametrize("text", [
    "", "not json", "[1, 2]", "null", '"x"', '{"a": NaN}', '{"a": Infinity}', "[" * 100_000,
], ids=["empty", "text", "array", "null", "string", "nan", "infinity", "deeply-nested"])
def test_load_result_refuses_non_objects_and_bad_json(text):
    with pytest.raises(InvalidScreeningResult):
        load_result(text)


def test_load_result_refuses_oversized_results():
    with pytest.raises(InvalidScreeningResult, match="too large"):
        load_result(json.dumps({"pad": "x" * intake.MAX_RESULT_CHARS}))


def test_tray_follows_the_decision():
    assert intake.tray_for(PASSED) == 1 and intake.tray_for(REVIEW) == 2
