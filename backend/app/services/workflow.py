"""Document request lifecycle (Section 14, approve-first order of change S-11) and document
release/revocation (Section 5G).

    Requested --approve--> Approved --generate--> For Verification --verify--> Verified --> Released
     (Pending)  (registrar)  (blocked: stays      (registrar checks   (auto-release   (Completed)
        |                    Approved + note)      the document)       skips this)       |
        +-> Rejected (not eligible, or registrar, with reason)              Expired | Revoked
        +-> Cancelled (student, or registrar with reason, until verified)

The registrar approves the REQUEST before any record is retrieved or any document number is
used; the generated document is then verified side by side before release, and may be sent back
for regeneration (new document number). Expired only ends the student's download window; the
document stays valid for scanning. Revoked makes future scans INVALID. Every transition is
audited. Callers commit.
"""

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.documents.context import MissingDataError
from app.documents.pdf import RendererUnavailable
from app.documents.service import GenerationError, generate_document
from app.documents.templating import TemplateRenderError
from app.models import DocumentRequest, GeneratedDocument, Student, User
from app.models.enums import (
    ClientStatus,
    DocumentType,
    GeneratedDocumentStatus,
    RequestStatus,
    Role,
)
from app.services import audit
from app.services.eligibility import check_eligibility
from app.services.numbering import next_number
from app.timeutil import as_utc, utcnow

R = RequestStatus
D = GeneratedDocumentStatus
C = ClientStatus

# A student may have only one of these per document type at a time (Section 5E).
OPEN_STATUSES = {R.REQUESTED, R.APPROVED, R.FOR_VERIFICATION, R.VERIFIED}
# Before verification the student or the registrar may still withdraw the request.
CANCELLABLE = (R.REQUESTED, R.APPROVED, R.FOR_VERIFICATION)

# Students see four words (Updated Features II-B); the registrar sees the sub-steps.
_CLIENT = {R.REQUESTED: C.PENDING, R.APPROVED: C.APPROVED, R.FOR_VERIFICATION: C.APPROVED,
           R.VERIFIED: C.APPROVED, R.RELEASED: C.COMPLETED, R.REJECTED: C.REJECTED,
           R.CANCELLED: C.CANCELLED, R.EXPIRED: C.EXPIRED, R.REVOKED: C.REVOKED}


def client_status(request_status: RequestStatus) -> ClientStatus:
    return _CLIENT[request_status]


def _log(db: Session, action: str, actor: User | None, request: DocumentRequest,
         ip: str | None = None, **values) -> None:
    audit.record(db, action, user_id=actor.id if actor else None,
                 entity_type="document_request", entity_id=request.id,
                 new_value={"reference_no": request.reference_no, **values},
                 ip_address=ip)


def _log_doc(db: Session, action: str, actor: User | None, doc: GeneratedDocument,
             ip: str | None = None, **values) -> None:
    audit.record(db, action, user_id=actor.id if actor else None,
                 entity_type="generated_document", entity_id=doc.id,
                 new_value={"document_number": doc.document_number, **values},
                 ip_address=ip)


def _require(request: DocumentRequest, *allowed: RequestStatus) -> None:
    if request.status not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Request is {request.status.value}; this action needs "
            f"{' or '.join(s.value for s in allowed)}",
        )


def latest_document(request: DocumentRequest) -> GeneratedDocument | None:
    return request.generated_documents[-1] if request.generated_documents else None


def _void_unreleased(db: Session, request: DocumentRequest, actor: User | None, reason: str,
                     ip: str | None) -> None:
    """A generated-but-unreleased file can never become official once its request moves on."""
    doc = latest_document(request)
    if doc is not None and doc.status in (D.GENERATED, D.APPROVED):
        doc.status = D.REVOKED
        doc.revoked_reason = reason
        _log_doc(db, "document_voided", actor, doc, ip, reason=reason)


def next_reference_no(db: Session, year: int) -> str:
    return next_number(db, DocumentRequest.reference_no, f"REQ-{year}-")


def _eligible(db: Session, request: DocumentRequest, actor: User | None,
              ip: str | None) -> bool:
    """Run the eligibility rules; an ineligible request is rejected automatically."""
    reasons = check_eligibility(db, request.student, request.document_type)
    _log(db, "eligibility_checked", actor, request, ip, eligible=not reasons, reasons=reasons)
    if reasons:
        request.status = R.REJECTED
        request.reject_reason = "Not eligible: " + "; ".join(reasons)
        _log(db, "request_rejected", None, request, ip, reason=request.reject_reason,
             automatic=True)
    return not reasons


# ---- submission -----------------------------------------------------------------------------

def submit(db: Session, *, student: Student, doc_type: DocumentType, purpose: str, copies: int,
           remarks: str | None, actor: User, ip: str | None) -> DocumentRequest:
    """File a request. Eligibility is checked at once (an ineligible request comes back
    Rejected with reasons); an eligible one waits as Requested for the registrar's approval.
    Nothing is retrieved or generated yet."""
    duplicate = db.scalar(select(DocumentRequest.reference_no).where(
        DocumentRequest.student_id == student.id, DocumentRequest.document_type == doc_type,
        DocumentRequest.status.in_(OPEN_STATUSES)))
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A {doc_type.value} request is already pending ({duplicate})")

    request = DocumentRequest(
        reference_no=next_reference_no(db, utcnow().year), student_id=student.id,
        document_type=doc_type, purpose=purpose.strip(), copies=copies, remarks=remarks,
        status=R.REQUESTED, requested_by=actor.id,
    )
    db.add(request)
    db.flush()
    _log(db, "request_submitted", actor, request, ip, document_type=doc_type.value,
         on_behalf=actor.role != Role.STUDENT)
    _eligible(db, request, actor, ip)
    return request


def confirm_payment(db: Session, request: DocumentRequest, actor: User, ip: str | None) -> None:
    """Only when the school charges a fee. Approval waits for it; it starts nothing itself."""
    _require(request, R.REQUESTED)
    if request.payment_confirmed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment is already confirmed")
    request.payment_confirmed = True
    _log(db, "payment_confirmed", actor, request, ip)


def cancel(db: Session, request: DocumentRequest, actor: User, ip: str | None,
           reason: str | None = None) -> None:
    # Allowed until the registrar has verified the document.
    _require(request, *CANCELLABLE)
    if actor.role == Role.REGISTRAR and not reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "A reason is required when the registrar cancels a request")
    request.status = R.CANCELLED
    _void_unreleased(db, request, actor, reason or "Request cancelled", ip)
    _log(db, "request_cancelled", actor, request, ip, **({"reason": reason} if reason else {}))


# ---- registrar approval, then retrieval + generation ----------------------------------------

def approve(db: Session, request: DocumentRequest, actor: User, ip: str | None) -> None:
    """Approve the REQUEST. Only now are the records retrieved and the document generated.

    Eligibility is checked again (records may have changed since submission): an ineligible
    request is rejected automatically instead. When generation is blocked (missing data, no
    active template) the request stays Approved with a processing note until regenerated."""
    _require(request, R.REQUESTED)
    if get_settings().payment_required and not request.payment_confirmed:
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirm the payment before approving")
    if not _eligible(db, request, actor, ip):
        return
    request.status = R.APPROVED
    request.reviewed_by = actor.id
    _log(db, "request_approved", actor, request, ip)
    _generate(db, request, actor, ip)


def _generate(db: Session, request: DocumentRequest, actor: User | None, ip: str | None) -> None:
    try:
        generate_document(db, request, actor_id=actor.id if actor else None)
    except (MissingDataError, GenerationError, TemplateRenderError, RendererUnavailable) as e:
        # Section 6: "Blocked: registrar alerted". The request waits as Approved.
        request.processing_note = str(e)
        _log(db, "generation_blocked", actor, request, ip, reason=str(e))
        return
    request.processing_note = None
    request.status = R.FOR_VERIFICATION


def regenerate(db: Session, request: DocumentRequest, actor: User, reason: str,
               ip: str | None) -> None:
    """Send back for regeneration: retry a blocked generation, replace a document that failed
    verification, or reopen a rejected request after the records were corrected (UC2
    alternative). Always a fresh document with a new number; the old one is voided."""
    _require(request, R.APPROVED, R.FOR_VERIFICATION, R.REJECTED)
    _void_unreleased(db, request, actor, "Superseded by regeneration", ip)
    request.reject_reason = None
    _log(db, "request_regenerated", actor, request, ip, reason=reason)
    if not _eligible(db, request, actor, ip):
        return
    request.status = R.APPROVED
    request.reviewed_by = actor.id
    _generate(db, request, actor, ip)


# ---- verification of the generated document, then release -----------------------------------

def verify(db: Session, request: DocumentRequest, actor: User, ip: str | None) -> None:
    """The registrar checked the generated document against the records (side-by-side
    review). It is released at once unless the school releases separately (e.g. pickup)."""
    _require(request, R.FOR_VERIFICATION)
    doc = latest_document(request)
    if doc is None or doc.status != D.GENERATED:
        raise HTTPException(status.HTTP_409_CONFLICT, "No generated document to verify")
    doc.status = D.APPROVED
    doc.approved_by = actor.id
    request.status = R.VERIFIED
    request.reviewed_by = actor.id
    _log_doc(db, "document_verified", actor, doc, ip)
    if get_settings().auto_release_on_approval:
        release(db, request, actor, ip)


def release(db: Session, request: DocumentRequest, actor: User, ip: str | None) -> None:
    _require(request, R.VERIFIED)
    doc = latest_document(request)
    now = utcnow()
    doc.status = D.RELEASED
    doc.released_at = now
    doc.download_expires_at = now + timedelta(days=get_settings().download_window_days)
    request.status = R.RELEASED
    _log_doc(db, "document_released", actor, doc, ip,
             download_expires_at=doc.download_expires_at.isoformat())


def reject(db: Session, request: DocumentRequest, actor: User, reason: str,
           ip: str | None) -> None:
    """Reject the request (before approval) or the whole request at verification."""
    _require(request, *CANCELLABLE)
    request.status = R.REJECTED
    request.reject_reason = reason
    request.reviewed_by = actor.id
    _void_unreleased(db, request, actor, f"Request rejected: {reason}", ip)
    _log(db, "request_rejected", actor, request, ip, reason=reason)


# ---- after release --------------------------------------------------------------------------

def revoke(db: Session, doc: GeneratedDocument, actor: User, reason: str,
           ip: str | None) -> None:
    if doc.status != D.RELEASED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only released documents can be revoked")
    doc.status = D.REVOKED
    doc.revoked_reason = reason
    doc.request.status = R.REVOKED
    _log_doc(db, "document_revoked", actor, doc, ip, reason=reason)


def reopen_download(db: Session, doc: GeneratedDocument, actor: User, reason: str,
                    ip: str | None) -> None:
    """UC3 alternative: registrar re-opens an expired download window."""
    if doc.status != D.RELEASED or doc.request.status not in (R.RELEASED, R.EXPIRED):
        raise HTTPException(status.HTTP_409_CONFLICT, "Document is not released")
    doc.download_expires_at = utcnow() + timedelta(days=get_settings().download_window_days)
    doc.request.status = R.RELEASED
    _log_doc(db, "download_reopened", actor, doc, ip, reason=reason,
             download_expires_at=doc.download_expires_at.isoformat())


def download_open(doc: GeneratedDocument) -> bool:
    return (doc.status == D.RELEASED and doc.download_expires_at is not None
            and as_utc(doc.download_expires_at) > utcnow())


def expire_overdue(db: Session) -> int:
    """Released requests whose download window has closed become Expired. Run on a schedule
    (python -m app.jobs) and lazily before student-facing reads."""
    # Python-side date check keeps SQLite/Postgres behaviour identical; fine at pilot scale.
    count = 0
    candidates = db.scalars(select(DocumentRequest).where(DocumentRequest.status == R.RELEASED))
    for request in candidates:
        doc = latest_document(request)
        if doc is not None and doc.status == D.RELEASED and not download_open(doc):
            request.status = R.EXPIRED
            _log(db, "request_expired", None, request)
            count += 1
    return count
