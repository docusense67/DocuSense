"""Reference samples for the applicant intake (docs/applicant-intake.md, "References").

Registrar/admin upload labelled samples of an issuing school's TOR or COE: label known-good
sample / altered sample / other, use training / held-out. Each upload is a new version; the
station's OCR worker reads it (a "reference" job with the same lease as a submission) and staff
approve the reading, with a reason. Approved versions can't be edited: upload a new version and
retire the old one. A match with a reference sample is not proof of authenticity.

Audit entries carry labels, versions and reasons, never document text or field values.
"""

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi import status as http
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import applicant_files as files
from app.api.deps import client_ip, get_or_404, get_station, require_roles
from app.config import get_settings
from app.db import get_db
from app.models import ApplicantReference, ScanStation, User
from app.models.enums import (
    IntakeDocType,
    ReferenceLabel,
    ReferenceStatus,
    ReferenceUse,
    Role,
)
from app.services import applicant_intake as rules
from app.services import applicant_jobs as jobs
from app.services import applicant_references as refs
from app.services import audit
from app.timeutil import utcnow

router = APIRouter(tags=["applicant intake"])
staff = require_roles(Role.REGISTRAR, Role.ADMIN)
ENTITY = "applicant_reference"
Status = ReferenceStatus
EDITABLE = {Status.QUEUED, Status.PROCESSING, Status.PENDING, Status.FAILED}
MAX_READING_CHARS = 512_000
NOTE = ("Reference samples are the school's own labelled examples. A match with a reference "
        "sample is not proof of authenticity: it only means no automatic check flagged the "
        "upload, and staff make the decision.")


def _conflict(message: str) -> HTTPException:
    return HTTPException(http.HTTP_409_CONFLICT, message)


def approved_refs(db: Session) -> list[refs.RefInfo]:
    """Every approved reference, as the assessment needs it."""
    rows = db.scalars(select(ApplicantReference).where(
        ApplicantReference.status == Status.APPROVED).order_by(ApplicantReference.id))
    return [refs.ref_info(r) for r in rows]


def _email(db: Session, user_id: int | None) -> str | None:
    user = db.get(User, user_id) if user_id else None
    return user.email if user else None


def _out(db: Session, ref: ApplicantReference, *, detail: bool = False) -> dict:
    reading = ref.reading or {}
    layout = reading.get("layout") if isinstance(reading.get("layout"), dict) else None
    out = {
        "id": ref.id, "issuing_school": ref.issuing_school, "school_key": ref.school_key,
        "doc_type": ref.doc_type, "label": ref.label, "use": ref.use, "version": ref.version,
        "replaces_id": ref.replaces_id, "status": ref.status, "status_reason": ref.status_reason,
        "rules": refs.parse_rules(ref.rules) if ref.rules is not None else None,
        "pages": len(ref.page_keys or []), "attempts": ref.attempts, "error": ref.error,
        "created_by": _email(db, ref.created_by), "created_at": ref.created_at,
        "decided_by": _email(db, ref.decided_by), "decided_at": ref.decided_at,
        "read": ref.reading is not None,
        "measured_pages": len(layout.get("pages") or []) if layout else 0,
    }
    if detail:
        out["reading"] = reading or None
        out["note"] = NOTE
    return out


def _lookup(db: Session, reference_id: int) -> ApplicantReference:
    return get_or_404(db, ApplicantReference, reference_id)


def _check_type(doc_type: IntakeDocType, label: ReferenceLabel) -> None:
    if label == ReferenceLabel.OTHER:
        if doc_type != IntakeDocType.UNKNOWN:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                                "A sample labelled 'other' is not a TOR or COE: choose the "
                                "type 'unknown'")
    elif doc_type not in rules.ONLINE_TYPES:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                            "A known-good or altered sample must be a TOR or a COE")


def _rules(text: str | dict | None) -> dict:
    try:
        raw = json.loads(text) if isinstance(text, str) else text
        return refs.parse_rules(raw)
    except (json.JSONDecodeError, RecursionError):
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "rules must be JSON") from None
    except refs.InvalidRules as e:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


# ---- staff ------------------------------------------------------------------------------------

@router.get("/applicant-references")
def list_references(state: ReferenceStatus | None = None, _: User = Depends(staff),
                    db: Session = Depends(get_db)):
    query = select(ApplicantReference)
    if state is not None:
        query = query.where(ApplicantReference.status == state)
    rows = db.scalars(query.order_by(ApplicantReference.school_key, ApplicantReference.doc_type,
                                     ApplicantReference.id.desc()).limit(500))
    return [_out(db, r) for r in rows]


School = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


@router.post("/applicant-references", status_code=http.HTTP_201_CREATED)
async def upload_reference(
    request: Request,
    issuing_school: School = Form(...),
    doc_type: IntakeDocType = Form(...),
    label: ReferenceLabel = Form(...),
    use: ReferenceUse = Form(ReferenceUse.TRAINING),
    rules_json: str = Form("{}", alias="rules", max_length=20_000),
    replaces_id: int | None = Form(None),
    file: list[UploadFile] = File(...),
    actor: User = Depends(staff),
    db: Session = Depends(get_db),
):
    """A new reference version: one request (under the upload limit) with a PDF or the page
    images in order. The worker reads it before staff can approve it."""
    _check_type(doc_type, label)
    parsed = _rules(rules_json)
    key = refs.school_key(issuing_school)
    if not key:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "Name the issuing school")
    if replaces_id is not None:
        old = db.get(ApplicantReference, replaces_id)
        if old is None or old.school_key != key or old.doc_type != doc_type:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT,
                                "replaces_id must be a reference of the same school and type")
    uploads = await files.read_upload(file)
    max_pages = get_settings().applicant_max_pages
    pages: list[tuple[bytes, str]] = []
    for content, kind in uploads:
        room = max_pages - len(pages)
        if room <= 0:
            raise _conflict(f"A reference has at most {max_pages} pages.")
        pages.extend(await files.to_pages(content, kind, room))
    del uploads
    version = 1 + (db.scalar(select(func.max(ApplicantReference.version)).where(
        ApplicantReference.school_key == key, ApplicantReference.doc_type == doc_type,
        ApplicantReference.label == label, ApplicantReference.use == use)) or 0)
    ref = ApplicantReference(issuing_school=issuing_school, school_key=key, doc_type=doc_type,
                             label=label, use=use, version=version, replaces_id=replaces_id,
                             status=Status.QUEUED, rules=parsed, page_keys=[], attempts=0,
                             created_by=actor.id)
    db.add(ref)
    db.flush()
    folder = f"applicant-references/{ref.id}"
    ref.page_keys = [files.store(folder, secrets.token_hex(8), data, ext) for data, ext in pages]
    audit.record(db, "applicant_reference_uploaded", user_id=actor.id, entity_type=ENTITY,
                 entity_id=ref.id,
                 new_value={"issuing_school": issuing_school, "doc_type": doc_type.value,
                            "label": label.value, "use": use.value, "version": version,
                            "replaces_id": replaces_id, "pages": len(pages),
                            "rules": parsed},
                 ip_address=client_ip(request))
    db.commit()
    return _out(db, ref, detail=True)


@router.get("/applicant-references/{reference_id}")
def get_reference(reference_id: int, _: User = Depends(staff), db: Session = Depends(get_db)):
    return _out(db, _lookup(db, reference_id), detail=True)


@router.get("/applicant-references/{reference_id}/pages/{number}")
def reference_page(reference_id: int, number: int, _: User = Depends(staff),
                   db: Session = Depends(get_db)):
    keys = _lookup(db, reference_id).page_keys or []
    return files.stream(keys[number - 1] if 1 <= number <= len(keys) else None)


class ReferenceEditIn(BaseModel):
    label: ReferenceLabel | None = None
    use: ReferenceUse | None = None
    rules: dict | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.patch("/applicant-references/{reference_id}")
def edit_reference(reference_id: int, body: ReferenceEditIn, request: Request,
                   actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Label, use or rules of a version that isn't approved yet. Approved versions can't be
    edited: upload a new version and retire the old one."""
    ref = _lookup(db, reference_id)
    if ref.status not in EDITABLE:
        raise _conflict("Approved or retired versions can't be edited: upload a new version "
                        "and retire this one.")
    label = body.label or ref.label
    _check_type(ref.doc_type, label)
    old = {"label": ref.label.value, "use": ref.use.value, "rules": ref.rules}
    ref.label = label
    ref.use = body.use or ref.use
    if body.rules is not None:
        ref.rules = _rules(body.rules)
    changed = sorted(k for k, v in {"label": ref.label.value, "use": ref.use.value,
                                    "rules": ref.rules}.items() if v != old[k])
    if changed:
        audit.record(db, "applicant_reference_edited", user_id=actor.id, entity_type=ENTITY,
                     entity_id=ref.id, old_value={k: old[k] for k in changed},
                     new_value={"label": ref.label.value, "use": ref.use.value,
                                "rules": ref.rules, "changed": changed,
                                "reason": (body.reason or "").strip() or None},
                     ip_address=client_ip(request))
    db.commit()
    return _out(db, ref, detail=True)


class DecisionIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _decide(db: Session, ref: ApplicantReference, new: ReferenceStatus, action: str,
            body: DecisionIn, actor: User, request: Request) -> dict:
    old = ref.status
    ref.status = new
    ref.status_reason = body.reason.strip()
    ref.decided_by = actor.id
    ref.decided_at = utcnow()
    audit.record(db, action, user_id=actor.id, entity_type=ENTITY, entity_id=ref.id,
                 old_value={"status": old.value},
                 new_value={"status": new.value, "reason": ref.status_reason,
                            "issuing_school": ref.issuing_school, "doc_type": ref.doc_type.value,
                            "label": ref.label.value, "use": ref.use.value,
                            "version": ref.version},
                 ip_address=client_ip(request))
    db.commit()
    return _out(db, ref, detail=True)


@router.post("/applicant-references/{reference_id}/approve")
def approve_reference(reference_id: int, body: DecisionIn, request: Request,
                      actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Staff checked the worker's reading of this sample; from now on it can be compared with
    (training) or evaluate the models (held-out)."""
    ref = _lookup(db, reference_id)
    if ref.status != Status.PENDING:
        raise _conflict("Only a reference that was read and waits for approval can be "
                        "approved.")
    return _decide(db, ref, Status.APPROVED, "applicant_reference_approved", body, actor,
                   request)


@router.post("/applicant-references/{reference_id}/retire")
def retire_reference(reference_id: int, body: DecisionIn, request: Request,
                     actor: User = Depends(staff), db: Session = Depends(get_db)):
    ref = _lookup(db, reference_id)
    if ref.status not in {Status.PENDING, Status.APPROVED, Status.FAILED}:
        raise _conflict("This reference is being read or was already retired.")
    return _decide(db, ref, Status.RETIRED, "applicant_reference_retired", body, actor, request)


@router.post("/applicant-references/{reference_id}/retry")
def retry_reference(reference_id: int, body: DecisionIn, request: Request,
                    actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Queue a reference the worker could not read again."""
    ref = _lookup(db, reference_id)
    if ref.status != Status.FAILED:
        raise _conflict("Only a reference that could not be read can be queued again.")
    ref.status = Status.QUEUED
    ref.attempts = 0
    ref.error = None
    audit.record(db, "applicant_reference_requeued", user_id=actor.id, entity_type=ENTITY,
                 entity_id=ref.id, old_value={"status": Status.FAILED.value},
                 new_value={"status": ref.status.value, "reason": body.reason.strip()},
                 ip_address=client_ip(request))
    db.commit()
    return _out(db, ref)


# ---- station: the OCR worker reads a reference ("reference" job) ------------------------------

def give_up_expired(db: Session, ref: ApplicantReference, max_attempts: int) -> None:
    ref.status = Status.FAILED
    ref.error = "The OCR worker stopped while reading this reference."
    jobs.end_lease(ref)
    ref.lease_station_id = None
    audit.record(db, "applicant_reference_failed", entity_type=ENTITY, entity_id=ref.id,
                 new_value={"attempts": ref.attempts, "max_attempts": max_attempts,
                            "error": ref.error})


def claim_reference(db: Session, station: ScanStation, corpus: list[dict]) -> dict | None:
    """The oldest reference waiting to be read, leased to this station (None: nothing)."""
    settings = get_settings()
    now = utcnow()
    candidates = db.scalars(select(ApplicantReference).where(
        ApplicantReference.status.in_([Status.QUEUED, Status.PROCESSING]))
        .order_by(ApplicantReference.id).limit(20))
    for ref in list(candidates):
        if not rules.claimable(ref.status, ref.lease_expires_at, now):
            continue
        if ref.status == Status.PROCESSING and ref.attempts >= settings.applicant_max_attempts:
            give_up_expired(db, ref, settings.applicant_max_attempts)
            continue
        token = jobs.try_claim(db, ref, station, now, settings.applicant_lease_minutes)
        if token is None:
            continue
        audit.record(db, "applicant_reference_claimed", entity_type=ENTITY, entity_id=ref.id,
                     new_value={"station": station.name, "attempt": ref.attempts})
        return {"kind": jobs.KIND_REFERENCE, "reference_id": ref.id, "lease_token": token,
                "lease_expires_at": ref.lease_expires_at, "pages": len(ref.page_keys or []),
                "attempt": ref.attempts, "doc_type": ref.doc_type,
                "issuing_school": ref.issuing_school,
                "field_labels": refs.parse_rules(ref.rules)["field_labels"],
                "classifier": corpus}
    return None


def _leased(db: Session, station: ScanStation, reference_id: int,
            token: str) -> ApplicantReference:
    ref = db.get(ApplicantReference, reference_id)
    if not jobs.holds_lease(ref, station, token, utcnow()):
        raise _conflict("This job's lease is not held (expired or claimed again).")
    return ref


@router.get("/station/applicant-references/{reference_id}/pages/{number}")
def reference_job_page(reference_id: int, number: int,
                       lease_token: str = Header(..., alias="X-Lease-Token", max_length=100),
                       station: ScanStation = Depends(get_station),
                       db: Session = Depends(get_db)):
    keys = _leased(db, station, reference_id, lease_token).page_keys or []
    return files.stream(keys[number - 1] if 1 <= number <= len(keys) else None)


class CompleteIn(BaseModel):
    lease_token: str = Field(max_length=100)
    reading: dict | None = None  # same schema as a submission's reading
    error: str | None = Field(default=None, max_length=2000)


@router.post("/station/applicant-references/{reference_id}/complete")
def complete_reference(reference_id: int, body: CompleteIn,
                       station: ScanStation = Depends(get_station),
                       db: Session = Depends(get_db)):
    settings = get_settings()
    ref = _leased(db, station, reference_id, body.lease_token)
    if body.reading is not None:  # validate first: a malformed reading keeps the lease
        if len(json.dumps(body.reading)) > MAX_READING_CHARS:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "reading is too large")
        try:
            reading = rules.parse_reading(body.reading)
        except rules.InvalidReading as e:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    jobs.end_lease(ref)
    station.last_seen_at = utcnow()
    if body.reading is None:
        ref.error = rules.clean_error(body.error)
        ref.status = rules.after_failure(ref.attempts, settings.applicant_max_attempts,
                                         ReferenceStatus)
        audit.record(db, "applicant_reference_failed" if ref.status == Status.FAILED
                     else "applicant_reference_retry", entity_type=ENTITY, entity_id=ref.id,
                     new_value={"station": station.name, "attempt": ref.attempts,
                                "error": ref.error})
        db.commit()
        return {"reference_id": ref.id, "status": ref.status}
    ref.reading = body.reading
    ref.status = Status.PENDING
    ref.error = None
    ref.lease_station_id = None
    # What was measured, never the text or field values.
    audit.record(db, "applicant_reference_read", entity_type=ENTITY, entity_id=ref.id,
                 new_value={"station": station.name, "attempt": ref.attempts,
                            "predicted": reading.predicted,
                            "fields_found": sorted(n for n, f in reading.fields.items()
                                                   if f.get("value")),
                            "layout_pages": len(reading.layout) if reading.layout else 0})
    db.commit()
    return {"reference_id": ref.id, "status": ref.status}
