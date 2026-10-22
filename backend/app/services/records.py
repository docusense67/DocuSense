"""Academic history per student. Used by the records views and, later, TOR generation."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import ClassSection, Enrollment, Grade, Subject, Term
from app.models.enums import EnrollmentStatus, Semester
from app.services.grading import compute_gwa

SEMESTER_ORDER = {Semester.FIRST: 0, Semester.SECOND: 1, Semester.SUMMER: 2}


def term_sort_key(term: Term) -> tuple[str, int]:
    return term.academic_year, SEMESTER_ORDER[term.semester]


def academic_history(db: Session, student_id: int) -> list[dict]:
    """Terms in chronological order, each with its subjects, grades, and GWA."""
    rows = db.execute(
        select(Enrollment, Grade)
        .join(Enrollment.class_section)
        .outerjoin(Grade, Grade.enrollment_id == Enrollment.id)
        .options(joinedload(Enrollment.class_section).joinedload(ClassSection.subject),
                 joinedload(Enrollment.class_section).joinedload(ClassSection.term))
        .where(Enrollment.student_id == student_id,
               Enrollment.status == EnrollmentStatus.ENROLLED)
    ).all()

    by_term: dict[int, list[tuple[Subject, Grade | None]]] = defaultdict(list)
    terms: dict[int, Term] = {}
    for enrollment, grade in rows:
        section = enrollment.class_section
        terms[section.term_id] = section.term
        by_term[section.term_id].append((section.subject, grade))

    history = []
    for term in sorted(terms.values(), key=term_sort_key):
        entries = sorted(by_term[term.id], key=lambda e: e[0].code)
        subjects = [
            {
                "subject_code": subject.code,
                "subject_title": subject.title,
                "units": subject.units,
                "grade": None if grade is None else _display(grade),
                "remarks": None if grade is None else grade.remarks.value,
            }
            for subject, grade in entries
        ]
        gwa = compute_gwa(
            (grade.grade_value, subject.units) for subject, grade in entries if grade is not None
        )
        history.append({
            "term_id": term.id,
            "academic_year": term.academic_year,
            "semester": term.semester.value,
            "is_grades_locked": term.is_grades_locked,
            "subjects": subjects,
            "gwa": gwa,
        })
    return history


def _display(grade: Grade) -> str:
    if grade.grade_value is None:
        return grade.remarks.value
    return str(Decimal(grade.grade_value).quantize(Decimal("0.01")))
