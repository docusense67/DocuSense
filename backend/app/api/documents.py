"""Issued documents: protected downloads, revocation, per-document timeline (5G, 5H, 18)."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_active_user, get_or_404, require_roles
from app.config import get_settings
from app.db import get_db
from app.models import AuditLog, GeneratedDocument, ScannedDocument, User
from app.models.enums import GeneratedDocumentStatus, Role
from app.schemas import ReasonIn
from app.security import sign_payload, verify_payload
from app.services import audit, workflow
from app.storage import StoredFileMissing, get_storage
from app.timeutil import utcnow

router = APIRouter(tags=["documents"])
registrar = require_roles(Role.REGISTRAR)


class DownloadLinkOut(BaseModel):
    url: str
    expires_at: datetime


class TimelineEntry(BaseModel):
    timestamp: datetime
    action: str
    user_id: int | None
    user_email: str | None
    entity_type: str | None
    details: dict | None


class DocumentOut(BaseModel):
    id: int
    document_number: str
    request_id: int
    template_id: int
    status: GeneratedDocumentStatus
    page_count: int
    data_hash: str
    data_snapshot: dict
    released_at: datetime | None
    download_expires_at: datetime | None
    revoked_reason: str | None
    timeline: list[TimelineEntry]


def _can_download(user: User, doc: GeneratedDocument) -> bool:
    """Registrar: any document (incl. drafts for review). Student: own, released, window open."""
    if user.role == Role.REGISTRAR:
        return True
    if user.role != Role.STUDENT or user.student is None:
        return False
    return doc.request.student_id == user.student.id and workflow.download_open(doc)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, _: User = Depends(registrar), db: Session = Depends(get_db)):
    doc = get_or_404(db, GeneratedDocument, document_id)
    # Section 5H per-document timeline: the request, every document generated for it
    # (regenerations included), each scan of those documents and its review decision.
    doc_ids = [str(i) for i in db.scalars(select(GeneratedDocument.id)
                                          .where(GeneratedDocument.request_id == doc.request_id))]
    scan_ids = [str(i) for i in db.scalars(select(ScannedDocument.id).where(
        ScannedDocument.matched_document_id.in_([int(i) for i in doc_ids])))]
    logs = db.execute(select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
                      .where(or_(
        and_(AuditLog.entity_type == "generated_document", AuditLog.entity_id.in_(doc_ids)),
        and_(AuditLog.entity_type == "document_request",
             AuditLog.entity_id == str(doc.request_id)),
        and_(AuditLog.entity_type == "scanned_document", AuditLog.entity_id.in_(scan_ids)),
    )).order_by(AuditLog.id)).all()
    return DocumentOut(
        id=doc.id, document_number=doc.document_number, request_id=doc.request_id,
        template_id=doc.template_id, status=doc.status, page_count=doc.page_count,
        data_hash=doc.data_hash, data_snapshot=doc.data_snapshot, released_at=doc.released_at,
        download_expires_at=doc.download_expires_at, revoked_reason=doc.revoked_reason,
        timeline=[TimelineEntry(timestamp=log.timestamp, action=log.action, user_id=log.user_id,
                                user_email=email, entity_type=log.entity_type,
                                details=log.new_value) for log, email in logs],
    )


@router.post("/documents/{document_id}/download-link", response_model=DownloadLinkOut)
def download_link(document_id: int, user: User = Depends(get_active_user),
                  db: Session = Depends(get_db)):
    """Short-lived, user-bound link (Section 18). PDFs are never served from a public path."""
    doc = get_or_404(db, GeneratedDocument, document_id)
    if not _can_download(user, doc):
        # Same answer whether the document is someone else's, unreleased, or expired.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not available for download")
    settings = get_settings()
    expires = utcnow() + timedelta(seconds=settings.download_link_seconds)
    token = sign_payload({"d": doc.id, "u": user.id, "exp": int(expires.timestamp())},
                         settings.secret_key)
    return DownloadLinkOut(url=f"/api/files/{token}", expires_at=expires)


@router.get("/files/{token}")
def serve_file(token: str, request: Request, user: User = Depends(get_active_user),
               db: Session = Depends(get_db)):
    payload = verify_payload(token, get_settings().secret_key)
    if (payload is None or payload.get("u") != user.id
            or payload.get("exp", 0) < utcnow().timestamp()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link expired or invalid")
    doc = get_or_404(db, GeneratedDocument, payload["d"])
    if not _can_download(user, doc):  # re-check: the document may have been revoked meanwhile
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not available for download")

    try:
        content = get_storage().get(doc.pdf_path)
    except StoredFileMissing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File missing") from None

    audit.record(db, "document_downloaded", user_id=user.id, entity_type="generated_document",
                 entity_id=doc.id, new_value={"document_number": doc.document_number},
                 ip_address=client_ip(request))
    db.commit()
    # inline, not attachment: the registrar's review page shows it in a frame (Section 5G);
    # students still save it from the browser's PDF viewer.
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{doc.document_number}.pdf"',
                             "Cache-Control": "no-store"})


@router.post("/documents/{document_id}/revoke")
def revoke_document(document_id: int, body: ReasonIn, request: Request,
                    actor: User = Depends(registrar), db: Session = Depends(get_db)):
    doc = get_or_404(db, GeneratedDocument, document_id)
    workflow.revoke(db, doc, actor, body.reason, client_ip(request))
    db.commit()
    return {"document_number": doc.document_number, "status": doc.status}


@router.post("/documents/{document_id}/reopen-download")
def reopen_download(document_id: int, body: ReasonIn, request: Request,
                    actor: User = Depends(registrar), db: Session = Depends(get_db)):
    doc = get_or_404(db, GeneratedDocument, document_id)
    workflow.reopen_download(db, doc, actor, body.reason, client_ip(request))
    db.commit()
    return {"document_number": doc.document_number,
            "download_expires_at": doc.download_expires_at}
