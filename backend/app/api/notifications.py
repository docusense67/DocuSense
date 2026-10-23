"""Notifications derived from the existing, durable workflow events (the audit log).

Two sources: document requests (students, registrar, admin) and the applicant intake
(docs/applicant-intake.md). Staff hear about online uploads that were read, could not be read
or need a closer look, and about reference samples to approve. An applicant, or the student
they became, hears only about their own submissions: read, accepted, please submit again, not
accepted. These never carry staff notes, reasons or anything read from the document.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_active_user
from app.db import get_db
from app.models import (
    ApplicantReference,
    ApplicantSubmission,
    AuditLog,
    DocumentRequest,
    GeneratedDocument,
    IntakeDocument,
    NotificationRead,
    Student,
    User,
)
from app.models.enums import IntakeSource, Role
from app.timeutil import as_utc

router = APIRouter(prefix="/notifications", tags=["notifications"])
LIMIT = 50
STAFF = (Role.REGISTRAR, Role.ADMIN)

# Approve-first order (S-11): approve the request, generate, verify, release.
MESSAGES = {
    "request_submitted": "Request filed",
    "request_approved": "Request approved",
    "document_generated": "Document waiting for verification",
    "document_verified": "Document verified",
    "document_released": "Document ready to download",
    "request_rejected": "Request rejected",
    "request_cancelled": "Request cancelled",
    "generation_blocked": "Request needs attention",
    "request_expired": "Download window expired",
    "document_revoked": "Document revoked",
}

DOC_LABEL = {"transcript_of_records": "Transcript of Records",
             "certificate_of_enrollment": "Certificate of Enrollment"}
# Applicant intake events for staff (registrar, admin).
STAFF_SUBMISSION_EVENTS = {
    "applicant_submission_failed": "Online upload could not be read",
    "applicant_second_pass_disagrees": "OCR passes disagree: check the fields",
    "applicant_second_pass_failed": "Second reading could not be done",
}
STAFF_REFERENCE_EVENTS = {
    "applicant_reference_read": "Reference sample ready to approve",
    "applicant_reference_failed": "Reference sample could not be read",
}
# For the applicant / student: own submissions only, plain words.
OWN_REVIEW = {
    "accept": "Your document was accepted",
    "reject": "Your document was not accepted",
    "request_resubmission": "Please submit your document again",
}


class NotificationOut(BaseModel):
    id: int
    title: str
    message: str
    timestamp: datetime
    href: str
    read: bool = False


def _request_events(db: Session, user: User) -> list[NotificationOut]:
    if user.role not in (Role.STUDENT, *STAFF):
        return []
    query = (
        select(AuditLog, DocumentRequest, Student)
        .select_from(AuditLog)
        .outerjoin(GeneratedDocument, and_(
            AuditLog.entity_type == "generated_document",
            AuditLog.entity_id == cast(GeneratedDocument.id, String),
        ))
        .join(DocumentRequest, or_(
            and_(AuditLog.entity_type == "document_request",
                 AuditLog.entity_id == cast(DocumentRequest.id, String)),
            GeneratedDocument.request_id == DocumentRequest.id,
        ))
        .join(Student, Student.id == DocumentRequest.student_id)
        .where(AuditLog.action.in_(MESSAGES))
    )
    if user.role == Role.STUDENT:
        query = query.where(Student.user_id == user.id)
    results = db.execute(query.order_by(AuditLog.id.desc()).limit(LIMIT))
    return [NotificationOut(
        id=event.id,
        title=MESSAGES[event.action],
        message=f"{student.first_name} {student.last_name} · {request.document_type.value} · {
            request.reference_no}",
        timestamp=as_utc(event.timestamp),
        href=(f"/requests/{request.id}" if user.role == Role.REGISTRAR
              else "/overview" if user.role == Role.ADMIN else "/"),
    ) for event, request, student in results]


def _describe(sub: ApplicantSubmission) -> str:
    return f"{DOC_LABEL.get(sub.declared_type.value, sub.declared_type.value)} · {
        sub.issuing_school}"


def _intake_events(db: Session, user: User) -> list[NotificationOut]:
    """Applicant intake events. Applicants and students: only their own submissions."""
    staff = user.role in STAFF
    if not staff and user.role not in (Role.APPLICANT, Role.STUDENT):
        return []
    own = None if staff else user.id
    out: list[NotificationOut] = []

    # Events on the intake document of an online upload (read, staff decisions).
    doc_actions = ["intake_document_screened"] + ([] if staff else ["intake_document_reviewed"])
    query = (
        select(AuditLog, IntakeDocument, ApplicantSubmission)
        .join(IntakeDocument, and_(AuditLog.entity_type == "intake_document",
                                   AuditLog.entity_id == cast(IntakeDocument.id, String)))
        .join(ApplicantSubmission, ApplicantSubmission.id == IntakeDocument.submission_id)
        .where(AuditLog.action.in_(doc_actions), IntakeDocument.source == IntakeSource.ONLINE)
    )
    if own is not None:
        query = query.where(ApplicantSubmission.applicant_id == own)
    for event, doc, sub in db.execute(query.order_by(AuditLog.id.desc()).limit(LIMIT)):
        if event.action == "intake_document_screened":
            if staff:
                title = ("Online upload passed screening"
                         if (event.new_value or {}).get("decision") == "passed_screening"
                         else "Online upload needs manual review")
            else:
                title = "Your document was read: waiting for the registrar's review"
        else:
            title = OWN_REVIEW.get((event.new_value or {}).get("action"))
            if title is None:
                continue
        out.append(NotificationOut(
            id=event.id, title=title, message=_describe(sub),
            timestamp=as_utc(event.timestamp),
            href=(f"/intake/{doc.id}" if user.role == Role.REGISTRAR
                  else "/overview" if user.role == Role.ADMIN else "/")))

    if staff:  # events on the submission itself, and on reference samples
        subs = db.execute(
            select(AuditLog, ApplicantSubmission)
            .join(ApplicantSubmission, and_(
                AuditLog.entity_type == "applicant_submission",
                AuditLog.entity_id == cast(ApplicantSubmission.id, String)))
            .where(AuditLog.action.in_(STAFF_SUBMISSION_EVENTS))
            .order_by(AuditLog.id.desc()).limit(LIMIT))
        for event, sub in subs:
            href = (f"/intake/{sub.intake_document_id}" if sub.intake_document_id
                    else "/applicants")
            out.append(NotificationOut(
                id=event.id, title=STAFF_SUBMISSION_EVENTS[event.action],
                message=_describe(sub), timestamp=as_utc(event.timestamp),
                href=href if user.role == Role.REGISTRAR else "/overview"))
        samples = db.execute(
            select(AuditLog, ApplicantReference)
            .join(ApplicantReference, and_(
                AuditLog.entity_type == "applicant_reference",
                AuditLog.entity_id == cast(ApplicantReference.id, String)))
            .where(AuditLog.action.in_(STAFF_REFERENCE_EVENTS))
            .order_by(AuditLog.id.desc()).limit(LIMIT))
        for event, ref in samples:
            out.append(NotificationOut(
                id=event.id, title=STAFF_REFERENCE_EVENTS[event.action],
                message=f"{ref.issuing_school} · version {ref.version}",
                timestamp=as_utc(event.timestamp), href="/references"))
    return out


@router.get("", response_model=list[NotificationOut])
def notifications(user: User = Depends(get_active_user), db: Session = Depends(get_db)):
    output = _request_events(db, user) + _intake_events(db, user)
    output = sorted(output, key=lambda n: n.id, reverse=True)[:LIMIT]
    read_ids = set(db.scalars(select(NotificationRead.event_id).where(
        NotificationRead.user_id == user.id)))
    for item in output:
        item.read = item.id in read_ids
    return output


class ReadIn(BaseModel):
    ids: list[int] = Field(max_length=100)


@router.post("/read")
def mark_read(body: ReadIn, user: User = Depends(get_active_user), db: Session = Depends(get_db)):
    visible_ids = {item.id for item in notifications(user, db)}
    existing = set(db.scalars(select(NotificationRead.event_id).where(
        NotificationRead.user_id == user.id)))
    for event_id in set(body.ids) & visible_ids - existing:
        try:
            with db.begin_nested():
                db.add(NotificationRead(user_id=user.id, event_id=event_id))
                db.flush()
        except IntegrityError:
            pass
    db.commit()
    return {"status": "ok"}
