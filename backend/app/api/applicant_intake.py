"""Applicant intake (docs/applicant-intake.md): documents from another school, uploaded online.

- Registrar/admin create applicant logins (no public sign-up) and watch the submissions.
- An applicant uploads the pages of a TOR or COE (one request per page or PDF, under the
  Vercel proxy's body limit) and submits them. Applicants only ever see their own submissions.
- The OCR worker on the station (X-Station-Key) claims a submission with a lease, reads the
  pages and sends the reading. It extracts only: services/applicant_intake.decide decides, and
  the backend creates an intake document (source "online") that joins the staff review queue.
  From there the existing review, field review and student link apply (api/intake.py,
  api/intake_profiles.py). Wording: "passed screening" / "needs manual review".

Audit entries never carry document text or field values.
"""

import hashlib
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from fastapi import status as http
from pydantic import BaseModel, EmailStr, Field, StringConstraints
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import client_ip, get_or_404, get_station, require_roles
from app.api.intake import load_thresholds
from app.config import get_settings
from app.db import get_db
from app.models import ApplicantPage, ApplicantSubmission, IntakeDocument, ScanStation, User
from app.models.enums import (
    ApplicantSubmissionStatus,
    IntakeDocType,
    IntakeSource,
    IntakeStatus,
    Role,
)
from app.services import applicant_intake as rules
from app.services import audit
from app.services import intake as screening
from app.services.accounts import create_account
from app.services.scan_images import InvalidScanImage, compress_page, render_pdf_pages
from app.services.scanning import IMAGE_MEDIA_TYPES
from app.storage import StoredFileMissing, get_storage
from app.timeutil import utcnow

router = APIRouter(tags=["applicant intake"])
staff = require_roles(Role.REGISTRAR, Role.ADMIN)
applicant = require_roles(Role.APPLICANT)
ENTITY = "applicant_submission"
Status = ApplicantSubmissionStatus
OPEN_LIMIT = 5  # unfinished submissions (draft/queued/processing) per applicant
# OCR needs more detail than the validation review screen: keep a higher JPEG quality.
PAGE_JPEG_QUALITY = 85
MAX_READING_CHARS = 256_000

# What the applicant sees: plain words, no staff notes.
APPLICANT_STATUS = {
    Status.DRAFT: "Draft: add the pages, then submit",
    Status.QUEUED: "Submitted: waiting to be read",
    Status.PROCESSING: "Being read",
    Status.FAILED: "Could not be read: the registrar will contact you",
    Status.WITHDRAWN: "Withdrawn",
}
REVIEW_STATUS = {
    IntakeStatus.SCREENED: "Waiting for the registrar's review",
    IntakeStatus.ACCEPTED: "Accepted by the registrar",
    IntakeStatus.DATA_REVIEWED: "Accepted by the registrar",
    IntakeStatus.LINKED: "Accepted: linked to your student record",
    IntakeStatus.REJECTED: "Not accepted: contact the registrar",
    IntakeStatus.RESUBMISSION_REQUESTED: "Please submit this document again",
}


def _conflict(message: str) -> HTTPException:
    return HTTPException(http.HTTP_409_CONFLICT, message)


def _status_text(db: Session, sub: ApplicantSubmission) -> str:
    if sub.status == Status.SCREENED and sub.intake_document_id:
        doc = db.get(IntakeDocument, sub.intake_document_id)
        if doc is not None:
            return REVIEW_STATUS[doc.status]
    return APPLICANT_STATUS.get(sub.status, sub.status.value)


def _own_out(db: Session, sub: ApplicantSubmission) -> dict:
    """The applicant's view: no lease, error or staff details."""
    return {"id": sub.id, "declared_type": sub.declared_type,
            "issuing_school": sub.issuing_school, "status": sub.status,
            "status_text": _status_text(db, sub), "pages": len(sub.pages),
            "created_at": sub.created_at, "submitted_at": sub.submitted_at}


def _staff_out(db: Session, sub: ApplicantSubmission) -> dict:
    user = db.get(User, sub.applicant_id)
    return {**_own_out(db, sub), "applicant_email": user.email if user else None,
            "attempts": sub.attempts, "error": sub.error,
            "intake_document_id": sub.intake_document_id,
            "lease_expires_at": sub.lease_expires_at}


# ---- staff: applicant logins and the submission list ----------------------------------------

class ApplicantIn(BaseModel):
    email: EmailStr
    temporary_password: str


@router.post("/applicants", status_code=http.HTTP_201_CREATED)
def create_applicant(body: ApplicantIn, request: Request, actor: User = Depends(staff),
                     db: Session = Depends(get_db)):
    """An applicant login (must change its password at first login). No public sign-up."""
    user = create_account(db, actor=actor, email=body.email, role=Role.APPLICANT,
                          temporary_password=body.temporary_password,
                          ip_address=client_ip(request))
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role}


@router.get("/applicant-submissions")
def list_submissions(state: ApplicantSubmissionStatus | None = None, limit: int = 100,
                     _: User = Depends(staff), db: Session = Depends(get_db)):
    query = select(ApplicantSubmission)
    if state is not None:
        query = query.where(ApplicantSubmission.status == state)
    query = query.order_by(ApplicantSubmission.id.desc()).limit(min(max(limit, 1), 500))
    return [_staff_out(db, s) for s in db.scalars(query)]


def _page_response(sub: ApplicantSubmission, number: int) -> Response:
    page = next((p for p in sub.pages if p.sequence == number), None)
    if page is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Page not found")
    try:
        content = get_storage().get(page.storage_key)
    except StoredFileMissing:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Page missing") from None
    media = IMAGE_MEDIA_TYPES.get(page.storage_key.rsplit(".", 1)[-1], "image/jpeg")
    return Response(content, media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/applicant-submissions/{submission_id}/pages/{number}")
def staff_page(submission_id: int, number: int, _: User = Depends(staff),
               db: Session = Depends(get_db)):
    """Streamed by the API: storage URLs never reach the browser."""
    return _page_response(get_or_404(db, ApplicantSubmission, submission_id), number)


class RetryIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/applicant-submissions/{submission_id}/retry")
def retry_submission(submission_id: int, body: RetryIn, request: Request,
                     actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Queue a failed submission again (e.g. after the worker was fixed)."""
    sub = get_or_404(db, ApplicantSubmission, submission_id)
    if sub.status != Status.FAILED:
        raise _conflict("Only a submission that could not be read can be queued again.")
    sub.status = Status.QUEUED
    sub.attempts = 0
    sub.error = None
    audit.record(db, "applicant_submission_requeued", user_id=actor.id, entity_type=ENTITY,
                 entity_id=sub.id, old_value={"status": Status.FAILED.value},
                 new_value={"status": sub.status.value, "reason": body.reason.strip()},
                 ip_address=client_ip(request))
    db.commit()
    return _staff_out(db, sub)


# ---- applicant: own submissions ---------------------------------------------------------------

def _own(db: Session, user: User, submission_id: int) -> ApplicantSubmission:
    sub = db.get(ApplicantSubmission, submission_id)
    if sub is None or sub.applicant_id != user.id:  # someone else's: not found
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Submission not found")
    return sub


def _require_draft(sub: ApplicantSubmission) -> None:
    if sub.status != Status.DRAFT:
        raise _conflict("This submission was already submitted.")


@router.get("/applicant/submissions")
def my_submissions(user: User = Depends(applicant), db: Session = Depends(get_db)):
    subs = db.scalars(select(ApplicantSubmission).where(ApplicantSubmission.applicant_id == user.id)
                      .order_by(ApplicantSubmission.id.desc()))
    return [_own_out(db, s) for s in subs]


School = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class SubmissionIn(BaseModel):
    declared_type: IntakeDocType
    issuing_school: School
    # The applicant read the privacy notice and agrees to the processing (Data Privacy Act).
    consent: bool


@router.post("/applicant/submissions", status_code=http.HTTP_201_CREATED)
def start_submission(body: SubmissionIn, request: Request, user: User = Depends(applicant),
                     db: Session = Depends(get_db)):
    if body.declared_type not in rules.ONLINE_TYPES:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Choose a Transcript of Records or a Certificate of Enrollment")
    if not body.consent:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Agree to the privacy notice to send documents")
    open_count = db.scalar(select(func.count()).select_from(ApplicantSubmission).where(
        ApplicantSubmission.applicant_id == user.id,
        ApplicantSubmission.status.in_([Status.DRAFT, Status.QUEUED, Status.PROCESSING])))
    if open_count >= OPEN_LIMIT:
        raise _conflict(f"You have {OPEN_LIMIT} unfinished submissions: submit or withdraw one "
                        "first.")
    now = utcnow()
    sub = ApplicantSubmission(applicant_id=user.id, declared_type=body.declared_type,
                              issuing_school=body.issuing_school, status=Status.DRAFT,
                              consent_at=now, attempts=0)
    db.add(sub)
    db.flush()
    audit.record(db, "applicant_submission_started", user_id=user.id, entity_type=ENTITY,
                 entity_id=sub.id, new_value={"declared_type": sub.declared_type.value,
                                              "consent_at": now.isoformat()},
                 ip_address=client_ip(request))
    db.commit()
    return _own_out(db, sub)


@router.get("/applicant/submissions/{submission_id}")
def my_submission(submission_id: int, user: User = Depends(applicant),
                  db: Session = Depends(get_db)):
    return _own_out(db, _own(db, user, submission_id))


@router.post("/applicant/submissions/{submission_id}/pages", status_code=http.HTTP_201_CREATED)
async def upload_pages(submission_id: int, request: Request, file: UploadFile = File(...),
                       user: User = Depends(applicant), db: Session = Depends(get_db)):
    """One page image (PNG/JPEG) or a PDF (split into its pages), appended in order."""
    sub = _own(db, user, submission_id)
    _require_draft(sub)
    settings = get_settings()
    limit = settings.applicant_upload_max_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(http.HTTP_413_CONTENT_TOO_LARGE,
                            f"Each upload must be under {limit // 1_000_000} MB: upload the "
                            "pages one at a time or scan at a lower resolution")
    try:
        kind = rules.upload_kind(content)
    except rules.InvalidUpload as e:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    digest = hashlib.sha256(content).hexdigest()
    if any(p.sha256 == digest for p in sub.pages):
        raise _conflict("This file was already uploaded to this submission.")
    room = settings.applicant_max_pages - len(sub.pages)
    if room <= 0:
        raise _conflict(f"A submission has at most {settings.applicant_max_pages} pages.")
    try:  # CPU-bound: off the event loop
        if kind == "pdf":
            pages = await run_in_threadpool(
                render_pdf_pages, content, max_pages=room,
                max_width=settings.scan_page_max_width, quality=PAGE_JPEG_QUALITY)
        else:
            pages = [await run_in_threadpool(
                compress_page, content, max_width=settings.scan_page_max_width,
                quality=PAGE_JPEG_QUALITY)]
    except InvalidScanImage as e:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    del content
    storage = get_storage()
    first = len(sub.pages) + 1
    for offset, (data, extension) in enumerate(pages):
        key = f"applicant/{sub.id}/{secrets.token_hex(8)}.{extension}"
        storage.put(key, data, IMAGE_MEDIA_TYPES[extension])
        db.add(ApplicantPage(submission_id=sub.id, sequence=first + offset, storage_key=key,
                             sha256=digest))
    audit.record(db, "applicant_pages_uploaded", user_id=user.id, entity_type=ENTITY,
                 entity_id=sub.id, new_value={"kind": kind, "pages_added": len(pages),
                                              "pages_total": first - 1 + len(pages)},
                 ip_address=client_ip(request))
    db.commit()
    db.refresh(sub)
    return _own_out(db, sub)


@router.get("/applicant/submissions/{submission_id}/pages/{number}")
def my_page(submission_id: int, number: int, user: User = Depends(applicant),
            db: Session = Depends(get_db)):
    return _page_response(_own(db, user, submission_id), number)


@router.delete("/applicant/submissions/{submission_id}/pages/{number}")
def delete_page(submission_id: int, number: int, request: Request,
                user: User = Depends(applicant), db: Session = Depends(get_db)):
    """Remove a page from a draft; the pages after it move up."""
    sub = _own(db, user, submission_id)
    _require_draft(sub)
    page = next((p for p in sub.pages if p.sequence == number), None)
    if page is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Page not found")
    get_storage().delete(page.storage_key)
    db.delete(page)
    db.flush()
    for later in sorted((p for p in sub.pages if p.sequence > number), key=lambda p: p.sequence):
        later.sequence -= 1
        db.flush()  # one at a time: (submission_id, sequence) is unique
    audit.record(db, "applicant_page_removed", user_id=user.id, entity_type=ENTITY,
                 entity_id=sub.id, new_value={"page": number}, ip_address=client_ip(request))
    db.commit()
    db.refresh(sub)
    return _own_out(db, sub)


@router.post("/applicant/submissions/{submission_id}/submit")
def submit_submission(submission_id: int, request: Request, user: User = Depends(applicant),
                      db: Session = Depends(get_db)):
    sub = _own(db, user, submission_id)
    _require_draft(sub)
    if not sub.pages:
        raise _conflict("Upload at least one page first.")
    sub.status = Status.QUEUED
    sub.submitted_at = utcnow()
    audit.record(db, "applicant_submission_submitted", user_id=user.id, entity_type=ENTITY,
                 entity_id=sub.id, new_value={"pages": len(sub.pages)},
                 ip_address=client_ip(request))
    db.commit()
    return _own_out(db, sub)


@router.post("/applicant/submissions/{submission_id}/withdraw")
def withdraw_submission(submission_id: int, request: Request, user: User = Depends(applicant),
                        db: Session = Depends(get_db)):
    """Before it is read: the uploaded pages are deleted (they were never used)."""
    sub = _own(db, user, submission_id)
    if sub.status not in {Status.DRAFT, Status.QUEUED}:
        raise _conflict("This submission is being read or was already read: contact the "
                        "registrar.")
    old = sub.status
    storage = get_storage()
    for page in list(sub.pages):
        storage.delete(page.storage_key)
        db.delete(page)
    sub.status = Status.WITHDRAWN
    audit.record(db, "applicant_submission_withdrawn", user_id=user.id, entity_type=ENTITY,
                 entity_id=sub.id, old_value={"status": old.value},
                 new_value={"status": sub.status.value}, ip_address=client_ip(request))
    db.commit()
    db.refresh(sub)
    return _own_out(db, sub)


# ---- station: the OCR worker (X-Station-Key) --------------------------------------------------

def _give_up_expired(db: Session, sub: ApplicantSubmission, max_attempts: int) -> None:
    """A lease ran out with no attempts left: the worker stopped on it every time."""
    sub.status = Status.FAILED
    sub.error = "The OCR worker stopped while reading this submission."
    sub.lease_token_hash = sub.lease_expires_at = sub.lease_station_id = None
    audit.record(db, "applicant_submission_failed", entity_type=ENTITY, entity_id=sub.id,
                 new_value={"attempts": sub.attempts, "max_attempts": max_attempts,
                            "error": sub.error})


@router.post("/station/applicant-jobs/claim")
def claim_job(station: ScanStation = Depends(get_station), db: Session = Depends(get_db)):
    """The oldest submission waiting to be read (or whose lease ran out). {"job": null} when
    there is nothing to do."""
    settings = get_settings()
    now = utcnow()
    station.last_seen_at = now
    candidates = list(db.scalars(select(ApplicantSubmission).where(
        ApplicantSubmission.status.in_([Status.QUEUED, Status.PROCESSING]))
        .order_by(ApplicantSubmission.submitted_at, ApplicantSubmission.id).limit(20)))
    for sub in candidates:
        if not rules.claimable(sub.status, sub.lease_expires_at, now):
            continue
        if sub.status == Status.PROCESSING and sub.attempts >= settings.applicant_max_attempts:
            _give_up_expired(db, sub, settings.applicant_max_attempts)
            continue
        token, token_hash, expires = rules.new_lease(now, settings.applicant_lease_minutes)
        # Compare-and-set: another worker may have claimed it a moment ago.
        claimed = db.execute(
            update(ApplicantSubmission)
            .where(ApplicantSubmission.id == sub.id, ApplicantSubmission.status == sub.status,
                   ApplicantSubmission.attempts == sub.attempts)
            .values(status=Status.PROCESSING, attempts=sub.attempts + 1,
                    lease_token_hash=token_hash, lease_expires_at=expires,
                    lease_station_id=station.id)).rowcount
        if not claimed:
            continue
        db.refresh(sub)
        audit.record(db, "applicant_submission_claimed", entity_type=ENTITY, entity_id=sub.id,
                     new_value={"station": station.name, "attempt": sub.attempts})
        db.commit()
        return {"job": {"submission_id": sub.id, "lease_token": token,
                        "lease_expires_at": expires, "declared_type": sub.declared_type,
                        "pages": len(sub.pages), "attempt": sub.attempts}}
    db.commit()
    return {"job": None}


def _leased(db: Session, station: ScanStation, submission_id: int,
            token: str) -> ApplicantSubmission:
    sub = db.get(ApplicantSubmission, submission_id)
    if (sub is None or sub.status != Status.PROCESSING or sub.lease_station_id != station.id
            or not rules.lease_held(sub.lease_token_hash, sub.lease_expires_at, token,
                                    utcnow())):
        # Expired, taken over by another worker, or never claimed: the result is not used.
        raise _conflict("This job's lease is not held (expired or claimed again).")
    return sub


@router.get("/station/applicant-jobs/{submission_id}/pages/{number}")
def job_page(submission_id: int, number: int,
             lease_token: str = Header(..., alias="X-Lease-Token", max_length=100),
             station: ScanStation = Depends(get_station), db: Session = Depends(get_db)):
    return _page_response(_leased(db, station, submission_id, lease_token), number)


class CompleteIn(BaseModel):
    lease_token: str = Field(max_length=100)
    reading: dict | None = None  # services/applicant_intake.parse_reading
    error: str | None = Field(default=None, max_length=2000)  # the worker could not read it


@router.post("/station/applicant-jobs/{submission_id}/complete")
def complete_job(submission_id: int, body: CompleteIn,
                 station: ScanStation = Depends(get_station), db: Session = Depends(get_db)):
    settings = get_settings()
    sub = _leased(db, station, submission_id, body.lease_token)
    if body.reading is not None:  # validate before changing anything: the lease stays held
        if len(json.dumps(body.reading)) > MAX_READING_CHARS:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "reading is too large")
        try:
            decision, reasons, reading = rules.decide(body.reading, sub.declared_type,
                                                      load_thresholds(db))
        except rules.InvalidReading as e:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    sub.lease_token_hash = sub.lease_expires_at = None
    station.last_seen_at = utcnow()

    if body.reading is None:
        sub.error = rules.clean_error(body.error)
        sub.status = rules.after_failure(sub.attempts, settings.applicant_max_attempts)
        audit.record(db, "applicant_submission_failed" if sub.status == Status.FAILED
                     else "applicant_submission_retry", entity_type=ENTITY, entity_id=sub.id,
                     new_value={"station": station.name, "attempt": sub.attempts,
                                "error": sub.error})
        db.commit()
        return {"submission_id": sub.id, "status": sub.status}

    doc = IntakeDocument(
        station_id=station.id, source=IntakeSource.ONLINE, submission_id=sub.id,
        doc_type=rules.document_type(reading, sub.declared_type),
        doc_type_confidence=reading.confidence,
        screening_result={**body.reading, "declared_type": sub.declared_type.value,
                          "issuing_school": sub.issuing_school},
        decision=decision, reasons=reasons, assigned_tray=screening.tray_for(decision),
        routing_jam=False, status=IntakeStatus.SCREENED,
        image_key=sub.pages[0].storage_key if sub.pages else None,
    )
    db.add(doc)
    db.flush()
    sub.intake_document_id = doc.id
    sub.status = Status.SCREENED
    sub.lease_station_id = None
    sub.error = None
    # Types and reason codes only: no field values or document text in the audit log.
    audit.record(db, "intake_document_screened", entity_type="intake_document", entity_id=doc.id,
                 new_value={"source": IntakeSource.ONLINE.value, "submission_id": sub.id,
                            "station": station.name, "doc_type": doc.doc_type.value,
                            "declared_type": sub.declared_type.value,
                            "decision": decision.value, "reasons": reasons})
    db.commit()
    return {"submission_id": sub.id, "status": sub.status, "intake_id": doc.id,
            "decision": decision, "reasons": reasons}
