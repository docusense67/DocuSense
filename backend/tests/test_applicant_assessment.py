"""Applicant intake, phase 2: the pure assessment of an upload against reference samples.

services/applicant_references.assess and services/applicant_intake.assess_upload: every reason
code fires on its own and has plain-language text; a match lets the upload pass screening; the
layout model only runs with labelled known-good AND altered training samples, and only flags.
All data is synthetic.
"""

import pytest

from app.models.enums import IntakeDecision, IntakeDocType
from app.services import applicant_intake as rules
from app.services import applicant_references as refs
from app.services import intake as screening

TOR = IntakeDocType.TRANSCRIPT_OF_RECORDS
COE = IntakeDocType.CERTIFICATE_OF_ENROLLMENT
SCHOOL = "Sample College of Arts"


def page(shift=0.0, height=0.02, gap=0.01, line_gap=0.04, lines=30, anchors=None):
    return {"anchors": anchors if anchors is not None else {
        "transcript": [0.3 + shift, 0.1], "name": [0.1, 0.2 + shift], "registrar": [0.7, 0.9]},
        "median_word_height": height, "median_word_gap": gap, "median_line_gap": line_gap,
        "line_count": lines}


def layout(*pages):
    return {"pages": list(pages) or [page()]}


FIELDS = {"student_name": {"value": "SAMPLEREYES, LUZVIMINDA C.", "confidence": None},
          "student_number": {"value": "OLD-2020-0042", "confidence": None}}


def reading(predicted="transcript_of_records", confidence=0.9, quality_ok=True, fields=None,
            lay=None, with_layout=True):
    out = {"schema": 1, "quality": {"ok": quality_ok},
           "doc_type": {"predicted": predicted, "confidence": confidence},
           "fields": FIELDS if fields is None else fields}
    if with_layout:
        out["layout"] = lay or layout()
    return out


def ref(id=1, version=1, school=SCHOOL, doc_type=TOR, label="known_good_sample", use="training",
        rules_=None, lay=None, text="transcript of records"):
    parsed = refs.parse_layout(lay if lay is not None else layout()) if lay is not False else None
    return refs.RefInfo(id=id, version=version, issuing_school=school,
                        school_key=refs.school_key(school), doc_type=doc_type.value, label=label,
                        use=use, rules=refs.parse_rules(rules_), layout=parsed, text=text)


def outcome(result=None, declared=TOR, school=SCHOOL, references=()):
    return rules.assess_upload(result or reading(), declared, school, list(references))


# ---- school names, rules, layouts -------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Sample College of Arts", "  sample college of arts ", "SAMPLE COLLEGE OF ARTS.",
    "The Sample College of Arts", "Sample Coll. of Arts", "Sample Collège of Arts",
])
def test_school_names_match_after_normalising(name):
    assert refs.school_key(name) == refs.school_key(SCHOOL) == "sample college of arts"


def test_different_schools_do_not_match():
    assert refs.school_key("Sample College of Arts & Sciences") == (
        "sample college of arts and sciences")
    assert refs.school_key("Sample College of Arts and Sciences") != refs.school_key(SCHOOL)


def test_rules_get_defaults_and_are_validated():
    assert refs.parse_rules(None) == {"required_fields": ["student_name"], "field_labels": {},
                                      "layout_tolerance": 0.15}
    parsed = refs.parse_rules({"required_fields": ["student_name", "gwa", "gwa"],
                               "field_labels": {"gwa": [" GWA ", "General average"]},
                               "layout_tolerance": 0.1})
    assert parsed == {"required_fields": ["student_name", "gwa"],
                      "field_labels": {"gwa": ["GWA", "General average"]},
                      "layout_tolerance": 0.1}


@pytest.mark.parametrize("bad", [
    [], {"colour": 1}, {"required_fields": "student_name"}, {"required_fields": ["Bad Name"]},
    {"field_labels": {"gwa": []}}, {"field_labels": {"gwa": ["x" * 100]}},
    {"field_labels": {"GWA": ["gwa"]}}, {"layout_tolerance": 0.9},
    {"layout_tolerance": True}, {"layout_tolerance": "0.1"},
])
def test_malformed_rules_are_refused(bad):
    with pytest.raises(refs.InvalidRules):
        refs.parse_rules(bad)


@pytest.mark.parametrize("bad", [
    [], {"pages": []}, {"pages": "x"}, {"pages": [{"anchors": {"Transcript": [0.1, 0.1]}}]},
    {"pages": [{"anchors": {"transcript": [0.1]}}]},
    {"pages": [{"anchors": {"transcript": [0.1, 1.5]}}]},
    {"pages": [{"median_word_height": -0.1}]}, {"pages": [{"line_count": 2.5}]},
    {"pages": [{"median_word_gap": float("nan")}]},
])
def test_malformed_layouts_are_refused_as_malformed_readings(bad):
    with pytest.raises(refs.InvalidLayout):
        refs.parse_layout(bad)
    with pytest.raises(rules.InvalidReading):
        rules.parse_reading({**reading(), "layout": bad})


def test_a_reading_without_layout_is_still_valid():
    assert rules.parse_reading(reading(with_layout=False)).layout is None


# ---- the reference used -----------------------------------------------------------------------

def test_the_latest_approved_known_good_training_reference_of_the_school_and_type_is_used():
    candidates = [ref(id=1, version=1), ref(id=2, version=3), ref(id=3, version=2),
                  ref(id=4, version=9, use="held_out"),
                  ref(id=5, version=9, label="altered_sample"),
                  ref(id=6, version=9, doc_type=COE), ref(id=7, version=9, school="Other School")]
    assert refs.pick_reference(candidates, "the sample college of arts", TOR.value).id == 2
    assert refs.pick_reference(candidates, "Unknown Academy", TOR.value) is None


def test_without_a_reference_the_upload_needs_manual_review():
    out = outcome(references=[ref(school="Other School")])
    assert out.decision == IntakeDecision.MANUAL_REVIEW
    assert out.reasons == [screening.NO_REFERENCE_PROFILE]
    assert out.assessment.as_json()["reference"] is None


def test_a_matching_reference_lets_the_upload_pass_screening():
    out = outcome(references=[ref(id=4, version=2)])
    assert out.decision == IntakeDecision.PASSED_SCREENING and out.reasons == []
    saved = out.assessment.as_json()
    assert saved["reference"]["id"] == 4 and saved["reference"]["version"] == 2
    assert saved["findings"] == [] and saved["layout_model"]["status"] == "unavailable"


def test_other_rules_still_run_when_a_reference_matches():
    out = outcome(reading(confidence=0.4), references=[ref()])
    assert out.reasons == [screening.DOC_TYPE_LOW_CONFIDENCE]
    out = outcome(reading(predicted="certificate_of_enrollment"), references=[ref()])
    assert out.reasons == [screening.DECLARED_TYPE_MISMATCH]


# ---- each finding, with its reason code -------------------------------------------------------

@pytest.mark.parametrize(("upload", "rules_", "expected", "detail"), [
    (reading(fields={"student_number": {"value": "X-1"}}), None,
     screening.REFERENCE_FIELD_MISSING, {"field": "student_name"}),
    (reading(), {"required_fields": ["student_name", "gwa"]},
     screening.REFERENCE_FIELD_MISSING, {"field": "gwa"}),
    (reading(with_layout=False), None, screening.LAYOUT_NOT_MEASURED, {"side": "upload"}),
    (reading(lay=layout(page(), page())), None, screening.PAGE_COUNT_DIFFERS,
     {"pages": 2, "expected": 1}),
    (reading(lay=layout(page(shift=0.2))), None, screening.LAYOUT_SHIFTED,
     {"page": 1, "anchor": "name"}),
    (reading(lay=layout(page(anchors={"transcript": [0.3, 0.1]}))), None,
     screening.REFERENCE_REGIONS_MISSING, {"page": 1, "anchors": ["name", "registrar"]}),
    (reading(lay=layout(page(height=0.05))), None, screening.TEXT_SPACING_DIFFERS,
     {"page": 1, "metric": "word_height"}),
    (reading(lay=layout(page(line_gap=0.01))), None, screening.TEXT_SPACING_DIFFERS,
     {"metric": "line_gap"}),
])
def test_each_difference_is_a_reason_with_plain_text(upload, rules_, expected, detail):
    out = outcome(upload, references=[ref(rules_=rules_)])
    assert out.decision == IntakeDecision.MANUAL_REVIEW
    assert out.reasons == [expected]
    assert expected in screening.REASON_TEXT
    finding = next(f for f in out.assessment.findings if f["code"] == expected)
    assert detail.items() <= finding.items()


def test_a_wider_tolerance_accepts_a_small_shift():
    assert outcome(reading(lay=layout(page(shift=0.1))), references=[ref()]).reasons == []
    shifted = reading(lay=layout(page(shift=0.18)))  # beyond the default 0.15
    assert outcome(shifted, references=[ref()]).reasons == [screening.LAYOUT_SHIFTED]
    assert outcome(shifted, references=[ref(rules_={"layout_tolerance": 0.2})]).reasons == []


def test_a_reference_without_layout_cannot_be_compared():
    out = outcome(references=[ref(lay=False)])
    assert out.reasons == [screening.LAYOUT_NOT_MEASURED]
    assert out.assessment.findings[-1]["side"] == "reference"


def test_reasons_are_listed_in_rule_order_without_repeats():
    upload = reading(quality_ok=False, fields={},
                     lay=layout(page(shift=0.3, height=0.06), page()))
    out = outcome(upload, references=[ref()])
    assert out.reasons == [screening.UPLOAD_QUALITY_NOT_OK, screening.REFERENCE_FIELD_MISSING,
                           screening.PAGE_COUNT_DIFFERS, screening.LAYOUT_SHIFTED,
                           screening.TEXT_SPACING_DIFFERS]


# ---- the nearest-neighbour layout model -------------------------------------------------------

GOOD, ALTERED = page(), page(height=0.035, gap=0.03, lines=12)


def labelled(extra=()):
    return [ref(id=1, lay=layout(GOOD)), ref(id=2, label="altered_sample", lay=layout(ALTERED)),
            *extra]


def test_the_layout_model_needs_known_good_and_altered_training_samples():
    model = refs.layout_model(refs.parse_layout(layout()), [ref()], SCHOOL, TOR.value)
    assert model["status"] == "unavailable"
    held_out_altered = ref(id=2, label="altered_sample", use="held_out", lay=layout(ALTERED))
    model = refs.layout_model(refs.parse_layout(layout()), [ref(), held_out_altered], SCHOOL,
                              TOR.value)
    assert model["status"] == "unavailable"  # held-out samples never train it
    other_school = ref(id=3, label="altered_sample", school="Other School", lay=layout(ALTERED))
    assert refs.layout_model(refs.parse_layout(layout()), [ref(), other_school], SCHOOL,
                             TOR.value)["status"] == "unavailable"


def test_the_layout_model_places_an_upload_and_records_its_version():
    model = refs.layout_model(refs.parse_layout(layout(GOOD)), labelled(), SCHOOL, TOR.value)
    assert model["status"] == "evaluated" and model["preliminary"] is True
    assert model["label"] == "known_good_sample" and model["confidence"] == 1.0
    assert model["training_reference_ids"] == [1, 2]
    assert model["version"].startswith("layout-1nn-")
    assert model["held_out_count"] == 0 and model["held_out_accuracy"] is None
    out = outcome(reading(lay=layout(GOOD)), references=labelled())
    assert out.decision == IntakeDecision.PASSED_SCREENING  # the model agrees: nothing fires


def test_the_layout_model_flags_but_never_passes_on_its_own():
    # Close to the altered sample (its text is bigger): the model sends it to manual review.
    out = outcome(reading(lay=layout(ALTERED)), references=labelled())
    assert screening.LAYOUT_MODEL_REVIEW in out.reasons
    assert out.assessment.model["label"] == "altered_sample"
    # Halfway between the two: not confident, so it flags too.
    between = page(height=0.0275, gap=0.02, lines=21)
    model = refs.layout_model(refs.parse_layout(layout(between)), labelled(), SCHOOL, TOR.value)
    assert model["confidence"] < refs.LAYOUT_MODEL_MIN_CONFIDENCE and refs.model_flags(model)
    assert screening.LAYOUT_MODEL_REVIEW in screening.REASON_TEXT


def test_held_out_samples_evaluate_the_layout_model():
    held = [ref(id=5, use="held_out", lay=layout(page(lines=29))),
            ref(id=6, label="altered_sample", use="held_out", lay=layout(page(lines=29)))]
    model = refs.layout_model(refs.parse_layout(layout(GOOD)), labelled(held), SCHOOL,
                              TOR.value)
    assert model["held_out_count"] == 2 and model["held_out_accuracy"] == 0.5
    assert 5 not in model["training_reference_ids"]


# ---- classifier corpus and the second pass ----------------------------------------------------

def test_classifier_corpus_marks_training_held_out_and_other_samples():
    corpus = refs.classifier_corpus([
        ref(id=1), ref(id=2, use="held_out"),
        ref(id=3, doc_type=IntakeDocType.UNKNOWN, label="other", text="parking ticket"),
        ref(id=4, text="  ")])
    assert [(c["id"], c["class"], c["use"]) for c in corpus] == [
        (1, "transcript_of_records", "training"), (2, "transcript_of_records", "held_out"),
        (3, "unknown", "training")]


def test_compare_passes_lists_field_names_that_differ():
    first = {"student_name": {"value": "SAMPLEREYES, LUZ"}, "program": {"value": "BA  Sample"},
             "student_number": {"value": "OLD-1"}, "school_name": {"value": None}}
    second = {"student_name": {"value": "samplereyes, luz"}, "program": {"value": "BA Sample"},
              "student_number": {"value": "OLD-7"}, "date_issued": {"value": "June 3"}}
    assert rules.compare_passes(first, second) == ["date_issued", "student_number"]
    assert rules.compare_passes({}, {}) == []


def test_reason_texts_keep_the_screening_wording():
    for code, text in screening.REASON_TEXT.items():
        lowered = text.lower()
        for word in ("authentic", "genuine", "verified"):
            assert word not in lowered, (code, word)
