"""Request notifications derived from the existing, durable workflow events."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_active_user
from app.db import get_db
from app.models import (
    AuditLog,
    DocumentRequest,
    GeneratedDocument,
    NotificationRead,
    Student,
    User,
)
from app.models.enums import Role
from app.timeutil import as_utc

router = APIRouter(prefix="/notifications", tags=["notifications"])

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


class NotificationOut(BaseModel):
    id: int
    title: str
    message: str
    timestamp: datetime
    href: str
    read: bool = False


@router.get("", response_model=list[NotificationOut])
def notifications(user: User = Depends(get_active_user), db: Session = Depends(get_db)):
    if user.role not in (Role.STUDENT, Role.REGISTRAR, Role.ADMIN):
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
    results = db.execute(query.order_by(AuditLog.id.desc()).limit(50))
    output = [NotificationOut(
        id=event.id,
        title=MESSAGES[event.action],
        message=f"{student.first_name} {student.last_name} · {request.document_type.value} · {
            request.reference_no}",
        timestamp=as_utc(event.timestamp),
        href=(f"/requests/{request.id}" if user.role == Role.REGISTRAR
              else "/overview" if user.role == Role.ADMIN else "/"),
    ) for event, request, student in results]
    output = sorted(output, key=lambda n: n.id, reverse=True)[:50]
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
