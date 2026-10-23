"""Leased OCR jobs of the applicant intake (docs/applicant-intake.md).

The station's OCR worker runs three kinds of job with one lease mechanism:

- "first": the first reading of a submitted upload (ApplicantSubmission);
- "reference": reading a reference sample (ApplicantReference);
- "second": the second OCR pass of an online document staff accepted (ApplicantSecondPass).

Each row carries status (queued/processing/...), attempts and a lease (token stored hashed,
station, expiry). A claim is a compare-and-set on (status, attempts), so two workers never get
the same job; a lease that runs out makes the job claimable again. The rules themselves are the
pure functions of services/applicant_intake.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import ApplicantSecondPass, ApplicantSubmission, IntakeDocument, ScanStation
from app.models.enums import IntakeSource, SecondPassStatus
from app.services import applicant_intake as rules
from app.services import audit

KIND_FIRST, KIND_SECOND, KIND_REFERENCE = "first", "second", "reference"
KINDS = (KIND_FIRST, KIND_SECOND, KIND_REFERENCE)  # claim order: applicants waiting come first


def parse_kinds(text: str | None) -> list[str]:
    """`kinds=first,second,reference` of the claim. A worker that doesn't send it (phase 1)
    only gets first readings. Raises ValueError for an unknown kind."""
    wanted = [k.strip() for k in (text or KIND_FIRST).split(",") if k.strip()]
    unknown = set(wanted) - set(KINDS)
    if unknown or not wanted:
        raise ValueError(f"kinds must be a comma-separated list of {', '.join(KINDS)}")
    return [k for k in KINDS if k in wanted]


def try_claim(db: Session, row: Any, station: ScanStation, now: datetime,
              lease_minutes: int) -> str | None:
    """Lease this job to the station. None when another worker claimed it a moment ago."""
    model = type(row)
    statuses = type(row.status)
    token, token_hash, expires = rules.new_lease(now, lease_minutes)
    claimed = db.execute(
        update(model)
        .where(model.id == row.id, model.status == row.status, model.attempts == row.attempts)
        .values(status=statuses("processing"), attempts=row.attempts + 1,
                lease_token_hash=token_hash, lease_expires_at=expires,
                lease_station_id=station.id)).rowcount
    if not claimed:
        return None
    db.refresh(row)
    return token


def holds_lease(row: Any, station: ScanStation, token: str, now: datetime) -> bool:
    """The station still holds this job's lease (same token, not expired, still processing)."""
    return (row is not None and row.status.value == "processing"
            and row.lease_station_id == station.id
            and rules.lease_held(row.lease_token_hash, row.lease_expires_at, token, now))


def end_lease(row: Any) -> None:
    row.lease_token_hash = row.lease_expires_at = None


def queue_second_pass(db: Session, doc: IntakeDocument, actor_id: int | None,
                      ip: str | None) -> ApplicantSecondPass | None:
    """Staff accepted an online document: read its pages again with independent preprocessing.
    Once per submission; station documents never get one."""
    if doc.source != IntakeSource.ONLINE or doc.submission_id is None:
        return None
    sub = db.get(ApplicantSubmission, doc.submission_id)
    if sub is None or sub.second_pass is not None:
        return None
    row = ApplicantSecondPass(submission_id=sub.id, status=SecondPassStatus.QUEUED, attempts=0,
                              queued_by=actor_id)
    db.add(row)
    db.flush()
    sub.second_pass = row
    audit.record(db, "applicant_second_pass_queued", user_id=actor_id,
                 entity_type="applicant_submission", entity_id=sub.id,
                 new_value={"intake_id": doc.id}, ip_address=ip)
    return row
