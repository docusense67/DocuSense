"""Demo dataset from Section 23: 1 program, 8 subjects over 2 terms, 3 students.

Run: python -m app.seed   (idempotent; skips if the demo program already exists)
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.documents.defaults import load_html
from app.documents.registry import REGISTRY
from app.documents.service import validate_template
from app.models import (
    ClassSection,
    DocumentTemplate,
    Enrollment,
    Grade,
    Program,
    Student,
    Subject,
    Teacher,
    Term,
    User,
)
from app.models.enums import GradeRemark, Role, Semester, TemplateStatus
from app.security import hash_password

DEMO_PASSWORD = "ChangeMe-2026"  # every demo account must change it on first login

SUBJECTS = [
    # (code, title, units, term index)
    ("IT101", "Introduction to Computing", "3.0", 0),
    ("IT102", "Computer Programming 1", "3.0", 0),
    ("GE101", "Understanding the Self", "3.0", 0),
    ("GE102", "Purposive Communication", "3.0", 0),
    ("IT103", "Computer Programming 2", "3.0", 1),
    ("IT104", "Data Structures and Algorithms", "3.0", 1),
    ("GE103", "Mathematics in the Modern World", "3.0", 1),
    ("PE101", "Physical Fitness", "2.0", 1),
]

STUDENTS = [
    # (student_number, first, middle, last, email, grades per subject; None = INC)
    ("2025-00001", "Maria", "Reyes", "Santos", "maria.santos",
     ["1.25", "1.50", "1.75", "1.25", "1.50", "2.00", "1.75", "1.00"]),
    ("2025-00002", "Juan", "Dizon", "Dela Cruz", "juan.delacruz",
     ["2.00", "2.25", "1.75", "2.50", "2.00", "3.00", "2.25", "1.50"]),
    # INC in a locked term, so a TOR request is rejected by eligibility (UC1 alternative flow).
    ("2025-00003", "Ana", "Lopez", "Garcia", "ana.garcia",
     ["1.75", "2.00", None, "1.75", "2.25", "2.00", "2.00", "1.25"]),
]

DOMAIN = "school.edu.ph"


def _user(db: Session, email: str, role: Role, **kw) -> User:
    user = User(email=email, password_hash=hash_password(DEMO_PASSWORD), role=role,
                must_change_password=True, **kw)
    db.add(user)
    db.flush()
    return user


def seed(db: Session) -> bool:
    if db.scalar(select(Program).where(Program.code == "BSIT")):
        return False

    _user(db, f"admin@{DOMAIN}", Role.ADMIN)
    registrar = _user(db, f"registrar@{DOMAIN}", Role.REGISTRAR, can_validate=True)
    teacher_user = _user(db, f"teacher@{DOMAIN}", Role.TEACHER)
    teacher = Teacher(user_id=teacher_user.id, employee_number="EMP-0001",
                      full_name="Prof. Jose Rizal Mercado", department="College of Computing")
    program = Program(code="BSIT", name="Bachelor of Science in Information Technology",
                      department="College of Computing", total_units=150)
    terms = [
        Term(academic_year="2025-2026", semester=Semester.FIRST, is_grades_locked=True),
        Term(academic_year="2025-2026", semester=Semester.SECOND, is_current=True),
    ]
    db.add_all([teacher, program, *terms])
    db.flush()

    sections = []
    for code, title, units, term_idx in SUBJECTS:
        subject = Subject(code=code, title=title, units=Decimal(units), program_id=program.id)
        db.add(subject)
        db.flush()
        section = ClassSection(subject_id=subject.id, term_id=terms[term_idx].id,
                               section_code="BSIT-1A", teacher_id=teacher.id)
        db.add(section)
        sections.append((section, terms[term_idx]))
    db.flush()

    for number, first, middle, last, handle, grades in STUDENTS:
        user = _user(db, f"{handle}@{DOMAIN}", Role.STUDENT)
        student = Student(user_id=user.id, student_number=number, first_name=first,
                          middle_name=middle, last_name=last, program_id=program.id, year_level=1)
        db.add(student)
        db.flush()
        for (section, term), value in zip(sections, grades, strict=True):
            enrollment = Enrollment(student_id=student.id, class_section_id=section.id)
            db.add(enrollment)
            db.flush()
            db.add(Grade(
                enrollment_id=enrollment.id,
                grade_value=None if value is None else Decimal(value),
                remarks=GradeRemark.INC if value is None else GradeRemark.PASSED,
                encoded_by=teacher_user.id,
                is_locked=term.is_grades_locked,
            ))

    install_default_templates(db, created_by=registrar.id)
    db.commit()
    return True


def install_default_templates(db: Session, created_by: int | None) -> list[str]:
    """Stand-in TOR/COE templates as active v1, until the school's official formats arrive.
    Skips any document type that already has a template. Caller commits."""
    installed = []
    for spec in REGISTRY.values():
        if db.scalar(select(DocumentTemplate.id).where(
                DocumentTemplate.document_type == spec.code).limit(1)):
            continue
        html = load_html(spec.default_html)
        check = validate_template(spec.code, html, spec.default_zones)
        if not check.can_activate:
            raise RuntimeError(f"Default {spec.code} template is invalid: {check}")
        db.add(DocumentTemplate(document_type=spec.code, version=1, html_content=html,
                                placeholder_list=check.placeholders,
                                field_zones=spec.default_zones, status=TemplateStatus.ACTIVE,
                                created_by=created_by))
        installed.append(str(spec.code))
    db.flush()
    return installed


if __name__ == "__main__":
    with SessionLocal() as session:
        created = seed(session)
    print("Demo data seeded." if created else "Demo data already present; nothing to do.")
    if created:
        print(f"All demo accounts use password {DEMO_PASSWORD!r} (forced change on first login).")
