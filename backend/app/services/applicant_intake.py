"""Applicant intake decision and job rules (docs/applicant-intake.md): pure, no database, no I/O.

The OCR worker on the station reads an online submission (OCR, document type, fields) and sends
that reading; this module alone decides what it means. Like the station's screening
(services/intake.py) the outcome is "passed screening" or "needs manual review", never a
statement that a document is authentic.

There are no reference layouts for other schools' documents yet, so every online submission
needs manual review (NO_REFERENCE_PROFILE). The other rules still run so staff see why else a
person should look closely.
"""

import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.models.enums import ApplicantSubmissionStatus, IntakeDecision, IntakeDocType
from app.services import intake as screening
from app.timeutil import as_utc

READING_SCHEMA = 1
# Document types an applicant may upload (from another school).
ONLINE_TYPES = (IntakeDocType.TRANSCRIPT_OF_RECORDS, IntakeDocType.CERTIFICATE_OF_ENROLLMENT)
MAX_FIELDS = 40
MAX_FIELD_CHARS = 500
MAX_ERROR_CHARS = 500
FILE_MAGIC = {b"%PDF-": "pdf", b"\x89PNG": "png", b"\xff\xd8\xff": "jpg"}


class InvalidReading(ValueError):
    """The worker's reading is malformed (HTTP 422)."""


class InvalidUpload(ValueError):
    """The uploaded file is not a PDF, PNG or JPEG (HTTP 422)."""


def upload_kind(content: bytes) -> str:
    """'pdf', 'png' or 'jpg' from the file's first bytes (the declared type is not trusted)."""
    for magic, kind in FILE_MAGIC.items():
        if content.startswith(magic):
            return kind
    raise InvalidUpload("Upload a PDF, PNG or JPEG file")


@dataclass(frozen=True)
class Reading:
    quality_ok: bool
    predicted: str
    doc_type: IntakeDocType  # UNKNOWN for anything that is not an online type
    confidence: float
    fields: dict[str, dict]  # name -> {"value": str|None, "confidence": float|None}


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidReading(f"{where} must be a number")
    if not 0 <= value <= 1:
        raise InvalidReading(f"{where} must be between 0 and 1")
    return float(value)


def parse_reading(result: Any) -> Reading:
    """Validate what the worker sent. Missing or wrongly typed values are refused: a decision
    is never made on data that isn't there."""
    if not isinstance(result, dict):
        raise InvalidReading("reading must be a JSON object")
    schema = result.get("schema")
    if isinstance(schema, bool) or schema != READING_SCHEMA:
        raise InvalidReading(f"reading.schema must be {READING_SCHEMA}")
    quality = result.get("quality")
    if not isinstance(quality, dict) or not isinstance(quality.get("ok"), bool):
        raise InvalidReading("reading.quality.ok must be true or false")
    doc_type = result.get("doc_type")
    if not isinstance(doc_type, dict):
        raise InvalidReading("reading.doc_type must be an object")
    predicted = doc_type.get("predicted")
    if not isinstance(predicted, str) or not predicted.strip():
        raise InvalidReading("reading.doc_type.predicted must be a document type name")
    predicted = predicted.strip()
    confidence = _number(doc_type.get("confidence"), "reading.doc_type.confidence")

    raw_fields = result.get("fields") or {}
    if not isinstance(raw_fields, dict) or len(raw_fields) > MAX_FIELDS:
        raise InvalidReading(f"reading.fields must be an object with at most {MAX_FIELDS} fields")
    fields = {}
    for name, item in raw_fields.items():
        if not isinstance(item, dict):
            raise InvalidReading(f"reading.fields.{name} must be an object")
        value = item.get("value")
        if value is not None and (not isinstance(value, str) or len(value) > MAX_FIELD_CHARS):
            raise InvalidReading(f"reading.fields.{name}.value must be text "
                                 f"(at most {MAX_FIELD_CHARS} characters)")
        conf = item.get("confidence")
        fields[str(name)] = {"value": value,
                             "confidence": None if conf is None
                             else _number(conf, f"reading.fields.{name}.confidence")}

    known = {t.value for t in ONLINE_TYPES}
    return Reading(quality_ok=quality["ok"], predicted=predicted,
                   doc_type=IntakeDocType(predicted) if predicted in known
                   else IntakeDocType.UNKNOWN,
                   confidence=confidence, fields=fields)


def reasons_for(reading: Reading, declared: IntakeDocType,
                thresholds: screening.ThresholdSource = screening.DEFAULTS) -> list[str]:
    """Every rule that fires, in rule order."""
    reasons = [screening.NO_REFERENCE_PROFILE]  # until reference layouts per school exist
    if not reading.quality_ok:
        reasons.append(screening.UPLOAD_QUALITY_NOT_OK)
    if reading.doc_type == IntakeDocType.UNKNOWN:
        reasons.append(screening.DOC_TYPE_UNKNOWN)
    elif reading.doc_type != declared:
        reasons.append(screening.DECLARED_TYPE_MISMATCH)
    if reading.confidence < thresholds.min_doc_type_confidence:
        reasons.append(screening.DOC_TYPE_LOW_CONFIDENCE)
    return reasons


def decide(result: Any, declared: IntakeDocType,
           thresholds: screening.ThresholdSource | None = None
           ) -> tuple[IntakeDecision, list[str], Reading]:
    """(decision, reasons, reading). Raises InvalidReading."""
    reading = parse_reading(result)
    reasons = reasons_for(reading, declared, thresholds or screening.DEFAULTS)
    decision = IntakeDecision.MANUAL_REVIEW if reasons else IntakeDecision.PASSED_SCREENING
    return decision, reasons, reading


def document_type(reading: Reading, declared: IntakeDocType) -> IntakeDocType:
    """The type the intake document starts with: what was read when it is an online type,
    otherwise what the applicant chose. Staff can still correct it in the field review."""
    return reading.doc_type if reading.doc_type in ONLINE_TYPES else declared


# ---- OCR job lease ----------------------------------------------------------------------------

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_lease(now: datetime, minutes: int) -> tuple[str, str, datetime]:
    """(token for the worker, its hash to store, expiry)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token), now + timedelta(minutes=minutes)


def lease_held(stored_hash: str | None, expires_at: datetime | None, token: str,
               now: datetime) -> bool:
    """The worker still holds this lease: same token, not expired."""
    return (stored_hash is not None and expires_at is not None and now < as_utc(expires_at)
            and secrets.compare_digest(stored_hash, hash_token(token)))


def claimable(status: ApplicantSubmissionStatus, expires_at: datetime | None,
              now: datetime) -> bool:
    """Queued, or processing with a lease that ran out (the worker stopped)."""
    return status == ApplicantSubmissionStatus.QUEUED or (
        status == ApplicantSubmissionStatus.PROCESSING
        and (expires_at is None or as_utc(expires_at) <= now))


def after_failure(attempts: int, max_attempts: int) -> ApplicantSubmissionStatus:
    """A failed or abandoned read: try again until the attempts are used up."""
    return (ApplicantSubmissionStatus.FAILED if attempts >= max_attempts
            else ApplicantSubmissionStatus.QUEUED)


def clean_error(text: Any) -> str:
    """The worker's error message, shortened. It must not carry document text."""
    return str(text or "unknown error").strip()[:MAX_ERROR_CHARS] or "unknown error"
