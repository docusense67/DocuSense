"""Who may request which document (Sections 5E, 6 step 2). Returns human-readable reasons.

Separate from the context builders on purpose: eligibility explains *why a student may not
request* a document, while MissingDataError guards generation against blank fields.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassSection, Enrollment, Student, Term
from app.models.enums import DocumentType, EnrollmentStatus, GradeRemark, StudentStatus
from app.services.records import academic_history


def check_eligibility(db: Session, student: Student, doc_type: DocumentType) -> list[str]:
    reasons: list[str] = []
    if student.program_id is None:
        reasons.append("No program on record")

    if doc_type == DocumentType.TOR:
        if student.status == StudentStatus.INACTIVE:
            reasons.append("Student record is inactive")
        locked = [t for t in academic_history(db, student.id) if t["is_grades_locked"]]
        if not locked:
            reasons.append("No completed (locked) terms on record")
        for term in locked:
            label = f"{term['semester']} Sem AY {term['academic_year']}"
            for subject in term["subjects"]:
                if subject["grade"] is None:
                    reasons.append(f"Missing grade in {subject['subject_code']} ({label})")
                elif subject["remarks"] == GradeRemark.INC.value:
                    reasons.append(f"Incomplete grade in {subject['subject_code']} ({label})")

    elif doc_type == DocumentType.COE:
        if student.status != StudentStatus.ACTIVE:
            reasons.append("Student is not currently active")
        term = db.scalar(select(Term).where(Term.is_current.is_(True)))
        enrolled = term is not None and db.scalar(
            select(Enrollment.id).join(ClassSection)
            .where(Enrollment.student_id == student.id, ClassSection.term_id == term.id,
                   Enrollment.status == EnrollmentStatus.ENROLLED).limit(1)
        ) is not None
        if not enrolled:
            reasons.append("Not enrolled in the current term")

    return reasons
