"""Programs, subjects, terms, teachers, class sections, enrollments (Section 5C)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    advised_by,
    client_ip,
    ensure_section_view_access,
    get_active_user,
    get_or_404,
    require_roles,
    teacher_profile_id,
)
from app.db import get_db
from app.models import (
    ClassSection,
    Enrollment,
    Program,
    SectionAdviser,
    Student,
    Subject,
    Teacher,
    Term,
    User,
)
from app.models.enums import EnrollmentStatus, Role, Semester
from app.schemas import ReasonIn
from app.services import audit
from app.services.accounts import create_account

router = APIRouter(tags=["catalog"])
registrar = require_roles(Role.REGISTRAR)


def _add(db: Session, obj, what: str) -> None:
    """Insert and flush, turning a unique-constraint hit into a 409."""
    db.add(obj)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"{what} already exists") from None


# ---- Programs -------------------------------------------------------------------------------

class ProgramIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    department: str | None = None
    total_units: int | None = None


class ProgramOut(ProgramIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


@router.get("/programs", response_model=list[ProgramOut])
def list_programs(_: User = Depends(get_active_user), db: Session = Depends(get_db)):
    return db.scalars(select(Program).order_by(Program.code)).all()


@router.post("/programs", response_model=ProgramOut, status_code=status.HTTP_201_CREATED)
def create_program(body: ProgramIn, request: Request, actor: User = Depends(registrar),
                   db: Session = Depends(get_db)):
    program = Program(**body.model_dump())
    _add(db, program, "Program code")
    audit.record(db, "program_created", user_id=actor.id, entity_type="program",
                 entity_id=program.id, new_value=body.model_dump(), ip_address=client_ip(request))
    db.commit()
    return program


# ---- Subjects -------------------------------------------------------------------------------

class SubjectIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=200)
    units: Decimal = Field(gt=0, le=10)
    program_id: int | None = None  # null = general education


class SubjectOut(SubjectIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


@router.get("/subjects", response_model=list[SubjectOut])
def list_subjects(_: User = Depends(get_active_user), db: Session = Depends(get_db)):
    return db.scalars(select(Subject).order_by(Subject.code)).all()


@router.post("/subjects", response_model=SubjectOut, status_code=status.HTTP_201_CREATED)
def create_subject(body: SubjectIn, request: Request, actor: User = Depends(registrar),
                   db: Session = Depends(get_db)):
    if body.program_id is not None:
        get_or_404(db, Program, body.program_id)
    subject = Subject(**body.model_dump())
    _add(db, subject, "Subject code")
    audit.record(db, "subject_created", user_id=actor.id, entity_type="subject",
                 entity_id=subject.id, new_value=body.model_dump(mode="json"),
                 ip_address=client_ip(request))
    db.commit()
    return subject


# ---- Terms ----------------------------------------------------------------------------------

class TermIn(BaseModel):
    academic_year: str = Field(pattern=r"^\d{4}-\d{4}$")
    semester: Semester


class TermOut(TermIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_grades_locked: bool
    is_current: bool


@router.get("/terms", response_model=list[TermOut])
def list_terms(_: User = Depends(get_active_user), db: Session = Depends(get_db)):
    return db.scalars(select(Term).order_by(Term.academic_year, Term.semester)).all()


@router.post("/terms", response_model=TermOut, status_code=status.HTTP_201_CREATED)
def create_term(body: TermIn, request: Request, actor: User = Depends(registrar),
                db: Session = Depends(get_db)):
    start, end = (int(y) for y in body.academic_year.split("-"))
    if end != start + 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Academic year must span consecutive years, e.g. 2025-2026")
    term = Term(**body.model_dump())
    _add(db, term, "Term")
    audit.record(db, "term_created", user_id=actor.id, entity_type="term", entity_id=term.id,
                 new_value=body.model_dump(mode="json"), ip_address=client_ip(request))
    db.commit()
    return term


@router.post("/terms/{term_id}/set-current", response_model=TermOut)
def set_current_term(term_id: int, request: Request, actor: User = Depends(registrar),
                     db: Session = Depends(get_db)):
    term = get_or_404(db, Term, term_id)
    db.execute(update(Term).values(is_current=False))
    term.is_current = True
    audit.record(db, "term_set_current", user_id=actor.id, entity_type="term", entity_id=term.id,
                 ip_address=client_ip(request))
    db.commit()
    return term


# ---- Teachers -------------------------------------------------------------------------------

class TeacherIn(BaseModel):
    employee_number: str = Field(min_length=1, max_length=30)
    full_name: str = Field(min_length=1, max_length=200)
    department: str | None = None
    email: EmailStr | None = None  # creates a login when given with temporary_password
    temporary_password: str | None = None


class TeacherOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    employee_number: str
    full_name: str
    department: str | None
    user_id: int | None


@router.get("/teachers", response_model=list[TeacherOut])
def list_teachers(_: User = Depends(registrar), db: Session = Depends(get_db)):
    return db.scalars(select(Teacher).order_by(Teacher.full_name)).all()


@router.post("/teachers", response_model=TeacherOut, status_code=status.HTTP_201_CREATED)
def create_teacher(body: TeacherIn, request: Request, actor: User = Depends(registrar),
                   db: Session = Depends(get_db)):
    user_id = None
    if body.email:
        if not body.temporary_password:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "temporary_password is required to create a login")
        user_id = create_account(db, actor=actor, email=body.email, role=Role.TEACHER,
                                 temporary_password=body.temporary_password,
                                 ip_address=client_ip(request)).id
    teacher = Teacher(user_id=user_id, employee_number=body.employee_number,
                      full_name=body.full_name, department=body.department)
    _add(db, teacher, "Employee number")
    audit.record(db, "teacher_created", user_id=actor.id, entity_type="teacher",
                 entity_id=teacher.id,
                 new_value=body.model_dump(exclude={"temporary_password"}, mode="json"),
                 ip_address=client_ip(request))
    db.commit()
    return teacher


# ---- Class sections -------------------------------------------------------------------------

class SectionIn(BaseModel):
    subject_id: int
    term_id: int
    section_code: str = Field(min_length=1, max_length=20)
    teacher_id: int | None = None


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    subject_id: int
    term_id: int
    section_code: str
    teacher_id: int | None
    subject_code: str
    subject_title: str
    academic_year: str
    semester: Semester
    is_grades_locked: bool
    grades_submitted: bool
    # For a teacher (S-08): teaches it (may encode) and/or advises its block (view only).
    teaching: bool = False
    advising: bool = False


def _section_out(section: ClassSection, *, teaching: bool = False,
                 advising: bool = False) -> SectionOut:
    return SectionOut(
        id=section.id, subject_id=section.subject_id, term_id=section.term_id,
        section_code=section.section_code, teacher_id=section.teacher_id,
        subject_code=section.subject.code, subject_title=section.subject.title,
        academic_year=section.term.academic_year, semester=section.term.semester,
        is_grades_locked=section.term.is_grades_locked,
        grades_submitted=section.grades_submitted_at is not None,
        teaching=teaching, advising=advising,
    )


@router.get("/sections", response_model=list[SectionOut])
def list_sections(
    term_id: int | None = None,
    user: User = Depends(require_roles(Role.REGISTRAR, Role.TEACHER)),
    db: Session = Depends(get_db),
):
    """Registrar: every section. Teacher: the sections they teach plus every section of the
    block sections they advise (S-08), flagged so the UI offers encoding only where allowed."""
    query = select(ClassSection).order_by(ClassSection.id)
    if term_id is not None:
        query = query.where(ClassSection.term_id == term_id)
    if user.role != Role.TEACHER:
        return [_section_out(s) for s in db.scalars(query)]
    me = teacher_profile_id(user)
    query = query.add_columns(advised_by(me)).where(
        or_(ClassSection.teacher_id == me, advised_by(me)))
    return [_section_out(s, teaching=s.teacher_id == me, advising=bool(advising))
            for s, advising in db.execute(query)]


@router.post("/sections", response_model=SectionOut, status_code=status.HTTP_201_CREATED)
def create_section(body: SectionIn, request: Request, actor: User = Depends(registrar),
                   db: Session = Depends(get_db)):
    get_or_404(db, Subject, body.subject_id)
    get_or_404(db, Term, body.term_id)
    if body.teacher_id is not None:
        get_or_404(db, Teacher, body.teacher_id)
    section = ClassSection(**body.model_dump())
    _add(db, section, "Section")
    audit.record(db, "section_created", user_id=actor.id, entity_type="class_section",
                 entity_id=section.id, new_value=body.model_dump(), ip_address=client_ip(request))
    db.commit()
    db.refresh(section)
    return _section_out(section)


class SectionTeacherIn(BaseModel):
    teacher_id: int | None


@router.patch("/sections/{section_id}/teacher", response_model=SectionOut)
def assign_teacher(section_id: int, body: SectionTeacherIn, request: Request,
                   actor: User = Depends(registrar), db: Session = Depends(get_db)):
    section = get_or_404(db, ClassSection, section_id)
    if body.teacher_id is not None:
        get_or_404(db, Teacher, body.teacher_id)
    old = section.teacher_id
    section.teacher_id = body.teacher_id
    audit.record(db, "section_teacher_assigned", user_id=actor.id, entity_type="class_section",
                 entity_id=section.id, old_value={"teacher_id": old},
                 new_value={"teacher_id": body.teacher_id}, ip_address=client_ip(request))
    db.commit()
    return _section_out(section)


# ---- Class advisers (S-08) ------------------------------------------------------------------

class AdviserIn(BaseModel):
    term_id: int
    section_code: str = Field(min_length=1, max_length=20)
    teacher_id: int | None  # null removes the adviser
    reason: str | None = Field(default=None, max_length=500)


class AdviserOut(BaseModel):
    term_id: int
    academic_year: str
    semester: Semester
    section_code: str
    subjects: int  # class sections in this block
    students: int  # distinct students enrolled in any of them
    teacher_id: int | None
    teacher_name: str | None


def _blocks(db: Session, term_id: int | None) -> list[AdviserOut]:
    query = select(ClassSection.term_id, ClassSection.section_code).distinct()
    if term_id is not None:
        query = query.where(ClassSection.term_id == term_id)
    keys = sorted((tid, code) for tid, code in db.execute(query))
    terms = {t.id: t for t in db.scalars(select(Term).where(Term.id.in_({k[0] for k in keys})))}
    advisers = {(a.term_id, a.section_code): a for a in db.scalars(select(SectionAdviser))}
    teachers = {t.id: t for t in db.scalars(select(Teacher))}
    out = []
    for tid, code in keys:
        in_block = select(ClassSection.id).where(ClassSection.term_id == tid,
                                                 ClassSection.section_code == code)
        subjects = len(db.scalars(in_block).all())
        students = len(set(db.scalars(select(Enrollment.student_id).where(
            Enrollment.class_section_id.in_(in_block),
            Enrollment.status == EnrollmentStatus.ENROLLED))))
        adviser = advisers.get((tid, code))
        teacher = teachers.get(adviser.teacher_id) if adviser else None
        out.append(AdviserOut(term_id=tid, academic_year=terms[tid].academic_year,
                              semester=terms[tid].semester, section_code=code,
                              subjects=subjects, students=students,
                              teacher_id=teacher.id if teacher else None,
                              teacher_name=teacher.full_name if teacher else None))
    return out


@router.get("/section-advisers", response_model=list[AdviserOut])
def list_advisers(term_id: int | None = None, _: User = Depends(registrar),
                  db: Session = Depends(get_db)):
    """Block sections (term + section code) with their class adviser, if any."""
    return _blocks(db, term_id)


@router.put("/section-advisers", response_model=AdviserOut)
def set_adviser(body: AdviserIn, request: Request, actor: User = Depends(registrar),
                db: Session = Depends(get_db)):
    """Designate (or remove) the class adviser of a block section. Audited like teacher
    assignment; an optional reason is kept in the audit entry."""
    get_or_404(db, Term, body.term_id)
    code = body.section_code.strip()
    if db.scalar(select(ClassSection.id).where(ClassSection.term_id == body.term_id,
                                               ClassSection.section_code == code)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No class sections with this code in "
                                                       "this term")
    if body.teacher_id is not None:
        get_or_404(db, Teacher, body.teacher_id)
    current = db.scalar(select(SectionAdviser).where(SectionAdviser.term_id == body.term_id,
                                                     SectionAdviser.section_code == code))
    old = current.teacher_id if current else None
    if old != body.teacher_id:
        if body.teacher_id is None:
            db.delete(current)
        elif current is None:
            current = SectionAdviser(term_id=body.term_id, section_code=code,
                                     teacher_id=body.teacher_id, assigned_by=actor.id)
            db.add(current)
        else:
            current.teacher_id = body.teacher_id
            current.assigned_by = actor.id
        db.flush()
        audit.record(db, "section_adviser_assigned", user_id=actor.id,
                     entity_type="section_adviser", entity_id=f"{body.term_id}:{code}",
                     old_value={"teacher_id": old},
                     new_value={"teacher_id": body.teacher_id, "term_id": body.term_id,
                                "section_code": code,
                                **({"reason": body.reason} if body.reason else {})},
                     ip_address=client_ip(request))
        db.commit()
    return next(b for b in _blocks(db, body.term_id) if b.section_code == code)


class AdviseeOut(BaseModel):
    student_id: int
    student_number: str
    student_name: str
    program_code: str | None
    year_level: int | None


class AdvisorySectionOut(BaseModel):
    id: int
    subject_code: str
    subject_title: str
    teacher_name: str | None


class AdvisoryOut(BaseModel):
    term_id: int
    academic_year: str
    semester: Semester
    is_grades_locked: bool
    section_code: str
    sections: list[AdvisorySectionOut]
    students: list[AdviseeOut]


@router.get("/advisory", response_model=list[AdvisoryOut])
def my_advisory(user: User = Depends(require_roles(Role.TEACHER)),
                db: Session = Depends(get_db)):
    """The teacher's advisory classes: subjects of the block and the students enrolled in it.
    Only names, student numbers, program and year level; grades through the read-only
    grade sheet of each section."""
    me = teacher_profile_id(user)
    out = []
    advisories = db.scalars(select(SectionAdviser).where(SectionAdviser.teacher_id == me)
                            .order_by(SectionAdviser.term_id.desc(), SectionAdviser.section_code))
    for adv in advisories:
        sections = list(db.scalars(select(ClassSection).where(
            ClassSection.term_id == adv.term_id, ClassSection.section_code == adv.section_code)
            .order_by(ClassSection.id)))
        teachers = {t.id: t.full_name for t in db.scalars(select(Teacher).where(
            Teacher.id.in_({s.teacher_id for s in sections if s.teacher_id})))}
        students = db.scalars(select(Student).where(Student.id.in_(
            select(Enrollment.student_id).where(
                Enrollment.class_section_id.in_([s.id for s in sections]),
                Enrollment.status == EnrollmentStatus.ENROLLED)))
            .order_by(Student.last_name, Student.first_name))
        out.append(AdvisoryOut(
            term_id=adv.term_id, academic_year=adv.term.academic_year,
            semester=adv.term.semester, is_grades_locked=adv.term.is_grades_locked,
            section_code=adv.section_code,
            sections=[AdvisorySectionOut(id=s.id, subject_code=s.subject.code,
                                         subject_title=s.subject.title,
                                         teacher_name=teachers.get(s.teacher_id))
                      for s in sections],
            students=[AdviseeOut(student_id=s.id, student_number=s.student_number,
                                 student_name=f"{s.last_name}, {s.first_name}",
                                 program_code=s.program.code if s.program else None,
                                 year_level=s.year_level) for s in students],
        ))
    return out


# ---- Enrollments ----------------------------------------------------------------------------

class EnrollmentIn(BaseModel):
    student_id: int
    class_section_id: int


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    student_id: int
    class_section_id: int
    status: EnrollmentStatus


@router.get("/sections/{section_id}/enrollments", response_model=list[EnrollmentOut])
def list_enrollments(section_id: int,
                     user: User = Depends(require_roles(Role.REGISTRAR, Role.TEACHER)),
                     db: Session = Depends(get_db)):
    ensure_section_view_access(db, user, get_or_404(db, ClassSection, section_id))
    return db.scalars(select(Enrollment).where(Enrollment.class_section_id == section_id)).all()


@router.post("/enrollments", response_model=EnrollmentOut, status_code=status.HTTP_201_CREATED)
def enroll(body: EnrollmentIn, request: Request, actor: User = Depends(registrar),
           db: Session = Depends(get_db)):
    get_or_404(db, Student, body.student_id)
    section = get_or_404(db, ClassSection, body.class_section_id)
    if section.term.is_grades_locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Term is locked")
    enrollment = Enrollment(**body.model_dump())
    _add(db, enrollment, "Enrollment")
    audit.record(db, "student_enrolled", user_id=actor.id, entity_type="enrollment",
                 entity_id=enrollment.id, new_value=body.model_dump(),
                 ip_address=client_ip(request))
    db.commit()
    return enrollment


@router.post("/enrollments/{enrollment_id}/drop", response_model=EnrollmentOut)
def drop_enrollment(enrollment_id: int, body: ReasonIn, request: Request,
                    actor: User = Depends(registrar), db: Session = Depends(get_db)):
    enrollment = get_or_404(db, Enrollment, enrollment_id)
    if enrollment.class_section.term.is_grades_locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Term is locked")
    enrollment.status = EnrollmentStatus.DROPPED
    audit.record(db, "enrollment_dropped", user_id=actor.id, entity_type="enrollment",
                 entity_id=enrollment.id, new_value={"reason": body.reason},
                 ip_address=client_ip(request))
    db.commit()
    return enrollment


class BulkEnrollIn(BaseModel):
    student_numbers: list[str] = Field(min_length=1, max_length=500)


@router.post("/sections/{section_id}/enroll", status_code=status.HTTP_201_CREATED)
def enroll_by_number(section_id: int, body: BulkEnrollIn, request: Request,
                     actor: User = Depends(registrar), db: Session = Depends(get_db)):
    """Enroll a class list by student number. All-or-nothing, every problem listed."""
    section = get_or_404(db, ClassSection, section_id)
    if section.term.is_grades_locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Term is locked")
    numbers = list(dict.fromkeys(n.strip() for n in body.student_numbers if n.strip()))
    students = {s.student_number: s for s in db.scalars(
        select(Student).where(Student.student_number.in_(numbers)))}
    already = set(db.scalars(select(Enrollment.student_id)
                             .where(Enrollment.class_section_id == section.id)))
    errors = []
    for number in numbers:
        if number not in students:
            errors.append(f"{number}: no such student")
        elif students[number].id in already:
            errors.append(f"{number}: already enrolled in this section")
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Nobody was enrolled. " + "; ".join(errors[:50]))
    for number in numbers:
        enrollment = Enrollment(student_id=students[number].id, class_section_id=section.id)
        db.add(enrollment)
        db.flush()
        audit.record(db, "student_enrolled", user_id=actor.id, entity_type="enrollment",
                     entity_id=enrollment.id,
                     new_value={"student_number": number, "class_section_id": section.id},
                     ip_address=client_ip(request))
    db.commit()
    return {"enrolled": len(numbers)}
