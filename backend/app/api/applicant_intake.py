"""Applicant intake (docs/applicant-intake.md): documents from another school, uploaded online.

- Registrar/admin create applicant logins (no public sign-up), watch the submissions, and may
  create and upload a submission on an existing applicant's behalf (audited, `uploaded_by`).
- An applicant uploads the pages of a TOR or COE (one request per page or PDF, under the
  Vercel proxy's body limit) and submits them. Applicants only ever see their own submissions;
  a student who came in as an applicant keeps a read-only list of them (plain status only).
- The OCR worker on the station (X-Station-Key) claims jobs with a lease: the first reading
  of a submission, a reference sample (api/applicant_references.py) and the second OCR pass of
  an accepted document. It extracts and measures only: services/applicant_intake decides,
  comparing the upload with the school's approved reference sample, and the backend creates an
  intake document (source "online") that joins the staff review queue. From there the existing
  review, field review and student link apply (api/intake.py, api/intake_profiles.py).
  Wording: "passed screening" / "needs manual review".
- Staff export a per-submission report (JSON, CSV, PDF) with both passes and every finding.

Audit entries never carry document text or field values.
"""

import hashlib
import json
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from fastapi import status as http
from pydantic import BaseModel, EmailStr, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import applicant_files as files
from app.api.applicant_references import approved_refs, claim_reference
from app.api.deps import client_ip, get_or_404, get_station, require_roles
from app.api.intake import load_thresholds
from app.config import get_settings
from app.db import get_db
from app.documents.pdf import RendererUnavailable, render_pdf
from app.models import (
    ApplicantPage,
    ApplicantSecondPass,
    ApplicantSubmission,
    IntakeDocument,
    ScanStation,
    User,
)
from app.models.enums import (
    ApplicantSubmissionStatus,
    IntakeDocType,
    IntakeSource,
    IntakeStatus,
    Role,
    SecondPassStatus,
)
from app.services import applicant_intake as rules
from app.services import applicant_jobs as jobs
from app.services import applicant_references as refs
from app.services import applicant_report, audit
from app.services import intake as screening
from app.services.accounts import create_account
from app.storage import get_storage
from app.timeutil import utcnow

router = APIRouter(tags=["applicant intake"])
staff = require_roles(Role.REGISTRAR, Role.ADMIN)
applicant = require_roles(Role.APPLICANT)
student_only = require_roles(Role.STUDENT)
ENTITY = "applicant_submission"
Status = ApplicantSubmissionStatus
OPEN_LIMIT = 5  # unfinished submissions (draft/queued/processing) per applicant
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


def status_text(db: Session, sub: ApplicantSubmission) -> str:
    if sub.status == Status.SCREENED and sub.intake_document_id:
        doc = db.get(IntakeDocument, sub.intake_document_id)
        if doc is not None:
            return REVIEW_STATUS[doc.status]
    return APPLICANT_STATUS.get(sub.status, sub.status.value)


def _own_out(db: Session, sub: ApplicantSubmission) -> dict:
    """The applicant's view: no lease, error or staff details."""
    return {"id": sub.id, "declared_type": sub.declared_type,
            "issuing_school": sub.issuing_school, "status": sub.status,
            "status_text": status_text(db, sub), "pages": len(sub.pages),
            "created_at": sub.created_at, "submitted_at": sub.submitted_at}


def _staff_out(db: Session, sub: ApplicantSubmission) -> dict:
    user = db.get(User, sub.applicant_id)
    uploader = db.get(User, sub.uploaded_by) if sub.uploaded_by else None
    second = sub.second_pass
    return {**_own_out(db, sub), "applicant_id": sub.applicant_id,
            "applicant_email": user.email if user else None,
            "uploaded_by": uploader.email if uploader else None,
            "attempts": sub.attempts, "error": sub.error,
            "intake_document_id": sub.intake_document_id,
            "lease_expires_at": sub.lease_expires_at,
            "second_pass_status": second.status if second else None,
            "second_pass_disagreements": len(second.disagreements or []) if second else 0}


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
    return files.stream(page.storage_key if page else None)


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
    """Queue a reading that failed again (e.g. after the worker was fixed): the first reading
    of a failed submission, or the second pass of an accepted one."""
    sub = get_or_404(db, ApplicantSubmission, submission_id)
    second = sub.second_pass
    if sub.status == Status.FAILED:
        sub.status = Status.QUEUED
        sub.attempts = 0
        sub.error = None
        action, old, new = "applicant_submission_requeued", Status.FAILED, sub.status
    elif second is not None and second.status == SecondPassStatus.FAILED:
        second.status = SecondPassStatus.QUEUED
        second.attempts = 0
        second.error = None
        action, old, new = "applicant_second_pass_requeued", SecondPassStatus.FAILED, \
            second.status
    else:
        raise _conflict("Only a submission (or second reading) that could not be read can be "
                        "queued again.")
    audit.record(db, action, user_id=actor.id, entity_type=ENTITY, entity_id=sub.id,
                 old_value={"status": old.value},
                 new_value={"status": new.value, "reason": body.reason.strip()},
                 ip_address=client_ip(request))
    db.commit()
    return _staff_out(db, sub)


@router.get("/applicant-submissions/{submission_id}/report")
def submission_report(submission_id: int, request: Request,
                      format: Literal["json", "csv", "pdf"] = "json",
                      actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Both OCR passes, the findings, reference and model versions, attempts and the review
    decisions of one submission. Staff only: it contains what was read."""
    sub = get_or_404(db, ApplicantSubmission, submission_id)
    data = applicant_report.build(db, sub)
    name = f"applicant-submission-{sub.id}"
    if format == "pdf":
        try:
            pdf = render_pdf(applicant_report.as_html(data), f"Applicant submission {sub.id}",
                             utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        except RendererUnavailable as e:
            raise HTTPException(http.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
        content, media = pdf.content, "application/pdf"
    elif format == "csv":
        content, media = applicant_report.as_csv(data), "text/csv"
    else:
        content = json.dumps(applicant_report.as_json(data), ensure_ascii=False, indent=2)
        media = "application/json"
    audit.record(db, "applicant_submission_report_exported", user_id=actor.id,
                 entity_type=ENTITY, entity_id=sub.id, new_value={"format": format},
                 ip_address=client_ip(request))
    db.commit()
    return Response(content, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}.{format}"'})


# ---- submissions: shared by the applicant and by staff on the applicant's behalf -------------

School = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class SubmissionIn(BaseModel):
    declared_type: IntakeDocType
    issuing_school: School
    # The applicant read the privacy notice and agrees to the processing (Data Privacy Act).
    consent: bool


def _start(db: Session, owner: User, body: SubmissionIn, actor: User,
           ip: str | None) -> ApplicantSubmission:
    if body.declared_type not in rules.ONLINE_TYPES:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Choose a Transcript of Records or a Certificate of Enrollment")
    if not body.consent:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Agree to the privacy notice to send documents")
    open_count = db.scalar(select(func.count()).select_from(ApplicantSubmission).where(
        ApplicantSubmission.applicant_id == owner.id,
        ApplicantSubmission.status.in_([Status.DRAFT, Status.QUEUED, Status.PROCESSING])))
    if open_count >= OPEN_LIMIT:
        raise _conflict(f"There are {OPEN_LIMIT} unfinished submissions: submit or withdraw one "
                        "first.")
    now = utcnow()
    on_behalf = actor.id != owner.id
    sub = ApplicantSubmission(applicant_id=owner.id, declared_type=body.declared_type,
                              issuing_school=body.issuing_school, status=Status.DRAFT,
                              consent_at=now, attempts=0,
                              uploaded_by=actor.id if on_behalf else None)
    db.add(sub)
    db.flush()
    audit.record(db, "applicant_submission_started", user_id=actor.id, entity_type=ENTITY,
                 entity_id=sub.id,
                 new_value={"declared_type": sub.declared_type.value,
                            "consent_at": now.isoformat(),
                            **({"on_behalf_of": owner.id} if on_behalf else {})},
                 ip_address=ip)
    db.commit()
    return sub


def _require_draft(sub: ApplicantSubmission) -> None:
    if sub.status != Status.DRAFT:
        raise _conflict("This submission was already submitted.")


async def _upload(db: Session, sub: ApplicantSubmission, file: UploadFile, actor: User,
                  ip: str | None) -> None:
    """One page image (PNG/JPEG) or a PDF (split into its pages), appended in order."""
    _require_draft(sub)
    [(content, kind)] = await files.read_upload([file])
    digest = hashlib.sha256(content).hexdigest()
    if any(p.sha256 == digest for p in sub.pages):
        raise _conflict("This file was already uploaded to this submission.")
    settings = get_settings()
    room = settings.applicant_max_pages - len(sub.pages)
    if room <= 0:
        raise _conflict(f"A submission has at most {settings.applicant_max_pages} pages.")
    pages = await files.to_pages(content, kind, room)
    del content
    first = len(sub.pages) + 1
    for offset, (data, extension) in enumerate(pages):
        key = files.store(f"applicant/{sub.id}", secrets.token_hex(8), data, extension)
        db.add(ApplicantPage(submission_id=sub.id, sequence=first + offset, storage_key=key,
                             sha256=digest))
    on_behalf = actor.id != sub.applicant_id
    audit.record(db, "applicant_pages_uploaded", user_id=actor.id, entity_type=ENTITY,
                 entity_id=sub.id, new_value={"kind": kind, "pages_added": len(pages),
                                              "pages_total": first - 1 + len(pages),
                                              **({"on_behalf": True} if on_behalf else {})},
                 ip_address=ip)
    db.commit()
    db.refresh(sub)


def _delete_page(db: Session, sub: ApplicantSubmission, number: int, actor: User,
                 ip: str | None) -> None:
    """Remove a page from a draft; the pages after it move up."""
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
    audit.record(db, "applicant_page_removed", user_id=actor.id, entity_type=ENTITY,
                 entity_id=sub.id, new_value={"page": number}, ip_address=ip)
    db.commit()
    db.refresh(sub)


def _submit(db: Session, sub: ApplicantSubmission, actor: User, ip: str | None) -> None:
    _require_draft(sub)
    if not sub.pages:
        raise _conflict("Upload at least one page first.")
    sub.status = Status.QUEUED
    sub.submitted_at = utcnow()
    audit.record(db, "applicant_submission_submitted", user_id=actor.id, entity_type=ENTITY,
                 entity_id=sub.id, new_value={"pages": len(sub.pages)}, ip_address=ip)
    db.commit()


# ---- staff: upload on an applicant's behalf ---------------------------------------------------

class StaffSubmissionIn(SubmissionIn):
    applicant_id: int
    # Here `consent` means: the applicant agreed to the privacy notice (e.g. on paper).


def _applicant_login(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.role != Role.APPLICANT or not user.is_active:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No active applicant login with this id")
    return user


def _staff_draft(db: Session, submission_id: int) -> ApplicantSubmission:
    return get_or_404(db, ApplicantSubmission, submission_id)


@router.post("/applicant-submissions", status_code=http.HTTP_201_CREATED)
def staff_start_submission(body: StaffSubmissionIn, request: Request,
                           actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Start a submission for an existing applicant login (e.g. documents brought in person)."""
    owner = _applicant_login(db, body.applicant_id)
    return _staff_out(db, _start(db, owner, body, actor, client_ip(request)))


@router.post("/applicant-submissions/{submission_id}/pages",
             status_code=http.HTTP_201_CREATED)
async def staff_upload_pages(submission_id: int, request: Request,
                             file: UploadFile = File(...), actor: User = Depends(staff),
                             db: Session = Depends(get_db)):
    sub = _staff_draft(db, submission_id)
    await _upload(db, sub, file, actor, client_ip(request))
    return _staff_out(db, sub)


@router.delete("/applicant-submissions/{submission_id}/pages/{number}")
def staff_delete_page(submission_id: int, number: int, request: Request,
                      actor: User = Depends(staff), db: Session = Depends(get_db)):
    sub = _staff_draft(db, submission_id)
    _delete_page(db, sub, number, actor, client_ip(request))
    return _staff_out(db, sub)


@router.post("/applicant-submissions/{submission_id}/submit")
def staff_submit(submission_id: int, request: Request, actor: User = Depends(staff),
                 db: Session = Depends(get_db)):
    sub = _staff_draft(db, submission_id)
    _submit(db, sub, actor, client_ip(request))
    return _staff_out(db, sub)


# ---- applicant: own submissions ---------------------------------------------------------------

def _own(db: Session, user: User, submission_id: int) -> ApplicantSubmission:
    sub = db.get(ApplicantSubmission, submission_id)
    if sub is None or sub.applicant_id != user.id:  # someone else's: not found
        raise HTTPException(http.HTTP_404_NOT_FOUND, "Submission not found")
    return sub


@router.get("/applicant/submissions")
def my_submissions(user: User = Depends(applicant), db: Session = Depends(get_db)):
    subs = db.scalars(select(ApplicantSubmission).where(ApplicantSubmission.applicant_id == user.id)
                      .order_by(ApplicantSubmission.id.desc()))
    return [_own_out(db, s) for s in subs]


@router.post("/applicant/submissions", status_code=http.HTTP_201_CREATED)
def start_submission(body: SubmissionIn, request: Request, user: User = Depends(applicant),
                     db: Session = Depends(get_db)):
    return _own_out(db, _start(db, user, body, user, client_ip(request)))


@router.get("/applicant/submissions/{submission_id}")
def my_submission(submission_id: int, user: User = Depends(applicant),
                  db: Session = Depends(get_db)):
    return _own_out(db, _own(db, user, submission_id))


@router.post("/applicant/submissions/{submission_id}/pages", status_code=http.HTTP_201_CREATED)
async def upload_pages(submission_id: int, request: Request, file: UploadFile = File(...),
                       user: User = Depends(applicant), db: Session = Depends(get_db)):
    sub = _own(db, user, submission_id)
    await _upload(db, sub, file, user, client_ip(request))
    return _own_out(db, sub)


@router.get("/applicant/submissions/{submission_id}/pages/{number}")
def my_page(submission_id: int, number: int, user: User = Depends(applicant),
            db: Session = Depends(get_db)):
    return _page_response(_own(db, user, submission_id), number)


@router.delete("/applicant/submissions/{submission_id}/pages/{number}")
def delete_page(submission_id: int, number: int, request: Request,
                user: User = Depends(applicant), db: Session = Depends(get_db)):
    sub = _own(db, user, submission_id)
    _delete_page(db, sub, number, user, client_ip(request))
    return _own_out(db, sub)


@router.post("/applicant/submissions/{submission_id}/submit")
def submit_submission(submission_id: int, request: Request, user: User = Depends(applicant),
                      db: Session = Depends(get_db)):
    sub = _own(db, user, submission_id)
    _submit(db, sub, user, client_ip(request))
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


# ---- student: application history (read-only) -------------------------------------------------

@router.get("/students/me/applications")
def my_applications(user: User = Depends(student_only), db: Session = Depends(get_db)):
    """A student who came in as an applicant: their past submissions, plain status only (no
    pages, reasons, readings or staff notes)."""
    subs = db.scalars(select(ApplicantSubmission).where(
        ApplicantSubmission.applicant_id == user.id,
        ApplicantSubmission.status != Status.DRAFT)
        .order_by(ApplicantSubmission.id.desc()))
    return [{"id": s.id, "declared_type": s.declared_type, "issuing_school": s.issuing_school,
             "status_text": status_text(db, s), "submitted_at": s.submitted_at}
            for s in subs]


# ---- station: the OCR worker (X-Station-Key) --------------------------------------------------

def _give_up_expired(db: Session, sub: ApplicantSubmission, max_attempts: int) -> None:
    """A lease ran out with no attempts left: the worker stopped on it every time."""
    sub.status = Status.FAILED
    sub.error = "The OCR worker stopped while reading this submission."
    sub.lease_token_hash = sub.lease_expires_at = sub.lease_station_id = None
    audit.record(db, "applicant_submission_failed", entity_type=ENTITY, entity_id=sub.id,
                 new_value={"attempts": sub.attempts, "max_attempts": max_attempts,
                            "error": sub.error})


def _field_labels(approved: list[refs.RefInfo], sub: ApplicantSubmission) -> dict:
    """Printed label aliases of the school's reference, so the worker finds its fields."""
    ref = refs.pick_reference(approved, sub.issuing_school, sub.declared_type.value)
    return ref.rules["field_labels"] if ref else {}


def _claim_first(db: Session, station: ScanStation, approved: list[refs.RefInfo],
                 corpus: list[dict]) -> dict | None:
    settings = get_settings()
    now = utcnow()
    candidates = list(db.scalars(select(ApplicantSubmission).where(
        ApplicantSubmission.status.in_([Status.QUEUED, Status.PROCESSING]))
        .order_by(ApplicantSubmission.submitted_at, ApplicantSubmission.id).limit(20)))
    for sub in candidates:
        if not rules.claimable(sub.status, sub.lease_expires_at, now):
            continue
        if sub.status == Status.PROCESSING and sub.attempts >= settings.applicant_max_attempts:
            _give_up_expired(db, sub, settings.applicant_max_attempts)
            continue
        token = jobs.try_claim(db, sub, station, now, settings.applicant_lease_minutes)
        if token is None:  # another worker claimed it a moment ago
            continue
        audit.record(db, "applicant_submission_claimed", entity_type=ENTITY, entity_id=sub.id,
                     new_value={"station": station.name, "attempt": sub.attempts})
        return {"kind": jobs.KIND_FIRST, "submission_id": sub.id, "lease_token": token,
                "lease_expires_at": sub.lease_expires_at, "declared_type": sub.declared_type,
                "issuing_school": sub.issuing_school, "pages": len(sub.pages),
                "attempt": sub.attempts, "field_labels": _field_labels(approved, sub),
                "classifier": corpus}
    return None


def _give_up_second(db: Session, second: ApplicantSecondPass, max_attempts: int) -> None:
    second.status = SecondPassStatus.FAILED
    second.error = "The OCR worker stopped while reading this submission again."
    jobs.end_lease(second)
    second.lease_station_id = None
    audit.record(db, "applicant_second_pass_failed", entity_type=ENTITY,
                 entity_id=second.submission_id,
                 new_value={"attempts": second.attempts, "max_attempts": max_attempts,
                            "error": second.error})


def _claim_second(db: Session, station: ScanStation, approved: list[refs.RefInfo],
                  corpus: list[dict]) -> dict | None:
    settings = get_settings()
    now = utcnow()
    candidates = list(db.scalars(select(ApplicantSecondPass).where(
        ApplicantSecondPass.status.in_([SecondPassStatus.QUEUED, SecondPassStatus.PROCESSING]))
        .order_by(ApplicantSecondPass.id).limit(20)))
    for second in candidates:
        if not rules.claimable(second.status, second.lease_expires_at, now):
            continue
        if (second.status == SecondPassStatus.PROCESSING
                and second.attempts >= settings.applicant_max_attempts):
            _give_up_second(db, second, settings.applicant_max_attempts)
            continue
        token = jobs.try_claim(db, second, station, now, settings.applicant_lease_minutes)
        if token is None:
            continue
        sub = second.submission
        audit.record(db, "applicant_second_pass_claimed", entity_type=ENTITY, entity_id=sub.id,
                     new_value={"station": station.name, "attempt": second.attempts})
        return {"kind": jobs.KIND_SECOND, "submission_id": sub.id, "lease_token": token,
                "lease_expires_at": second.lease_expires_at, "declared_type": sub.declared_type,
                "issuing_school": sub.issuing_school, "pages": len(sub.pages),
                "attempt": second.attempts, "field_labels": _field_labels(approved, sub),
                "classifier": corpus}
    return None


@router.post("/station/applicant-jobs/claim")
def claim_job(kinds: str | None = None, station: ScanStation = Depends(get_station),
              db: Session = Depends(get_db)):
    """The next job of the kinds the worker runs (`kinds=first,second,reference`; without it,
    first readings only). {"job": null} when there is nothing to do. First readings go first
    (applicants are waiting), then second passes, then reference samples."""
    try:
        wanted = jobs.parse_kinds(kinds)
    except ValueError as e:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    station.last_seen_at = utcnow()
    approved = approved_refs(db)
    corpus = refs.classifier_corpus(approved)
    claimers = {jobs.KIND_FIRST: lambda: _claim_first(db, station, approved, corpus),
                jobs.KIND_SECOND: lambda: _claim_second(db, station, approved, corpus),
                jobs.KIND_REFERENCE: lambda: claim_reference(db, station, corpus)}
    for kind in wanted:
        job = claimers[kind]()
        if job is not None:
            db.commit()
            return {"job": job}
    db.commit()
    return {"job": None}


def _leased(db: Session, station: ScanStation, submission_id: int,
            token: str) -> ApplicantSubmission:
    sub = db.get(ApplicantSubmission, submission_id)
    if sub is None or not jobs.holds_lease(sub, station, token, utcnow()):
        # Expired, taken over by another worker, or never claimed: the result is not used.
        raise _conflict("This job's lease is not held (expired or claimed again).")
    return sub


def _second_leased(db: Session, station: ScanStation, submission_id: int,
                   token: str) -> ApplicantSecondPass:
    sub = db.get(ApplicantSubmission, submission_id)
    second = sub.second_pass if sub else None
    if second is None or not jobs.holds_lease(second, station, token, utcnow()):
        raise _conflict("This job's lease is not held (expired or claimed again).")
    return second


@router.get("/station/applicant-jobs/{submission_id}/pages/{number}")
def job_page(submission_id: int, number: int,
             lease_token: str = Header(..., alias="X-Lease-Token", max_length=100),
             station: ScanStation = Depends(get_station), db: Session = Depends(get_db)):
    """A page for the first reading or the second pass, whichever lease the token is."""
    sub = db.get(ApplicantSubmission, submission_id)
    now = utcnow()
    if sub is None or not (jobs.holds_lease(sub, station, lease_token, now)
                           or jobs.holds_lease(sub.second_pass, station, lease_token, now)):
        raise _conflict("This job's lease is not held (expired or claimed again).")
    return _page_response(sub, number)


class CompleteIn(BaseModel):
    lease_token: str = Field(max_length=100)
    reading: dict | None = None  # services/applicant_intake.parse_reading
    error: str | None = Field(default=None, max_length=2000)  # the worker could not read it


def _check_size(reading: dict) -> None:
    if len(json.dumps(reading)) > MAX_READING_CHARS:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "reading is too large")


@router.post("/station/applicant-jobs/{submission_id}/complete")
def complete_job(submission_id: int, body: CompleteIn,
                 station: ScanStation = Depends(get_station), db: Session = Depends(get_db)):
    settings = get_settings()
    sub = _leased(db, station, submission_id, body.lease_token)
    if body.reading is not None:  # validate before changing anything: the lease stays held
        _check_size(body.reading)
        try:
            outcome = rules.assess_upload(body.reading, sub.declared_type, sub.issuing_school,
                                          approved_refs(db), load_thresholds(db))
        except rules.InvalidReading as e:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    jobs.end_lease(sub)
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

    decision, reasons, reading = outcome.decision, outcome.reasons, outcome.reading
    assessment = outcome.assessment.as_json()
    doc = IntakeDocument(
        station_id=station.id, source=IntakeSource.ONLINE, submission_id=sub.id,
        doc_type=rules.document_type(reading, sub.declared_type),
        doc_type_confidence=reading.confidence,
        # The worker's reading as sent, plus what the backend knows and decided.
        screening_result={**body.reading, "declared_type": sub.declared_type.value,
                          "issuing_school": sub.issuing_school, "assessment": assessment},
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
    reference = assessment["reference"]
    model = assessment["layout_model"]
    # Types, versions and reason codes only: no field values or document text.
    audit.record(db, "intake_document_screened", entity_type="intake_document", entity_id=doc.id,
                 new_value={"source": IntakeSource.ONLINE.value, "submission_id": sub.id,
                            "station": station.name, "doc_type": doc.doc_type.value,
                            "declared_type": sub.declared_type.value,
                            "decision": decision.value, "reasons": reasons,
                            "reference": {"id": reference["id"], "version": reference["version"]}
                            if reference else None,
                            "classifier": (body.reading.get("doc_type") or {}).get("model"),
                            "layout_model": {"status": model.get("status"),
                                             "version": model.get("version")}})
    db.commit()
    return {"submission_id": sub.id, "status": sub.status, "intake_id": doc.id,
            "decision": decision, "reasons": reasons}


@router.post("/station/applicant-jobs/{submission_id}/second-pass")
def complete_second_pass(submission_id: int, body: CompleteIn,
                         station: ScanStation = Depends(get_station),
                         db: Session = Depends(get_db)):
    """The second OCR pass of an accepted document. Stored apart from the first pass; the
    backend lists the fields where the two disagree (names only in the audit log)."""
    settings = get_settings()
    second = _second_leased(db, station, submission_id, body.lease_token)
    if body.reading is not None:
        _check_size(body.reading)
        try:
            reading = rules.parse_reading(body.reading)
        except rules.InvalidReading as e:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    jobs.end_lease(second)
    station.last_seen_at = utcnow()
    if body.reading is None:
        second.error = rules.clean_error(body.error)
        second.status = rules.after_failure(second.attempts, settings.applicant_max_attempts,
                                            SecondPassStatus)
        audit.record(db, "applicant_second_pass_failed"
                     if second.status == SecondPassStatus.FAILED
                     else "applicant_second_pass_retry", entity_type=ENTITY,
                     entity_id=submission_id,
                     new_value={"station": station.name, "attempt": second.attempts,
                                "error": second.error})
        db.commit()
        return {"submission_id": submission_id, "status": second.status}
    doc = db.get(IntakeDocument, second.submission.intake_document_id)
    first_fields = ((doc.screening_result if doc else None) or {}).get("fields") or {}
    second.reading = body.reading
    second.disagreements = rules.compare_passes(first_fields, reading.fields)
    second.status = SecondPassStatus.DONE
    second.completed_at = utcnow()
    second.error = None
    second.lease_station_id = None
    audit.record(db, "applicant_second_pass_disagrees" if second.disagreements
                 else "applicant_second_pass_completed", entity_type=ENTITY,
                 entity_id=submission_id,
                 new_value={"station": station.name, "attempt": second.attempts,
                            "intake_id": doc.id if doc else None,
                            "disagreements": second.disagreements,
                            "engine": str(body.reading.get("engine") or "")[:100]})
    db.commit()
    return {"submission_id": submission_id, "status": second.status,
            "disagreements": second.disagreements}
