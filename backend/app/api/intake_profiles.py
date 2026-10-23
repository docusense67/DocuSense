"""Incoming documents -> student data (S-06/S-07): reviewed fields, matching, link or create.

Staff (registrar, admin) review the fields the station read from an enrollment document,
correct them and save a reviewed set (the station's reading stays untouched in
`screening_result`). From the reviewed set the backend suggests existing students; staff
confirm one (never automatic) or create a new student profile, and the document is linked to
that student.

Status flow: screened -> accepted -> data_reviewed -> linked. A document that passed screening
may skip the explicit accept: saving its fields accepts it (audited as an acceptance). A
document that needs manual review must be accepted first; rejected and resubmission-requested
documents can't be used at all (409).

Audit entries record which fields changed, never their values.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_roles
from app.api.students import StudentFields, add_student
from app.db import get_db
from app.models import ApplicantSubmission, IntakeDocument, Student, User
from app.models.enums import IntakeDecision, IntakeDocType, IntakeStatus, Role
from app.services import audit
from app.services import intake_fields as fields
from app.timeutil import utcnow

router = APIRouter(tags=["intake"])
staff = require_roles(Role.REGISTRAR, Role.ADMIN)
ENTITY = "intake_document"
MAX_CANDIDATES = 20

BLOCKED = {
    IntakeStatus.REJECTED: "This document was rejected; its data can't be used for a student "
                           "profile.",
    IntakeStatus.RESUBMISSION_REQUESTED: "A resubmission was requested for this document; its "
                                         "data can't be used for a student profile.",
}
NEEDS_ACCEPT = "This document needs manual review: accept it before reviewing its data."


def blocked_reason(doc: IntakeDocument) -> str | None:
    """Why this document's data can't be used, or None. Only documents that passed screening,
    or were accepted by staff, get as far as the student data step."""
    if doc.status in BLOCKED:
        return BLOCKED[doc.status]
    if doc.status == IntakeStatus.SCREENED and doc.decision == IntakeDecision.MANUAL_REVIEW:
        return NEEDS_ACCEPT
    return None


def _conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


def _require_usable(doc: IntakeDocument) -> None:
    reason = blocked_reason(doc)
    if reason:
        raise _conflict(reason)


def _require_reviewed(doc: IntakeDocument) -> None:
    _require_usable(doc)
    if doc.status not in {IntakeStatus.DATA_REVIEWED, IntakeStatus.LINKED} \
            or not doc.reviewed_fields:
        raise _conflict("Review and save the document's fields first.")


def _station_fields(doc: IntakeDocument) -> dict:
    found = (doc.screening_result or {}).get("fields")
    return found if isinstance(found, dict) else {}


def _reviewed_values(doc: IntakeDocument) -> dict:
    return dict((doc.reviewed_fields or {}).get("values") or {})


def _student_summary(student: Student | None) -> dict | None:
    if student is None:
        return None
    first = " ".join(p for p in (student.first_name, student.middle_name) if p)
    return {"id": student.id, "student_number": student.student_number,
            "name": f"{student.last_name}, {first}", "lrn": student.lrn,
            "birthdate": student.birthdate}


def _fields_out(db: Session, doc: IntakeDocument, preview: IntakeDocType | None = None) -> dict:
    schema_type = preview or doc.doc_type
    known = schema_type != IntakeDocType.UNKNOWN
    reviewer = db.get(User, doc.fields_reviewed_by) if doc.fields_reviewed_by else None
    linker = db.get(User, doc.linked_by) if doc.linked_by else None
    blocked = blocked_reason(doc)
    reviewed = doc.reviewed_fields or None
    return {
        "intake_id": doc.id, "status": doc.status, "decision": doc.decision,
        "doc_type": doc.doc_type, "schema_doc_type": schema_type,
        "doc_types": [t for t in IntakeDocType if t != IntakeDocType.UNKNOWN],
        "schema": fields.schema_out(schema_type) if known else [],
        "extracted": fields.prefill(schema_type, _station_fields(doc)) if known else {},
        "reviewed": {"values": reviewed.get("values", {}), "changed": reviewed.get("changed", []),
                     "reviewed_by": reviewer.email if reviewer else None,
                     "reviewed_at": doc.fields_reviewed_at} if reviewed else None,
        "editable": blocked is None and doc.status != IntakeStatus.LINKED,
        "blocked": blocked,
        "student": _student_summary(db.get(Student, doc.student_id) if doc.student_id else None),
        "linked_by": linker.email if linker else None, "linked_at": doc.linked_at,
    }


@router.get("/intake/documents/{intake_id}/fields")
def get_intake_fields(intake_id: int, doc_type: IntakeDocType | None = None,
                      _: User = Depends(staff), db: Session = Depends(get_db)):
    """The field schema of the document type, what the station read (normalized, with
    confidence) and the reviewed set if one was saved. `doc_type` previews another type's
    schema (for documents the station could not classify)."""
    return _fields_out(db, get_or_404(db, IntakeDocument, intake_id), doc_type)


FieldValue = Annotated[str, StringConstraints(max_length=500)] | None


class FieldsIn(BaseModel):
    doc_type: IntakeDocType | None = None  # staff correction of the document type
    values: dict[str, FieldValue] = Field(default_factory=dict, max_length=40)


@router.put("/intake/documents/{intake_id}/fields")
def save_intake_fields(intake_id: int, body: FieldsIn, request: Request,
                       actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Save the reviewed field set (validated). The station's own reading is kept as it was."""
    doc = get_or_404(db, IntakeDocument, intake_id)
    _require_usable(doc)
    if doc.status == IntakeStatus.LINKED:
        raise _conflict("This document is linked to a student: unlink it before changing "
                        "its fields.")
    doc_type = body.doc_type or doc.doc_type
    if doc_type == IntakeDocType.UNKNOWN:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Choose the document type before saving its fields")
    cleaned, errors = fields.clean_values(doc_type, body.values)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            {"message": "Some fields need attention", "errors": errors})

    station = {k: v["value"] for k, v in fields.prefill(doc_type, _station_fields(doc)).items()}
    corrected = fields.changed_fields(station, cleaned)
    previous = _reviewed_values(doc) if doc.reviewed_fields and doc_type == doc.doc_type \
        else None
    edited = fields.changed_fields(previous, cleaned) if previous is not None else None
    ip = client_ip(request)
    now = utcnow()

    if doc.status == IntakeStatus.SCREENED:  # passed screening: reviewing its data accepts it
        doc.status = IntakeStatus.ACCEPTED
        doc.reviewed_by = actor.id
        doc.reviewed_at = now
        audit.record(db, "intake_document_reviewed", user_id=actor.id, entity_type=ENTITY,
                     entity_id=doc.id, old_value={"status": IntakeStatus.SCREENED.value},
                     new_value={"status": IntakeStatus.ACCEPTED.value, "action": "accept",
                                "decision": doc.decision.value, "reason": None,
                                "implicit": "accepted by reviewing its fields"},
                     ip_address=ip)

    old = {"status": doc.status.value, "doc_type": doc.doc_type.value}
    doc.doc_type = doc_type
    doc.reviewed_fields = {"values": cleaned, "changed": corrected}
    doc.fields_reviewed_by = actor.id
    doc.fields_reviewed_at = now
    doc.status = IntakeStatus.DATA_REVIEWED
    # Field names only: the audit log never carries extracted or reviewed values.
    audit.record(db, "intake_fields_reviewed", user_id=actor.id, entity_type=ENTITY,
                 entity_id=doc.id, old_value=old,
                 new_value={"status": doc.status.value, "doc_type": doc_type.value,
                            "fields_corrected": corrected, "fields_edited": edited,
                            "fields_empty": sorted(k for k, v in cleaned.items() if v is None)},
                 ip_address=ip)
    db.commit()
    return _fields_out(db, doc)


def _candidates(db: Session, doc: IntakeDocument) -> list[dict]:
    values = _reviewed_values(doc)
    lrn, last, first = values.get("lrn"), values.get("last_name"), values.get("first_name")
    birth = values.get("birth_date")
    conditions = []
    if lrn:
        conditions.append(Student.lrn == lrn)
    if last and first:
        lowered = {last.lower(), last.lower().replace(" ", "")}
        conditions.append(or_(func.lower(Student.last_name).in_(lowered),
                              func.replace(func.lower(Student.last_name), " ", "").in_(lowered)))
    if birth and first:
        conditions.append(and_(Student.birthdate == date.fromisoformat(birth),
                               func.lower(Student.first_name) == first.lower()))
    if not conditions:
        return []
    found = db.scalars(select(Student).where(or_(*conditions)).order_by(Student.id).limit(200))
    return fields.match_candidates(values, found)[:MAX_CANDIDATES]


@router.get("/intake/documents/{intake_id}/candidates")
def intake_candidates(intake_id: int, _: User = Depends(staff), db: Session = Depends(get_db)):
    """Existing students that may be the person on this document (LRN first, then name +
    birth date). Suggestions only: staff confirm a link."""
    doc = get_or_404(db, IntakeDocument, intake_id)
    _require_reviewed(doc)
    return {"intake_id": doc.id, "candidates": _candidates(db, doc)}


def _link(db: Session, doc: IntakeDocument, student: Student, actor: User, ip: str | None,
          *, how: str, match: dict | None, reason: str | None) -> None:
    doc.student_id = student.id
    doc.linked_by = actor.id
    doc.linked_at = utcnow()
    doc.status = IntakeStatus.LINKED
    audit.record(db, "intake_document_linked", user_id=actor.id, entity_type=ENTITY,
                 entity_id=doc.id, old_value={"status": IntakeStatus.DATA_REVIEWED.value},
                 new_value={"status": doc.status.value, "student_id": student.id,
                            "student_number": student.student_number, "how": how,
                            "match": match["match"] if match else None,
                            "conflicts": match["conflicts"] if match else None,
                            "reason": reason},
                 ip_address=ip)
    _hand_over_applicant_login(db, doc, student, actor, ip)


def _hand_over_applicant_login(db: Session, doc: IntakeDocument, student: Student, actor: User,
                               ip: str | None) -> None:
    """An online upload (docs/applicant-intake.md): the applicant's login becomes this
    student's login, unless the student already has one (then the applicant login stays as
    it is and staff decide what to do with it)."""
    if doc.submission_id is None or student.user_id is not None:
        return
    submission = db.get(ApplicantSubmission, doc.submission_id)
    user = db.get(User, submission.applicant_id) if submission else None
    if user is None or user.role != Role.APPLICANT or not user.is_active:
        return
    student.user_id = user.id
    user.role = Role.STUDENT
    audit.record(db, "applicant_login_became_student", user_id=actor.id, entity_type="user",
                 entity_id=user.id,
                 old_value={"role": Role.APPLICANT.value},
                 new_value={"role": Role.STUDENT.value, "student_id": student.id,
                            "student_number": student.student_number, "intake_id": doc.id},
                 ip_address=ip)


def _require_linkable(doc: IntakeDocument) -> None:
    _require_reviewed(doc)
    if doc.status == IntakeStatus.LINKED:
        raise _conflict("This document is already linked to a student.")


class LinkIn(BaseModel):
    student_id: int
    reason: str | None = Field(default=None, max_length=500)


@router.post("/intake/documents/{intake_id}/link")
def link_intake_document(intake_id: int, body: LinkIn, request: Request,
                         actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Staff confirm which existing student this document belongs to. Linking to a student who
    is not a strong match (or not suggested at all) needs a reason."""
    doc = get_or_404(db, IntakeDocument, intake_id)
    _require_linkable(doc)
    student = db.get(Student, body.student_id)
    if student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    match = next((c for c in _candidates(db, doc) if c["student_id"] == student.id), None)
    reason = (body.reason or "").strip() or None
    if (match is None or match["strength"] != "strong") and (reason is None or len(reason) < 3):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "This student is not a strong match for the reviewed fields: give a "
                            "reason (at least 3 characters) to link anyway")
    _link(db, doc, student, actor, client_ip(request), how="existing", match=match,
          reason=reason)
    db.commit()
    return _fields_out(db, doc)


class CreateStudentIn(BaseModel):
    """What the document doesn't say. Names, sex, birth date and place, parents and LRN come
    from the reviewed fields, never from this body."""

    student_number: str = Field(min_length=1, max_length=30)
    program_id: int | None = None
    year_level: int | None = Field(default=None, ge=1, le=6)
    admission_date: date | None = None
    confirm_new: bool = False  # staff saw the suggested matches and still want a new profile


@router.post("/intake/documents/{intake_id}/create-student", status_code=status.HTTP_201_CREATED)
def create_student_from_intake(intake_id: int, body: CreateStudentIn, request: Request,
                               actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Create a student profile from the reviewed fields (same rules as the master list) and
    link the document to it."""
    doc = get_or_404(db, IntakeDocument, intake_id)
    _require_linkable(doc)
    if _candidates(db, doc) and not body.confirm_new:
        raise _conflict("Students that may be the same person already exist: link one of them, "
                        "or confirm that this is a new student.")
    attrs = fields.student_values(doc.doc_type, _reviewed_values(doc))
    try:
        profile = StudentFields(student_number=body.student_number.strip(),
                                program_id=body.program_id, year_level=body.year_level,
                                admission_date=body.admission_date, **attrs)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}"
                             for err in e.errors())
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problems) from None
    student = add_student(db, profile)
    ip = client_ip(request)
    # Field names only (no values from the document) in either entry.
    audit.record(db, "student_created", user_id=actor.id, entity_type="student",
                 entity_id=student.id,
                 new_value={"source": "intake_document", "intake_id": doc.id,
                            "student_number": student.student_number,
                            "fields_from_document": sorted(attrs),
                            "program_id": student.program_id, "year_level": student.year_level},
                 ip_address=ip)
    _link(db, doc, student, actor, ip, how="created", match=None, reason=None)
    db.commit()
    return _fields_out(db, doc)


class UnlinkIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/intake/documents/{intake_id}/unlink")
def unlink_intake_document(intake_id: int, body: UnlinkIn, request: Request,
                           actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Undo a wrong link (with a reason). The student profile itself is not changed."""
    doc = get_or_404(db, IntakeDocument, intake_id)
    if doc.status != IntakeStatus.LINKED:
        raise _conflict("This document is not linked to a student.")
    old = {"status": doc.status.value, "student_id": doc.student_id}
    doc.student_id = None
    doc.linked_by = None
    doc.linked_at = None
    doc.status = IntakeStatus.DATA_REVIEWED
    audit.record(db, "intake_document_unlinked", user_id=actor.id, entity_type=ENTITY,
                 entity_id=doc.id, old_value=old,
                 new_value={"status": doc.status.value, "reason": body.reason.strip()},
                 ip_address=client_ip(request))
    db.commit()
    return _fields_out(db, doc)
