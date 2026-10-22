"""Student master list (Section 5B) and academic records views."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_roles
from app.config import get_settings
from app.db import get_db
from app.models import Program, Student, User
from app.models.enums import Role, StudentStatus
from app.services import audit
from app.services.accounts import create_account
from app.services.records import academic_history
from app.services.tabular import RowErrors, TableFormatError, read_table

router = APIRouter(prefix="/students", tags=["students"])
LRN_PATTERN = r"^\d{12}$"  # DepEd Learner Reference Number
registrar = require_roles(Role.REGISTRAR)
student_only = require_roles(Role.STUDENT)


class StudentFields(BaseModel):
    student_number: str = Field(min_length=1, max_length=30)
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    suffix: str | None = Field(default=None, max_length=20)
    birthdate: date | None = None
    sex: str | None = Field(default=None, max_length=10)
    program_id: int | None = None
    year_level: int | None = Field(default=None, ge=1, le=6)
    status: StudentStatus = StudentStatus.ACTIVE
    admission_date: date | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=30)
    lrn: str | None = Field(default=None, pattern=LRN_PATTERN)
    birth_place: str | None = Field(default=None, max_length=200)
    mother_name: str | None = Field(default=None, max_length=200)
    father_name: str | None = Field(default=None, max_length=200)


class StudentCreateIn(StudentFields):
    email: EmailStr | None = None  # login email; needs temporary_password
    temporary_password: str | None = None


class StudentUpdateIn(BaseModel):
    """Every field optional; a reason is always required (Section 4)."""

    reason: str = Field(min_length=3)
    student_number: str | None = Field(default=None, min_length=1, max_length=30)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    middle_name: str | None = None
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    suffix: str | None = None
    birthdate: date | None = None
    sex: str | None = None
    program_id: int | None = None
    year_level: int | None = Field(default=None, ge=1, le=6)
    status: StudentStatus | None = None
    admission_date: date | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    lrn: str | None = Field(default=None, pattern=LRN_PATTERN)
    birth_place: str | None = Field(default=None, max_length=200)
    mother_name: str | None = Field(default=None, max_length=200)
    father_name: str | None = Field(default=None, max_length=200)


class StudentOut(StudentFields):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int | None
    login_active: bool | None = None


class AccountIn(BaseModel):
    email: EmailStr
    temporary_password: str


class TermRecord(BaseModel):
    term_id: int
    academic_year: str
    semester: str
    is_grades_locked: bool
    subjects: list[dict]
    gwa: Decimal | None


def _get_student(db: Session, student_id: int) -> Student:
    return get_or_404(db, Student, student_id)


def _own_student(user: User) -> Student:
    if user.student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No student profile linked to this account")
    return user.student


def _check_program(db: Session, program_id: int | None) -> None:
    if program_id is not None:
        get_or_404(db, Program, program_id)


def _check_lrn(db: Session, lrn: str | None, student_id: int | None = None) -> None:
    if lrn is None:
        return
    other = db.scalar(select(Student.id).where(Student.lrn == lrn))
    if other is not None and other != student_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Another student already has this LRN")


def _flush_unique(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Student number or LRN already exists") from None


def add_student(db: Session, fields: StudentFields, user_id: int | None = None) -> Student:
    """Insert a student after the master-list rules (program exists, unique number and LRN).
    Shared by the registrar form and profiles created from enrollment documents (S-07);
    the caller audits and commits."""
    _check_program(db, fields.program_id)
    _check_lrn(db, fields.lrn)
    student = Student(user_id=user_id, **fields.model_dump())
    db.add(student)
    _flush_unique(db)
    return student


# ---- Student self-service (row-level: only their own record) --------------------------------

@router.get("/me", response_model=StudentOut)
def my_profile(user: User = Depends(student_only)):
    return _own_student(user)


@router.get("/me/records", response_model=list[TermRecord])
def my_records(user: User = Depends(student_only), db: Session = Depends(get_db)):
    return academic_history(db, _own_student(user).id)


# ---- Registrar ------------------------------------------------------------------------------

@router.get("", response_model=list[StudentOut])
def list_students(
    q: str | None = None,
    program_id: int | None = None,
    status_: StudentStatus | None = Query(None, alias="status"),
    _: User = Depends(registrar),
    db: Session = Depends(get_db),
):
    query = select(Student).order_by(Student.last_name, Student.first_name)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(
            func.lower(Student.student_number).like(like),
            func.lower(Student.first_name).like(like),
            func.lower(Student.last_name).like(like),
        ))
    if program_id is not None:
        query = query.where(Student.program_id == program_id)
    if status_ is not None:
        query = query.where(Student.status == status_)
    return db.scalars(query.limit(500)).all()


@router.post("", response_model=StudentOut, status_code=status.HTTP_201_CREATED)
def create_student(body: StudentCreateIn, request: Request, actor: User = Depends(registrar),
                   db: Session = Depends(get_db)):
    _check_program(db, body.program_id)
    _check_lrn(db, body.lrn)
    user_id = None
    if body.email:
        if not body.temporary_password:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "temporary_password is required to create a login")
        user_id = create_account(db, actor=actor, email=body.email, role=Role.STUDENT,
                                 temporary_password=body.temporary_password,
                                 ip_address=client_ip(request)).id

    student = add_student(db, StudentFields.model_validate(
        body.model_dump(exclude={"email", "temporary_password"})), user_id)
    audit.record(db, "student_created", user_id=actor.id, entity_type="student",
                 entity_id=student.id,
                 new_value=StudentFields.model_validate(student, from_attributes=True)
                 .model_dump(mode="json"),
                 ip_address=client_ip(request))
    db.commit()
    return student


@router.get("/{student_id}", response_model=StudentOut)
def get_student(student_id: int, _: User = Depends(registrar), db: Session = Depends(get_db)):
    return _get_student(db, student_id)


@router.get("/{student_id}/records", response_model=list[TermRecord])
def student_records(student_id: int, _: User = Depends(registrar),
                    db: Session = Depends(get_db)):
    return academic_history(db, _get_student(db, student_id).id)


@router.patch("/{student_id}", response_model=StudentOut)
def update_student(student_id: int, body: StudentUpdateIn, request: Request,
                   actor: User = Depends(registrar), db: Session = Depends(get_db)):
    student = _get_student(db, student_id)
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "program_id" in changes:
        _check_program(db, changes["program_id"])
    if changes.get("lrn"):
        _check_lrn(db, changes["lrn"], student.id)

    old, new = {}, {}
    for field, value in changes.items():
        if getattr(student, field) != value:
            old[field] = getattr(student, field)
            new[field] = value
            setattr(student, field, value)
    if not new:
        return student

    _flush_unique(db)
    audit.record(db, "student_updated", user_id=actor.id, entity_type="student",
                 entity_id=student.id,
                 old_value=_jsonable(old), new_value={**_jsonable(new), "reason": body.reason},
                 ip_address=client_ip(request))
    db.commit()
    return student


@router.post("/{student_id}/account", response_model=StudentOut)
def create_student_account(student_id: int, body: AccountIn, request: Request,
                           actor: User = Depends(registrar), db: Session = Depends(get_db)):
    student = _get_student(db, student_id)
    if student.user_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Student already has a login")
    student.user_id = create_account(db, actor=actor, email=body.email, role=Role.STUDENT,
                                     temporary_password=body.temporary_password,
                                     ip_address=client_ip(request)).id
    db.commit()
    return student


# ---- Bulk import ----------------------------------------------------------------------------

IMPORT_REQUIRED = {"student_number", "first_name", "last_name", "program_code"}
OPTIONAL_TEXT = ("middle_name", "suffix", "sex", "contact_email", "contact_phone")


@router.post("/import")
async def import_students(
    file: UploadFile,
    request: Request,
    dry_run: bool = False,
    actor: User = Depends(registrar),
    db: Session = Depends(get_db),
):
    """All-or-nothing: if any row has an error, nothing is imported and every error is listed."""
    content = await file.read(get_settings().max_import_bytes + 1)
    if len(content) > get_settings().max_import_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File too large")
    try:
        rows = read_table(file.filename or "", content, IMPORT_REQUIRED)
    except TableFormatError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None

    programs = {p.code.upper(): p.id for p in db.scalars(select(Program))}
    existing = set(db.scalars(select(Student.student_number)))
    seen: set[str] = set()
    errors = RowErrors()
    students: list[Student] = []

    for index, row in enumerate(rows, start=2):
        if not row:
            continue
        number = row.get("student_number", "")
        for col in IMPORT_REQUIRED:
            if not row.get(col):
                errors.add(index, f"{col} is required")
        if number in existing:
            errors.add(index, f"student_number {number} already exists")
        elif number and number in seen:
            errors.add(index, f"student_number {number} appears twice in the file")
        seen.add(number)

        program_id = programs.get(row.get("program_code", "").upper())
        if row.get("program_code") and program_id is None:
            errors.add(index, f"unknown program_code {row['program_code']}")

        parsed = {}
        for col in ("birthdate", "admission_date"):
            if row.get(col):
                try:
                    parsed[col] = date.fromisoformat(row[col])
                except ValueError:
                    errors.add(index, f"{col} must be YYYY-MM-DD")
        if row.get("year_level"):
            if row["year_level"].isdigit() and 1 <= int(row["year_level"]) <= 6:
                parsed["year_level"] = int(row["year_level"])
            else:
                errors.add(index, "year_level must be 1-6")
        if row.get("status"):
            try:
                parsed["status"] = StudentStatus(row["status"].lower())
            except ValueError:
                errors.add(index, "status must be active, graduated, or inactive")

        students.append(Student(
            student_number=number, first_name=row.get("first_name", ""),
            last_name=row.get("last_name", ""), program_id=program_id,
            **{col: row.get(col) or None for col in OPTIONAL_TEXT}, **parsed,
        ))

    if errors:
        return JSONResponse(status_code=422, content={
            "detail": "No students were imported. Fix the rows below and upload again.",
            "errors": errors.rows,
        })
    if dry_run:
        return {"imported": 0, "valid_rows": len(students), "dry_run": True}

    db.add_all(students)
    audit.record(db, "students_imported", user_id=actor.id, entity_type="student",
                 new_value={"file": file.filename, "rows": len(students)},
                 ip_address=client_ip(request))
    db.commit()
    return {"imported": len(students), "dry_run": False}


def _jsonable(values: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in values.items()}
