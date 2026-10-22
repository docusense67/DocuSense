"""Grade scale and GWA rules (Section 5C). Pure functions: no DB access."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.config import get_settings
from app.models.enums import GradeRemark

SPECIAL_REMARKS = {"INC": GradeRemark.INC, "DRP": GradeRemark.DRP}
TWO_PLACES = Decimal("0.01")


class GradeError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedGrade:
    value: Decimal | None  # None for INC / DRP
    remarks: GradeRemark

    @property
    def display(self) -> str:
        return str(self.value) if self.value is not None else self.remarks.value


def parse_grade(raw: str) -> ParsedGrade:
    """Turn user/CSV input ("1.5", "1.50", "inc") into a validated grade."""
    text = raw.strip().upper()
    if text in SPECIAL_REMARKS:
        return ParsedGrade(None, SPECIAL_REMARKS[text])

    settings = get_settings()
    try:
        value = Decimal(text).quantize(TWO_PLACES)
    except InvalidOperation:
        raise GradeError(f"'{raw}' is not a grade") from None

    allowed = {Decimal(g) for g in settings.grade_scale}
    if value not in allowed:
        raise GradeError(f"{value} is not on the grading scale ({', '.join(settings.grade_scale)})")

    passed = value <= Decimal(settings.passing_grade)
    return ParsedGrade(value, GradeRemark.PASSED if passed else GradeRemark.FAILED)


def compute_gwa(rows: Iterable[tuple[Decimal | None, Decimal]]) -> Decimal | None:
    """Units-weighted average of numeric grades, rounded half-up to 2 decimals.

    INC/DRP rows (value None) are excluded. [Assumption] All subjects count, including PE;
    confirm with the registrar whether PE/NSTP are excluded from GWA.
    """
    total_points = Decimal(0)
    total_units = Decimal(0)
    for value, units in rows:
        if value is None:
            continue
        total_points += value * units
        total_units += units
    if total_units == 0:
        return None
    return (total_points / total_units).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
