"""Grade encoding, term locking, and post-lock corrections (Sections 4, 5C)."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import (
    client_ip,
    ensure_section_access,
    ensure_section_view_access,
    get_or_404,
    require_roles,
)
from app.config import get_settings
from app.db import get_db
from app.models import ClassSection, Enrollment, Grade, GradeChangeRequest, Student, Term, User
from app.models.enums import ChangeRequestStatus, EnrollmentStatus, Role
from app.schemas import ReasonIn
from app.services import audit
from app.services.grading import GradeError, ParsedGrade, parse_grade
from app.services.tabular import RowErrors, TableFormatError, read_table

router = APIRouter(tags=["grades"])
registrar = require_roles(Role.REGISTRAR)
grader = require_roles(Role.REGISTRAR, Role.TEACHER)


class GradeRow(BaseModel):
    enrollment_id: int
    student_id: int
    student_number: str
    student_name: str
    grade_id: int | None
    grade: str | None
    remarks: str | None
    is_locked: bool


class GradeEntry(BaseModel):
    enrollment_id: int
    grade: str


class GradesIn(BaseModel):
    entries: list[GradeEntry] = Field(min_length=1)
    reason: str | None = None  # required when a registrar edits (Section 4)


class ChangeRequestIn(BaseModel):
    grade_id: int
    new_grade: str
    reason: str = Field(min_length=3)


class ChangeRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    grade_id: int
    old_value: str | None
    new_value: str
    reason: str
    requested_by: int
    approved_by: int | None
    status: ChangeRequestStatus
    created_at: datetime
    # What the registrar needs to judge the request without opening other screens.
    student_number: str = ""
    student_name: str = ""
    subject_code: str = ""
    section_code: str = ""
    term: str = ""
    requested_by_email: str | None = None


def _change_out(db: Session, change: GradeChangeRequest) -> ChangeRequestOut:
    enrollment = db.get(Grade, change.grade_id).enrollment
    student = db.get(Student, enrollment.student_id)
    section = enrollment.class_section
    requester = db.get(User, change.requested_by)
    return ChangeRequestOut.model_validate(change).model_copy(update={
        "student_number": student.student_number,
        "student_name": f"{student.last_name}, {student.first_name}",
        "subject_code": section.subject.code, "section_code": section.section_code,
        "term": f"{section.term.semester} Semester, AY {section.term.academic_year}",
        "requested_by_email": requester.email if requester else None,
    })


class LockIn(BaseModel):
    force: bool = False  # lock even if some enrolled students have no grade


# ---- helpers --------------------------------------------------------------------------------

def _section_for_grading(db: Session, user: User, section_id: int) -> ClassSection:
    section = get_or_404(db, ClassSection, section_id)
    ensure_section_access(user, section)
    return section


def _ensure_editable(section: ClassSection) -> None:
    if section.term.is_grades_locked:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Term is locked. Submit a grade change request instead.")


def _require_registrar_reason(user: User, reason: str | None) -> None:
    if user.role == Role.REGISTRAR and not (reason and len(reason.strip()) >= 3):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "A reason is required when the registrar edits grades")


def _roster(db: Session, section_id: int) -> list[tuple[Enrollment, Student]]:
    return list(db.execute(
        select(Enrollment, Student)
        .join(Student, Student.id == Enrollment.student_id)
        .where(Enrollment.class_section_id == section_id,
               Enrollment.status == EnrollmentStatus.ENROLLED)
        .order_by(Student.last_name, Student.first_name)
    ).tuples())


def _display(grade: Grade | None) -> str | None:
    if grade is None:
        return None
    if grade.grade_value is None:
        return grade.remarks.value
    return str(Decimal(grade.grade_value).quantize(Decimal("0.01")))


def _apply_grades(db: Session, actor: User, changes: list[tuple[Enrollment, ParsedGrade]],
                  reason: str | None, ip: str | None) -> int:
    changed = 0
    for enrollment, parsed in changes:
        grade = enrollment.grade
        old = _display(grade)
        if old == parsed.display:
            continue
        if grade is None:
            grade = Grade(enrollment_id=enrollment.id, remarks=parsed.remarks)
            db.add(grade)
        grade.grade_value = parsed.value
        grade.remarks = parsed.remarks
        grade.encoded_by = actor.id
        db.flush()
        audit.record(db, "grade_encoded" if old is None else "grade_edited", user_id=actor.id,
                     entity_type="grade", entity_id=grade.id,
                     old_value=None if old is None else {"grade": old},
                     new_value={"grade": parsed.display, "enrollment_id": enrollment.id,
                                **({"reason": reason} if reason else {})},
                     ip_address=ip)
        changed += 1
    return changed


def _errors_response(errors: list[dict]) -> JSONResponse:
    return JSONResponse(status_code=422, content={
        "detail": "No grades were saved. Fix the entries below and try again.",
        "errors": errors,
    })


# ---- encoding -------------------------------------------------------------------------------

@router.get("/sections/{section_id}/grades", response_model=list[GradeRow])
def section_grades(section_id: int, user: User = Depends(grader), db: Session = Depends(get_db)):
    """Teachers of the section, its class adviser (read-only, S-08) and the registrar."""
    ensure_section_view_access(db, user, get_or_404(db, ClassSection, section_id))
    rows = []
    for enrollment, student in _roster(db, section_id):
        grade = enrollment.grade
        rows.append(GradeRow(
            enrollment_id=enrollment.id, student_id=student.id,
            student_number=student.student_number,
            student_name=f"{student.last_name}, {student.first_name}",
            grade_id=grade.id if grade else None, grade=_display(grade),
            remarks=grade.remarks.value if grade else None,
            is_locked=bool(grade and grade.is_locked),
        ))
    return rows


@router.put("/sections/{section_id}/grades")
def encode_grades(section_id: int, body: GradesIn, request: Request,
                  user: User = Depends(grader), db: Session = Depends(get_db)):
    section = _section_for_grading(db, user, section_id)
    _ensure_editable(section)
    _require_registrar_reason(user, body.reason)

    roster = {e.id: e for e, _ in _roster(db, section_id)}
    errors, changes = [], []
    for entry in body.entries:
        enrollment = roster.get(entry.enrollment_id)
        if enrollment is None:
            errors.append({"enrollment_id": entry.enrollment_id,
                           "error": "Not an active enrollment in this section"})
            continue
        try:
            changes.append((enrollment, parse_grade(entry.grade)))
        except GradeError as e:
            errors.append({"enrollment_id": entry.enrollment_id, "error": str(e)})
    if errors:
        return _errors_response(errors)

    changed = _apply_grades(db, user, changes, body.reason, client_ip(request))
    db.commit()
    return {"saved": changed}


@router.post("/sections/{section_id}/grades/import")
async def import_grades(section_id: int, file: UploadFile, request: Request,
                        reason: str | None = None, user: User = Depends(grader),
                        db: Session = Depends(get_db)):
    """CSV/XLSX with columns student_number, grade. All-or-nothing."""
    section = _section_for_grading(db, user, section_id)
    _ensure_editable(section)
    _require_registrar_reason(user, reason)

    content = await file.read(get_settings().max_import_bytes + 1)
    if len(content) > get_settings().max_import_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File too large")
    try:
        rows = read_table(file.filename or "", content, {"student_number", "grade"})
    except TableFormatError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None

    by_number = {s.student_number: e for e, s in _roster(db, section_id)}
    errors, changes, seen = RowErrors(), [], set()
    for index, row in enumerate(rows, start=2):
        if not row:
            continue
        number = row.get("student_number", "")
        enrollment = by_number.get(number)
        if enrollment is None:
            errors.add(index, f"{number or '(blank)'} is not enrolled in this section")
        elif number in seen:
            errors.add(index, f"{number} appears twice in the file")
        seen.add(number)
        try:
            parsed = parse_grade(row.get("grade", ""))
        except GradeError as e:
            errors.add(index, str(e))
            continue
        if enrollment is not None:
            changes.append((enrollment, parsed))
    if errors:
        return _errors_response(errors.rows)

    changed = _apply_grades(db, user, changes, reason, client_ip(request))
    audit.record(db, "grades_imported", user_id=user.id, entity_type="class_section",
                 entity_id=section.id,
                 new_value={"file": file.filename, "rows": len(changes), "changed": changed},
                 ip_address=client_ip(request))
    db.commit()
    return {"saved": changed}


@router.post("/sections/{section_id}/submit")
def submit_section(section_id: int, request: Request, user: User = Depends(grader),
                   db: Session = Depends(get_db)):
    """Teacher marks grades final for the section ("submit for lock")."""
    section = _section_for_grading(db, user, section_id)
    _ensure_editable(section)
    missing = [s.student_number for e, s in _roster(db, section_id) if e.grade is None]
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Grades missing for: {', '.join(missing)}")
    section.grades_submitted_at = datetime.now(UTC)
    audit.record(db, "grades_submitted", user_id=user.id, entity_type="class_section",
                 entity_id=section.id, ip_address=client_ip(request))
    db.commit()
    return {"submitted": True}


# ---- term locking ---------------------------------------------------------------------------

@router.post("/terms/{term_id}/lock")
def lock_term(term_id: int, body: LockIn, request: Request, actor: User = Depends(registrar),
              db: Session = Depends(get_db)):
    term = get_or_404(db, Term, term_id)
    if term.is_grades_locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Term is already locked")

    section_ids = list(db.scalars(select(ClassSection.id).where(ClassSection.term_id == term.id)))
    missing = db.scalar(
        select(Enrollment.id).outerjoin(Grade, Grade.enrollment_id == Enrollment.id)
        .where(Enrollment.class_section_id.in_(section_ids),
               Enrollment.status == EnrollmentStatus.ENROLLED, Grade.id.is_(None)).limit(1)
    )
    if missing is not None and not body.force:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "missing_grades",
            "message": "Some enrolled students have no grade yet. If you lock anyway, the "
                       "term is final with those grades missing and those students cannot "
                       "get a TOR."})

    term.is_grades_locked = True
    enrollment_ids = select(Enrollment.id).where(Enrollment.class_section_id.in_(section_ids))
    db.execute(update(Grade).where(Grade.enrollment_id.in_(enrollment_ids))
               .values(is_locked=True))
    audit.record(db, "term_locked", user_id=actor.id, entity_type="term", entity_id=term.id,
                 new_value={"forced": body.force and missing is not None},
                 ip_address=client_ip(request))
    db.commit()
    return {"locked": True}


# ---- corrections after lock -----------------------------------------------------------------

@router.post("/grade-change-requests", response_model=ChangeRequestOut,
             status_code=status.HTTP_201_CREATED)
def request_grade_change(body: ChangeRequestIn, request: Request, user: User = Depends(grader),
                         db: Session = Depends(get_db)):
    grade = get_or_404(db, Grade, body.grade_id)
    ensure_section_access(user, grade.enrollment.class_section)
    if not grade.is_locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Grade is not locked; edit it directly")
    try:
        parsed = parse_grade(body.new_grade)
    except GradeError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    if parsed.display == _display(grade):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New grade equals the current grade")
    pending = db.scalar(select(GradeChangeRequest.id).where(
        GradeChangeRequest.grade_id == grade.id,
        GradeChangeRequest.status == ChangeRequestStatus.PENDING))
    if pending is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A change request is already pending for this grade")

    change = GradeChangeRequest(grade_id=grade.id, old_value=_display(grade),
                                new_value=parsed.display, reason=body.reason,
                                requested_by=user.id)
    db.add(change)
    db.flush()
    audit.record(db, "grade_change_requested", user_id=user.id, entity_type="grade",
                 entity_id=grade.id, old_value={"grade": change.old_value},
                 new_value={"grade": change.new_value, "reason": body.reason,
                            "request_id": change.id},
                 ip_address=client_ip(request))
    db.commit()
    return _change_out(db, change)


@router.get("/grade-change-requests", response_model=list[ChangeRequestOut])
def list_change_requests(status_filter: ChangeRequestStatus | None = Query(None, alias="status"),
                         user: User = Depends(grader), db: Session = Depends(get_db)):
    query = select(GradeChangeRequest).order_by(GradeChangeRequest.created_at)
    if status_filter is not None:
        query = query.where(GradeChangeRequest.status == status_filter)
    if user.role == Role.TEACHER:
        query = query.where(GradeChangeRequest.requested_by == user.id)
    return [_change_out(db, change) for change in db.scalars(query)]


def _pending_request(db: Session, request_id: int) -> GradeChangeRequest:
    change = get_or_404(db, GradeChangeRequest, request_id)
    if change.status != ChangeRequestStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request is already {change.status}")
    return change


@router.post("/grade-change-requests/{request_id}/approve", response_model=ChangeRequestOut)
def approve_grade_change(request_id: int, request: Request, actor: User = Depends(registrar),
                         db: Session = Depends(get_db)):
    change = _pending_request(db, request_id)
    grade = get_or_404(db, Grade, change.grade_id)
    if _display(grade) != change.old_value:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Grade changed since the request was made; reject and resubmit")
    parsed = parse_grade(change.new_value)
    grade.grade_value = parsed.value
    grade.remarks = parsed.remarks
    change.status = ChangeRequestStatus.APPROVED
    change.approved_by = actor.id
    audit.record(db, "grade_change_approved", user_id=actor.id, entity_type="grade",
                 entity_id=grade.id, old_value={"grade": change.old_value},
                 new_value={"grade": change.new_value, "request_id": change.id,
                            "reason": change.reason},
                 ip_address=client_ip(request))
    db.commit()
    return _change_out(db, change)


@router.post("/grade-change-requests/{request_id}/reject", response_model=ChangeRequestOut)
def reject_grade_change(request_id: int, body: ReasonIn, request: Request,
                        actor: User = Depends(registrar), db: Session = Depends(get_db)):
    change = _pending_request(db, request_id)
    change.status = ChangeRequestStatus.REJECTED
    change.approved_by = actor.id
    audit.record(db, "grade_change_rejected", user_id=actor.id, entity_type="grade",
                 entity_id=change.grade_id,
                 new_value={"request_id": change.id, "reason": body.reason},
                 ip_address=client_ip(request))
    db.commit()
    return _change_out(db, change)
