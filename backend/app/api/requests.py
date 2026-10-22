"""Document requests (Sections 5E, 5G, 14). Students act on their own; registrars on all."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_active_user, get_or_404, require_roles
from app.config import get_settings
from app.db import get_db
from app.documents.context import BuildOptions, MissingDataError
from app.documents.registry import REGISTRY
from app.models import AuditLog, DocumentRequest, GeneratedDocument, Student, User
from app.models.enums import (
    ClientStatus,
    DocumentType,
    GeneratedDocumentStatus,
    RequestStatus,
    Role,
)
from app.schemas import ReasonIn
from app.services import workflow
from app.services.eligibility import check_eligibility
from app.timeutil import as_utc

router = APIRouter(prefix="/requests", tags=["requests"])
registrar = require_roles(Role.REGISTRAR)
requester = require_roles(Role.STUDENT, Role.REGISTRAR)


class RequestIn(BaseModel):
    document_type: DocumentType
    purpose: str = Field(min_length=2, max_length=200)
    copies: int = Field(default=1, ge=1, le=10)
    remarks: str | None = Field(default=None, max_length=1000)
    student_id: int | None = None  # registrar filing for a walk-in student


class DocumentSummary(BaseModel):
    id: int
    document_number: str
    status: GeneratedDocumentStatus
    page_count: int
    released_at: datetime | None
    download_expires_at: datetime | None
    can_download: bool
    downloads: list[datetime] = []  # Section 16 student history: when it was downloaded


class RequestOut(BaseModel):
    id: int
    reference_no: str
    student_id: int
    student_number: str
    student_name: str
    document_type: DocumentType
    purpose: str
    copies: int
    remarks: str | None
    status: RequestStatus  # registrar sub-step (Section 14, S-11 order)
    client_status: ClientStatus  # what the student sees: pending/approved/rejected/completed...
    payment_confirmed: bool
    reject_reason: str | None
    processing_note: str | None
    requested_at: datetime
    document: DocumentSummary | None


class RequestDetailOut(RequestOut):
    snapshot: dict | None = None  # exactly what is printed on the document
    current_data: dict | None = None  # rebuilt from today's records, for side-by-side review
    current_data_error: list[str] | None = None
    differs_from_records: bool | None = None
    # School settings (Section 14) so the review screen labels its buttons truthfully.
    payment_required: bool = False
    auto_release: bool = True


def _summary(doc: GeneratedDocument | None,
             downloads: list[datetime] | None = None) -> DocumentSummary | None:
    if doc is None:
        return None
    return DocumentSummary(
        id=doc.id, document_number=doc.document_number, status=doc.status,
        page_count=doc.page_count, released_at=doc.released_at,
        download_expires_at=doc.download_expires_at, can_download=workflow.download_open(doc),
        downloads=downloads or [],
    )


def _downloads(db: Session, user: User, requests: list[DocumentRequest]) -> dict[int, list]:
    """Download times per document from the audit log; students see their own downloads."""
    ids = [str(d.id) for r in requests if (d := workflow.latest_document(r))]
    if not ids:
        return {}
    query = select(AuditLog.entity_id, AuditLog.timestamp).where(
        AuditLog.action == "document_downloaded", AuditLog.entity_type == "generated_document",
        AuditLog.entity_id.in_(ids)).order_by(AuditLog.id)
    if user.role == Role.STUDENT:
        query = query.where(AuditLog.user_id == user.id)
    found: dict[int, list] = {}
    for entity_id, when in db.execute(query):
        found.setdefault(int(entity_id), []).append(as_utc(when))
    return found


def _out(request: DocumentRequest, downloads: dict[int, list] | None = None) -> RequestOut:
    doc = workflow.latest_document(request)
    student = request.student
    return RequestOut(
        id=request.id, reference_no=request.reference_no, student_id=student.id,
        student_number=student.student_number,
        student_name=f"{student.last_name}, {student.first_name}",
        document_type=request.document_type, purpose=request.purpose, copies=request.copies,
        remarks=request.remarks, status=request.status,
        client_status=workflow.client_status(request.status),
        payment_confirmed=request.payment_confirmed, reject_reason=request.reject_reason,
        processing_note=request.processing_note,
        requested_at=as_utc(request.requested_at),
        document=_summary(doc, (downloads or {}).get(doc.id) if doc else None),
    )


def _visible_request(db: Session, user: User, request_id: int) -> DocumentRequest:
    request = get_or_404(db, DocumentRequest, request_id)
    if user.role == Role.STUDENT and (user.student is None
                                      or request.student_id != user.student.id):
        # 404, not 403: don't confirm that another student's request exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DocumentRequest not found")
    return request


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def submit_request(body: RequestIn, request: Request, user: User = Depends(requester),
                   db: Session = Depends(get_db)):
    """Eligibility runs immediately; an ineligible request comes back Rejected with reasons,
    an eligible one waits as Requested (Pending) for the registrar's approval."""
    if user.role == Role.STUDENT:
        if body.student_id is not None or user.student is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Students request for themselves only")
        student = user.student
    else:
        if body.student_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "student_id is required")
        student = get_or_404(db, Student, body.student_id)

    doc_request = workflow.submit(db, student=student, doc_type=body.document_type,
                                  purpose=body.purpose, copies=body.copies,
                                  remarks=body.remarks, actor=user, ip=client_ip(request))
    db.commit()
    return _out(doc_request)


@router.get("", response_model=list[RequestOut])
def list_requests(
    status_filter: RequestStatus | None = Query(None, alias="status"),
    client_status: ClientStatus | None = None,
    document_type: DocumentType | None = None,
    user: User = Depends(requester),
    db: Session = Depends(get_db),
):
    """Students: their own requests, newest first. Registrar: all, oldest first (a queue)."""
    if workflow.expire_overdue(db):
        db.commit()
    query = select(DocumentRequest)
    if user.role == Role.STUDENT:
        if user.student is None:
            return []
        query = query.where(DocumentRequest.student_id == user.student.id)
        query = query.order_by(DocumentRequest.id.desc())
    else:
        query = query.order_by(DocumentRequest.id)
    if status_filter is not None:
        query = query.where(DocumentRequest.status == status_filter)
    if client_status is not None:
        query = query.where(DocumentRequest.status.in_(
            [s for s in RequestStatus if workflow.client_status(s) == client_status]))
    if document_type is not None:
        query = query.where(DocumentRequest.document_type == document_type)
    found = list(db.scalars(query.limit(500)))
    downloads = _downloads(db, user, found)
    return [_out(r, downloads) for r in found]


@router.get("/eligibility")
def my_eligibility(user: User = Depends(require_roles(Role.STUDENT)),
                   db: Session = Depends(get_db)):
    """Which document types this student may request right now, with reasons if not.
    Declared before /{request_id} so the path isn't parsed as an id."""
    if user.student is None:
        return {t.value: ["No student profile linked to this account"] for t in DocumentType}
    return {t.value: check_eligibility(db, user.student, t) for t in REGISTRY}


@router.get("/{request_id}", response_model=RequestDetailOut)
def get_request(request_id: int, user: User = Depends(get_active_user),
                db: Session = Depends(get_db)):
    if user.role not in (Role.STUDENT, Role.REGISTRAR):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
    doc_request = _visible_request(db, user, request_id)
    settings = get_settings()
    detail = RequestDetailOut(**_out(doc_request).model_dump(),
                              payment_required=settings.payment_required,
                              auto_release=settings.auto_release_on_approval)
    doc = workflow.latest_document(doc_request)
    if doc is not None:
        detail.snapshot = doc.data_snapshot
    if user.role == Role.REGISTRAR and doc is not None:
        # Side-by-side review (Section 5G): printed snapshot vs. what the records say today.
        spec = REGISTRY[doc_request.document_type]
        try:
            current = spec.build_context(db, doc_request.student,
                                         BuildOptions(issued_on=date.today(),
                                                      purpose=doc_request.purpose))
        except MissingDataError as e:
            detail.current_data_error = e.missing
            detail.differs_from_records = True
        else:
            detail.current_data = current
            ignore = {"document_number", "document_type", "date_issued"}
            printed = {k: v for k, v in doc.data_snapshot.items() if k not in ignore}
            today = {k: v for k, v in current.items() if k not in ignore}
            detail.differs_from_records = printed != today
    return detail


@router.post("/{request_id}/cancel", response_model=RequestOut)
def cancel_request(request_id: int, request: Request, body: ReasonIn | None = None,
                   user: User = Depends(requester), db: Session = Depends(get_db)):
    """Students may cancel without a reason; the registrar must give one (Section 4)."""
    doc_request = _visible_request(db, user, request_id)
    workflow.cancel(db, doc_request, user, client_ip(request), body.reason if body else None)
    db.commit()
    return _out(doc_request)


@router.post("/{request_id}/confirm-payment", response_model=RequestOut)
def confirm_payment(request_id: int, request: Request, actor: User = Depends(registrar),
                    db: Session = Depends(get_db)):
    doc_request = get_or_404(db, DocumentRequest, request_id)
    workflow.confirm_payment(db, doc_request, actor, client_ip(request))
    db.commit()
    return _out(doc_request)


@router.post("/{request_id}/approve", response_model=RequestOut)
def approve_request(request_id: int, request: Request, actor: User = Depends(registrar),
                    db: Session = Depends(get_db)):
    """Approve the request (S-11): records are retrieved and the document generated only now.
    Comes back For Verification, Approved with a processing note if generation is blocked, or
    Rejected if the student is no longer eligible."""
    doc_request = get_or_404(db, DocumentRequest, request_id)
    workflow.approve(db, doc_request, actor, client_ip(request))
    db.commit()
    return _out(doc_request)


@router.post("/{request_id}/verify", response_model=RequestOut)
def verify_request(request_id: int, request: Request, actor: User = Depends(registrar),
                   db: Session = Depends(get_db)):
    """The registrar verified the generated document; released unless release is manual."""
    doc_request = get_or_404(db, DocumentRequest, request_id)
    workflow.verify(db, doc_request, actor, client_ip(request))
    db.commit()
    return _out(doc_request)


@router.post("/{request_id}/release", response_model=RequestOut)
def release_request(request_id: int, request: Request, actor: User = Depends(registrar),
                    db: Session = Depends(get_db)):
    doc_request = get_or_404(db, DocumentRequest, request_id)
    workflow.release(db, doc_request, actor, client_ip(request))
    db.commit()
    return _out(doc_request)


@router.post("/{request_id}/reject", response_model=RequestOut)
def reject_request(request_id: int, body: ReasonIn, request: Request,
                   actor: User = Depends(registrar), db: Session = Depends(get_db)):
    doc_request = get_or_404(db, DocumentRequest, request_id)
    workflow.reject(db, doc_request, actor, body.reason, client_ip(request))
    db.commit()
    return _out(doc_request)


@router.post("/{request_id}/regenerate", response_model=RequestOut)
def regenerate_request(request_id: int, body: ReasonIn, request: Request,
                       actor: User = Depends(registrar), db: Session = Depends(get_db)):
    doc_request = get_or_404(db, DocumentRequest, request_id)
    workflow.regenerate(db, doc_request, actor, body.reason, client_ip(request))
    db.commit()
    return _out(doc_request)
