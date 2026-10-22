"""Mapping layer (Section 13): database records -> the exact strings printed on a document.

Templates never query the database. Every value here is a string, so the stored snapshot
is exactly what was printed and the validator can compare OCR output against it directly.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassSection, Enrollment, Student, Term
from app.models.enums import EnrollmentStatus
from app.services.grading import compute_gwa
from app.services.records import academic_history

SEMESTER_LABEL = {"1st": "1st Semester", "2nd": "2nd Semester", "Summer": "Summer"}


class MissingDataError(Exception):
    """Required data is absent; generation must stop (Section 13, 'Preventing missing fields')."""

    def __init__(self, missing: list[str]):
        super().__init__("Missing required data: " + "; ".join(missing))
        self.missing = missing


@dataclass(frozen=True)
class BuildOptions:
    issued_on: date
    purpose: str = ""


def format_name(student: Student) -> str:
    """JUAN D. DELA CRUZ JR."""
    parts = [student.first_name]
    if student.middle_name:
        parts.append(f"{student.middle_name[0]}.")
    parts.append(student.last_name)
    if student.suffix:
        parts.append(student.suffix)
    return " ".join(p.strip() for p in parts if p and p.strip()).upper()


def format_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def ordinal_year(level: int) -> str:
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(level if level < 20 else level % 10, "th")
    return f"{level}{suffix} Year"


def format_units(units: Decimal) -> str:
    normalized = Decimal(units).normalize()
    return f"{normalized:f}"


def _common(student: Student, opts: BuildOptions, missing: list[str]) -> dict:
    if student.program is None:
        missing.append("student has no program")
    return {
        "student_name": format_name(student),
        "student_number": student.student_number,
        "program": student.program.name if student.program else "",
        "date_issued": format_date(opts.issued_on),
    }


def build_tor(db: Session, student: Student, opts: BuildOptions) -> dict:
    missing: list[str] = []
    values = _common(student, opts, missing)
    # Only finalized (locked) terms appear on a TOR; grades still being encoded never do.
    history = [t for t in academic_history(db, student.id) if t["is_grades_locked"]]
    if not history:
        missing.append("student has no completed (locked) terms")

    all_rows = []
    terms = []
    for term in history:
        subjects = []
        for s in term["subjects"]:
            if s["grade"] is None:
                missing.append(f"no grade for {s['subject_code']} ({term['academic_year']})")
            subjects.append({
                "subject_code": s["subject_code"],
                "subject_title": s["subject_title"],
                "units": format_units(s["units"]),
                "grade": s["grade"] or "",
                "remarks": s["remarks"] or "",
            })
        all_rows.extend(
            (Decimal(s["grade"]) if _is_numeric(s["grade"]) else None, Decimal(s["units"]))
            for s in term["subjects"]
        )
        terms.append({
            "academic_year": term["academic_year"],
            "semester": SEMESTER_LABEL[term["semester"]],
            "gwa": "" if term["gwa"] is None else str(term["gwa"]),
            "subjects": subjects,
        })

    gwa = compute_gwa(all_rows)
    if gwa is None and history:
        missing.append("no numeric grades to compute GWA")
    if missing:
        raise MissingDataError(missing)
    return {**values, "gwa": str(gwa), "terms": terms}


def build_coe(db: Session, student: Student, opts: BuildOptions) -> dict:
    missing: list[str] = []
    values = _common(student, opts, missing)
    if student.year_level is None:
        missing.append("student has no year level")
    if not opts.purpose.strip():
        missing.append("request has no purpose")

    term = db.scalar(select(Term).where(Term.is_current.is_(True)))
    if term is None:
        missing.append("no current term is set")
    else:
        enrolled = db.scalar(
            select(Enrollment.id).join(ClassSection)
            .where(Enrollment.student_id == student.id, ClassSection.term_id == term.id,
                   Enrollment.status == EnrollmentStatus.ENROLLED).limit(1)
        )
        if enrolled is None:
            missing.append("student is not enrolled in the current term")
    if missing:
        raise MissingDataError(missing)

    return {
        **values,
        "year_level": ordinal_year(student.year_level),
        "semester": SEMESTER_LABEL[term.semester.value],
        "academic_year": term.academic_year,
        "purpose": opts.purpose.strip(),
    }


def _is_numeric(grade: str | None) -> bool:
    return bool(grade) and grade[0].isdigit()
