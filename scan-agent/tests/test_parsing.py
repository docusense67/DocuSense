import pytest

from scan_agent.processing.ocr import OcrLine, Word
from scan_agent.processing.parsing import parse_footer, parse_table


def line(text: str, conf: float = 95.0) -> OcrLine:
    return OcrLine([Word(w, conf) for w in text.split()])


@pytest.mark.parametrize("text", [
    "TOR-2026-000001 | Page 2 of 3 | Ref 6d95476a59d4",
    "TOR-2026-000001 l Page 2 of 3 l Ref 6d95476a59d4",
    "TOR-2026-000001 Page 2 of 3 Ref 6d95476a59d4",
])
def test_footer_tolerates_separator_misreads(text):
    footer = parse_footer(line(text))
    assert (footer.document_number, footer.page, footer.pages, footer.ref) == (
        "TOR-2026-000001", 2, 3, "6d95476a59d4")


def test_footer_unreadable_returns_none():
    assert parse_footer(line("smudged text")) is None


def test_table_rows_terms_and_confidence():
    lines = [
        line("Code Descriptive Title Units Grade Remarks"),
        line("1st Semester, AY 2025-2026"),
        line("IT101 Introduction to Computing 3 1.25 Passed"),
        OcrLine([Word("IT102", 96), Word("Programming", 90), Word("3", 94),
                 Word("1.50", 61), Word("Passed", 95)]),
        line("Term GWA: 1.38"),
        line("2nd semester AY 2025 - 2026"),
        line("PE101 Physical Fitness 2 INC Incomplete"),
        line("*** Nothing follows ***"),
    ]
    table = parse_table(lines)
    assert [(r.subject_code, r.units, r.grade, r.term) for r in table.rows] == [
        ("IT101", "3", "1.25", "1st Semester, AY 2025-2026"),
        ("IT102", "3", "1.50", "1st Semester, AY 2025-2026"),
        ("PE101", "2", "INC", "2nd Semester, AY 2025-2026"),
    ]
    assert table.rows[1].confidence["grade"] == 61
    assert table.term_gwas == [{"term": "1st Semester, AY 2025-2026", "gwa": "1.38",
                                "confidence": 95.0}]
    assert table.unparsed == ["*** Nothing follows ***"]


def test_term_carries_over_from_previous_page():
    table = parse_table([line("GE101 Understanding the Self 3 1.75 Passed")],
                        current_term="2nd Semester, AY 2023-2024")
    assert table.rows[0].term == "2nd Semester, AY 2023-2024"


@pytest.mark.parametrize(("ocr", "grade"), [
    ("1:75", "1.75"), ("1,25", "1.25"), ("125", "1.25"), ("L25", "1.25"), ("2.0O", "2.00"),
    ("5.00", "5.00"), ("inc", "INC"), ("2.75.", "2.75"),
])
def test_grade_misreads_with_one_possible_meaning_are_fixed(ocr, grade):
    rows = parse_table([line(f"GE101 Understanding the Self 3 {ocr} Passed")]).rows
    assert [(r.subject_code, r.grade) for r in rows] == [("GE101", grade)]


def test_subject_code_leading_digit_misread_is_fixed():
    rows = parse_table([line("1T101 Introduction to Computing 3 1.25 Passed")]).rows
    assert rows[0].subject_code == "IT101"


@pytest.mark.parametrize(("ocr", "code"), [
    ("Cs101", "CS101"), ("cs101", "CS101"),  # case carries no meaning in a code
    ("C5204", "CS204"), ("C$203", "CS203"),  # a code starts with at least two letters
    ("FIL1O1", "FIL101"),                    # a letter between two digits is a digit
])
def test_subject_code_format_misreads_are_fixed(ocr, code):
    rows = parse_table([line(f"{ocr} Discrete Structures 1 3 2.75 Passed")]).rows
    assert [(r.subject_code, r.grade) for r in rows] == [(code, "2.75")]


@pytest.mark.parametrize(("ocr", "code"), [("CsS101", "CSS101"), ("CSi101", "CSI101")])
def test_an_extra_character_in_a_code_is_kept(ocr, code):
    """Only the case is restored. The extra letter stays, so the row doesn't match CS101
    and validation asks for review instead of trusting a guess."""
    rows = parse_table([line(f"{ocr} Discrete Structures 1 3 2.75 Passed")]).rows
    assert [r.subject_code for r in rows] == [code]


def test_a_code_without_two_leading_letters_stays_unparsed():
    out = parse_table([line("17101 Introduction to Computing 3 2.00 Passed")])
    assert out.rows == [] and len(out.unparsed) == 1


@pytest.mark.parametrize("ocr", ["lst", "Ist", "1st"])
def test_first_semester_ordinal_misread_keeps_the_term(ocr):
    out = parse_table([line(f"{ocr} Semester, AY 2024-2025"),
                       line("GE101 Understanding the Self 3 1.75 Passed")])
    assert [r.term for r in out.rows] == ["1st Semester, AY 2024-2025"]
    assert out.unparsed == []


@pytest.mark.parametrize("ocr", ["12", "1.2", "6.00", "1.255", "9.75", "2./5", "1/5", "2.7."])
def test_grades_that_cannot_be_read_unambiguously_stay_unparsed(ocr):
    """Not on the d.dd scale after fixing: leave it for review rather than guess."""
    out = parse_table([line(f"GE101 Understanding the Self 3 {ocr} Passed")])
    assert out.rows == [] and len(out.unparsed) == 1


def test_an_altered_grade_still_reads_as_altered():
    # Correction restores the format only; the printed digits (here 1.00) are kept.
    rows = parse_table([line("IT101 Introduction to Computing 3 100 Passed")]).rows
    assert rows[0].grade == "1.00"


def test_a_grade_point_read_as_a_gap_is_rejoined():
    rows = parse_table([line("IT101 Introduction to Computing 3 1 oo Passed")]).rows
    assert [(r.units, r.grade) for r in rows] == [("3", "1.00")]


def test_split_digits_without_units_before_them_are_not_joined():
    """'1 00' could be units 1 and a grade missing its first digit: leave it for review."""
    out = parse_table([line("IT101 Introduction to Computing 1 00 Passed")])
    assert out.rows == []


@pytest.mark.parametrize(("text", "code"), [
    ("NSTP1 National Service Training Program 1 3 2.00 Passed", "NSTP1"),
    ("PE 1 Physical Fitness 2 1.50 Passed", "PE1"),
    ("RIZAL Life and Works of Rizal 3 2.25 Passed", "RIZAL"),
    ("ELEC2 Web Systems and Technologies 3 1.75 Passed", "ELEC2"),
])
def test_codes_with_one_digit_or_none(text, code):
    rows = parse_table([line(text)]).rows
    assert [r.subject_code for r in rows] == [code]


@pytest.mark.parametrize("split", [
    # how OCR splits a row whose title wraps inside the cell
    ["FIL101 Kontekstwalisadong Komunikasyon sa", "Filipino 3 1.75 Passed"],
    ["FIL101 Kontekstwalisadong Komunikasyon sa", "3 1.75 Passed", "Filipino"],
    ["FIL101 Kontekstwalisadong Komunikasyon sa 3 1.75 Passed", "Filipino"],
])
def test_wrapped_title_rows_are_reassembled(split):
    out = parse_table([line("1st Semester, AY 2023-2024"), *map(line, split),
                       line("NSTP1 National Service Training Program 1 3 2.00 Passed")])
    assert [(r.subject_code, r.grade, r.term) for r in out.rows] == [
        ("FIL101", "1.75", "1st Semester, AY 2023-2024"),
        ("NSTP1", "2.00", "1st Semester, AY 2023-2024")]


def test_an_unfinished_row_is_reported_not_merged_into_the_next():
    out = parse_table([line("FIL101 Kontekstwalisadong Komunikasyon sa"),
                       line("NSTP1 National Service Training Program 1 3 2.00 Passed")])
    assert [r.subject_code for r in out.rows] == ["NSTP1"]
    assert out.unparsed == ["FIL101 Kontekstwalisadong Komunikasyon sa"]
