from decimal import Decimal

import pytest

from app.models.enums import GradeRemark
from app.services.grading import GradeError, compute_gwa, parse_grade


@pytest.mark.parametrize(
    ("raw", "value", "remarks"),
    [
        ("1.25", Decimal("1.25"), GradeRemark.PASSED),
        ("1.5", Decimal("1.50"), GradeRemark.PASSED),
        (" 3.00 ", Decimal("3.00"), GradeRemark.PASSED),
        ("5", Decimal("5.00"), GradeRemark.FAILED),
        ("inc", None, GradeRemark.INC),
        ("DRP", None, GradeRemark.DRP),
    ],
)
def test_parse_valid_grades(raw, value, remarks):
    parsed = parse_grade(raw)
    assert parsed.value == value
    assert parsed.remarks == remarks


@pytest.mark.parametrize("raw", ["1.26", "4.00", "0.75", "A", "", "1.2.5"])
def test_parse_rejects_off_scale(raw):
    with pytest.raises(GradeError):
        parse_grade(raw)


def test_gwa_is_units_weighted_and_rounded_half_up():
    rows = [(Decimal("1.25"), Decimal("3")), (Decimal("2.00"), Decimal("3")),
            (Decimal("1.00"), Decimal("2"))]
    # (3.75 + 6.00 + 2.00) / 8 = 1.46875 -> 1.47
    assert compute_gwa(rows) == Decimal("1.47")


def test_gwa_excludes_inc_and_drp():
    rows = [(Decimal("1.50"), Decimal("3")), (None, Decimal("3"))]
    assert compute_gwa(rows) == Decimal("1.50")


def test_gwa_none_when_no_numeric_grades():
    assert compute_gwa([(None, Decimal("3"))]) is None
    assert compute_gwa([]) is None
