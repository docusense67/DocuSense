"""S-06/S-07 field review and matching (services/intake_fields.py): pure functions.

All names and numbers are synthetic SAMPLE values.
"""

from dataclasses import dataclass
from datetime import date

import pytest

from app.models.enums import IntakeDocType
from app.services import intake_fields as f

PSA = IntakeDocType.PSA_BIRTH_CERTIFICATE
CARD = IntakeDocType.JHS_REPORT_CARD
MORAL = IntakeDocType.GOOD_MORAL_CERTIFICATE
COMPLETION = IntakeDocType.JHS_COMPLETION_CERTIFICATE
TODAY = date(2026, 10, 12)


def read(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


# ---- schema -----------------------------------------------------------------------------------

def test_every_type_has_required_names_and_unknown_has_no_schema():
    for doc_type in (PSA, CARD, MORAL, COMPLETION):
        specs = {s.key: s for s in f.schema_for(doc_type)}
        assert specs["last_name"].required and specs["first_name"].required
        assert not specs["middle_name"].required
        assert specs["last_name"].student_attr == "last_name"
    with pytest.raises(f.UnknownDocType):
        f.schema_for(IntakeDocType.UNKNOWN)


def test_schemas_map_onto_student_attributes_where_they_exist():
    psa = {s.key: s.student_attr for s in f.schema_for(PSA)}
    assert psa["birth_date"] == "birthdate" and psa["birth_place"] == "birth_place"
    assert psa["mother_name"] == "mother_name" and psa["father_name"] == "father_name"
    card = {s.key: s.student_attr for s in f.schema_for(CARD)}
    assert card["lrn"] == "lrn"
    assert card["school_year"] is None and card["general_average"] is None
    assert {s.key for s in f.schema_for(CARD)} >= {"school_year", "grade_level", "section",
                                                   "general_average"}
    assert {s.key for s in f.schema_for(MORAL)} >= {"school_name", "date_issued"}


# ---- prefill ----------------------------------------------------------------------------------

def test_psa_prefill_maps_station_fields_and_normalizes():
    station = {
        "child_last_name": read("SAMPLE", 0.95), "child_first_name": read("JUANITO", 0.9),
        "child_middle_name": read("  PEREZ "), "sex": read("M", 0.8),
        "date_of_birth": read("MARCH 5, 2010", 0.7), "place_of_birth": read("QUEZON CITY"),
        "mother_first_name": read("MARIA", 0.9), "mother_middle_name": read("LOPEZ", 0.6),
        "mother_last_name": read("PEREZ", 0.95), "registry_no": read("2010-123"),
    }
    out = f.prefill(PSA, station)
    assert out["last_name"] == {"value": "SAMPLE", "read_as": "SAMPLE", "confidence": 0.95,
                                "derived": False}
    assert out["middle_name"]["value"] == "PEREZ"
    assert out["sex"]["value"] == "Male" and out["sex"]["read_as"] == "M"
    assert out["birth_date"]["value"] == "2010-03-05"
    assert out["mother_name"]["value"] == "MARIA LOPEZ PEREZ"
    assert out["mother_name"]["confidence"] == 0.6  # the weakest part
    assert out["father_name"] == {"value": None, "read_as": None, "confidence": None,
                                  "derived": False}
    assert "registry_no" not in out


def test_report_card_name_is_split_and_marked_derived():
    out = f.prefill(CARD, {"learner_name": read("SAMPLE DELA CRUZ, JUANITO P.", 88),
                           "lrn": read("1234 5678 9012"), "school_year": read("S.Y. 2025 - 2026"),
                           "grade": read("Grade 10")})
    assert out["last_name"]["value"] == "SAMPLE DELA CRUZ"
    assert out["first_name"]["value"] == "JUANITO" and out["middle_name"]["value"] == "P."
    assert out["first_name"]["derived"] is True
    assert out["first_name"]["confidence"] == 0.88  # 0..100 readers scaled to 0..1
    assert out["lrn"]["value"] == "123456789012"
    assert out["school_year"]["value"] == "2025-2026" and out["grade_level"]["value"] == "10"


@pytest.mark.parametrize("station", [None, [], {"learner_name": "flat string"},
                                     {"learner_name": {"value": {"x": 1}}}])
def test_prefill_tolerates_missing_or_odd_station_fields(station):
    out = f.prefill(CARD, station)
    assert all(v["value"] is None for v in out.values())


@pytest.mark.parametrize(("full", "parts"), [
    ("DELA CRUZ, JUAN P.", ("DELA CRUZ", "JUAN", "P.")),
    ("DELA CRUZ, JUAN CARLOS PEREZ", ("DELA CRUZ", "JUAN CARLOS", "PEREZ")),
    ("DELA CRUZ, JUAN", ("DELA CRUZ", "JUAN", None)),
    ("JUAN P. DELA CRUZ", ("DELA CRUZ", "JUAN", "P.")),
    ("JUAN SAMPLE", ("SAMPLE", "JUAN", None)),
    ("SAMPLE", ("SAMPLE", None, None)),
])
def test_split_full_name(full, parts):
    out = f.split_full_name(full)
    assert (out["last"], out["first"], out["middle"]) == parts


@pytest.mark.parametrize(("printed", "iso"), [
    ("2010-03-05", "2010-03-05"), ("March 5, 2010", "2010-03-05"), ("MAR 5, 2010", "2010-03-05"),
    ("03/05/2010", "2010-03-05"), ("5 March 2010", "2010-03-05"),
    ("5th day of June, 2024", "2024-06-05"), ("Issued this 21st day of May 2025", "2025-05-21"),
])
def test_printed_dates(printed, iso):
    assert f.parse_printed_date(printed).isoformat() == iso


def test_unreadable_date_is_left_for_staff():
    assert f.parse_printed_date("sometime in 2010") is None
    assert f.prefill(MORAL, {"date_issued": read("sometime")})["date_issued"]["value"] == "sometime"


# ---- validation -------------------------------------------------------------------------------

def test_valid_psa_set_is_cleaned():
    values, errors = f.clean_values(PSA, {
        "last_name": " Sample ", "first_name": "Juanito", "middle_name": "",
        "sex": "female", "birth_date": "2010-03-05", "mother_name": "Maria  L. Perez"}, TODAY)
    assert errors == {}
    assert values["last_name"] == "Sample" and values["middle_name"] is None
    assert values["sex"] == "Female" and values["mother_name"] == "Maria L. Perez"
    assert values["father_name"] is None  # missing keys are empty


@pytest.mark.parametrize(("doc_type", "key", "value", "message"), [
    (PSA, "last_name", "", "required"),
    (PSA, "first_name", None, "required"),
    (PSA, "birth_date", None, "required"),
    (PSA, "first_name", "Juan3", "Letters"),
    (PSA, "birth_date", "2010-02-30", "real date"),
    (PSA, "birth_date", "20100305", "real date"),
    (PSA, "birth_date", "2027-01-01", "future"),
    (PSA, "birth_date", "1899-12-31", "1900"),
    (PSA, "sex", "X", "Male or Female"),
    (PSA, "birth_place", "x" * 201, "At most 200"),
    (CARD, "lrn", "12345678901", "12 digits"),
    (CARD, "lrn", "12345678901A", "12 digits"),
    (CARD, "school_year", "2023-2025", "School year"),
    (CARD, "grade_level", "13", "1 to 12"),
    (CARD, "general_average", "59.9", "between 60 and 100"),
    (CARD, "general_average", "100.5", "between 60 and 100"),
    (CARD, "general_average", "ninety", "number"),
    (CARD, "general_average", "90.1234", "3 decimal"),
    (MORAL, "date_issued", "June 5", "real date"),
])
def test_validation_rules(doc_type, key, value, message):
    base = {"last_name": "Sample", "first_name": "Juanito", "birth_date": "2010-03-05"}
    _, errors = f.clean_values(doc_type, {**base, key: value}, TODAY)
    assert message in errors[key]


def test_lrn_with_spaces_and_good_values_pass():
    values, errors = f.clean_values(CARD, {
        "last_name": "Sample", "first_name": "Juanito", "lrn": "1234 5678 9012",
        "school_year": "2025-2026", "grade_level": "Grade 10", "general_average": "89.50"}, TODAY)
    assert errors == {}
    assert values["lrn"] == "123456789012" and values["grade_level"] == "10"
    assert values["general_average"] == "89.5"


def test_unknown_keys_and_non_text_are_errors():
    _, errors = f.clean_values(MORAL, {"last_name": "Sample", "first_name": True,
                                       "lrn": "123456789012"}, TODAY)
    assert errors["lrn"] == "Not a field of this document type"
    assert errors["first_name"] == "Must be text"


def test_changed_fields_lists_names_only():
    assert f.changed_fields({"a": "x", "b": None, "c": "y"},
                            {"a": "x", "b": "", "c": "z", "d": "new"}) == ["c", "d"]


def test_student_values():
    values, _ = f.clean_values(PSA, {"last_name": "Sample", "first_name": "Juanito",
                                     "birth_date": "2010-03-05", "birth_place": "Quezon City"},
                               TODAY)
    assert f.student_values(PSA, values) == {
        "last_name": "Sample", "first_name": "Juanito", "birthdate": date(2010, 3, 5),
        "birth_place": "Quezon City"}
    card, _ = f.clean_values(CARD, {"last_name": "Sample", "first_name": "Juanito",
                                    "lrn": "123456789012", "school_year": "2025-2026"}, TODAY)
    assert f.student_values(CARD, card) == {"last_name": "Sample", "first_name": "Juanito",
                                            "lrn": "123456789012"}  # school year stays on the doc


# ---- matching ---------------------------------------------------------------------------------

@dataclass
class S:
    id: int
    student_number: str
    last_name: str
    first_name: str
    birthdate: date | None = None
    lrn: str | None = None
    middle_name: str | None = None


BIRTH = date(2010, 3, 5)
DOC = {"last_name": "SAMPLE DELA CRUZ", "first_name": "JUANITO", "birth_date": "2010-03-05",
       "lrn": "123456789012"}


def matches(students, values=DOC):
    return [(c["student_id"], c["match"], c["strength"], c["conflicts"])
            for c in f.match_candidates(values, students)]


def test_candidates_are_ranked_lrn_first_then_name_and_birth_date():
    students = [
        S(1, "S-1", "Other", "Person"),  # no match
        S(2, "S-2", "Sample Dela Cruz", "Juanito"),  # name, no birth date on record
        S(3, "S-3", "Samplé Delacruz", "JUANITO", BIRTH),  # name + birth date (accent/space)
        S(4, "S-4", "Unrelated", "Name", lrn="123456789012"),  # same LRN, other name
        S(5, "S-5", "Sample Dela Cruz", "Juanito", date(2011, 1, 1)),  # birth date differs
        S(6, "S-6", "Santos", "Juanito", BIRTH),  # first name + birth date
    ]
    assert matches(students) == [
        (4, "lrn", "possible", ["name_differs"]),
        (3, "name_birthdate", "strong", []),
        (2, "name", "possible", []),
        (5, "name_birthdate_differs", "possible", ["birthdate_differs"]),
        (6, "first_name_birthdate", "possible", ["name_differs"]),
    ]


def test_lrn_and_name_agreeing_is_strong_and_lrn_conflict_is_reported():
    assert matches([S(1, "S-1", "SAMPLE DELA CRUZ", "Juanito", BIRTH, "123456789012")]) == [
        (1, "lrn", "strong", [])]
    assert matches([S(1, "S-1", "SAMPLE DELA CRUZ", "Juanito", BIRTH, "999999999999")]) == [
        (1, "name_birthdate", "possible", ["lrn_differs"])]


def test_no_names_no_lrn_no_candidates():
    assert matches([S(1, "S-1", "Sample", "Juanito", BIRTH)], {"school_name": "x"}) == []


def test_name_key_ignores_case_accents_spaces_and_punctuation():
    assert f.name_key("Dela Cruz") == f.name_key("DELACRUZ") == f.name_key("dela-cruz")
    assert f.name_key("Peña") == f.name_key("PENA")
    assert f.name_key(None) == ""
