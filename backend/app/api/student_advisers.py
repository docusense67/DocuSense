"""Per-student advisers (docs/applicant-intake.md, "Advisers").

The registrar (or an administrator) assigns one teacher-adviser per student per academic term,
with a reason that is audited. Students see their own adviser ("My adviser"); teachers see only
the students assigned to them ("My advisees"). This is distinct from the class advisers of
block sections (S-08, /section-advisers and /advisory), which stay as they are.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_roles, teacher_profile_id
from app.db import get_db
from app.models import Program, Student, StudentAdviser, Teacher, Term, User
from app.models.enums import Role
from app.services import audit
from app.timeutil import utcnow

router = APIRouter(tags=["advisers"])
staff = require_roles(Role.REGISTRAR, Role.ADMIN)
ENTITY = "student_adviser"


def _term_name(term: Term) -> str:
    return f"{term.academic_year} {term.semester.value}"


def _name(student: Student) -> str:
    return f"{student.last_name}, {student.first_name}"


def _row(db: Session, row: StudentAdviser) -> dict:
    student = db.get(Student, row.student_id)
    teacher = db.get(Teacher, row.teacher_id)
    return {"id": row.id, "student_id": row.student_id,
            "student_number": student.student_number, "student_name": _name(student),
            "term_id": row.term_id, "term": _term_name(row.term),
            "teacher_id": row.teacher_id, "teacher_name": teacher.full_name,
            "assigned_at": row.assigned_at}


@router.get("/student-advisers")
def list_student_advisers(term_id: int | None = None, _: User = Depends(staff),
                          db: Session = Depends(get_db)):
    query = select(StudentAdviser)
    if term_id is not None:
        query = query.where(StudentAdviser.term_id == term_id)
    rows = db.scalars(query.order_by(StudentAdviser.term_id.desc(), StudentAdviser.id))
    return [_row(db, r) for r in rows]


@router.get("/student-advisers/options")
def adviser_options(_: User = Depends(staff), db: Session = Depends(get_db)):
    """Students, teachers and terms to choose from (administrators have no records pages)."""
    students = db.scalars(select(Student).order_by(Student.last_name, Student.first_name)
                          .limit(5000))
    return {
        "students": [{"id": s.id, "student_number": s.student_number, "name": _name(s)}
                     for s in students],
        "teachers": [{"id": t.id, "name": t.full_name}
                     for t in db.scalars(select(Teacher).order_by(Teacher.full_name))],
        "terms": [{"id": t.id, "name": _term_name(t), "is_current": t.is_current}
                  for t in db.scalars(select(Term).order_by(Term.academic_year.desc(),
                                                            Term.id.desc()))],
    }


class StudentAdviserIn(BaseModel):
    student_id: int
    term_id: int
    teacher_id: int | None  # null removes the adviser
    reason: str = Field(min_length=3, max_length=500)


@router.put("/student-advisers")
def set_student_adviser(body: StudentAdviserIn, request: Request, actor: User = Depends(staff),
                        db: Session = Depends(get_db)):
    """Assign, change or remove a student's adviser for a term. Always with a reason."""
    get_or_404(db, Student, body.student_id)
    get_or_404(db, Term, body.term_id)
    if body.teacher_id is not None:
        get_or_404(db, Teacher, body.teacher_id)
    row = db.scalar(select(StudentAdviser).where(StudentAdviser.student_id == body.student_id,
                                                 StudentAdviser.term_id == body.term_id))
    old = row.teacher_id if row else None
    if old == body.teacher_id:
        return _row(db, row) if row else None
    if body.teacher_id is None:
        db.delete(row)
    elif row is None:
        row = StudentAdviser(student_id=body.student_id, term_id=body.term_id,
                             teacher_id=body.teacher_id, assigned_by=actor.id,
                             assigned_at=utcnow())
        db.add(row)
    else:
        row.teacher_id = body.teacher_id
        row.assigned_by = actor.id
        row.assigned_at = utcnow()
    db.flush()
    audit.record(db, "student_adviser_removed" if body.teacher_id is None
                 else "student_adviser_assigned", user_id=actor.id, entity_type=ENTITY,
                 entity_id=f"{body.student_id}:{body.term_id}",
                 old_value={"teacher_id": old},
                 new_value={"student_id": body.student_id, "term_id": body.term_id,
                            "teacher_id": body.teacher_id, "reason": body.reason.strip()},
                 ip_address=client_ip(request))
    db.commit()
    return _row(db, row) if body.teacher_id is not None else None


@router.get("/students/me/advisers")
def my_advisers(user: User = Depends(require_roles(Role.STUDENT)),
                db: Session = Depends(get_db)):
    """The student's own adviser per term (name only)."""
    if user.student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No student profile linked to this account")
    rows = db.scalars(select(StudentAdviser).where(StudentAdviser.student_id == user.student.id)
                      .order_by(StudentAdviser.term_id.desc()))
    out = []
    for row in rows:
        teacher = db.get(Teacher, row.teacher_id)
        out.append({"term_id": row.term_id, "term": _term_name(row.term),
                    "is_current": row.term.is_current, "teacher_name": teacher.full_name,
                    "department": teacher.department})
    return out


@router.get("/advisees")
def my_advisees(user: User = Depends(require_roles(Role.TEACHER)),
                db: Session = Depends(get_db)):
    """The students assigned to this teacher, and nobody else: names, numbers, program and
    year level (grades stay with each section's teacher)."""
    me = teacher_profile_id(user)
    rows = db.scalars(select(StudentAdviser).where(StudentAdviser.teacher_id == me)
                      .order_by(StudentAdviser.term_id.desc(), StudentAdviser.id))
    out = []
    for row in rows:
        student = db.get(Student, row.student_id)
        program = db.get(Program, student.program_id) if student.program_id else None
        out.append({"student_id": student.id, "student_number": student.student_number,
                    "student_name": _name(student), "program_code": program.code if program
                    else None, "year_level": student.year_level, "term_id": row.term_id,
                    "term": _term_name(row.term), "is_current": row.term.is_current})
    return out
