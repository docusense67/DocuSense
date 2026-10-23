"""Incoming-document screening (Part I): station intake and the staff review queue.

Contract: docs/intake/screening-contract.md. Station routes authenticate with the station's
API key (X-Station-Key). The station measures; the backend decides (services/intake.decide)
and tells it which tray to use. Staff (registrar, admin) accept, reject or ask for a
resubmission; thresholds are admin-only. Wording: "passed screening" / "needs manual review".
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import client_ip, get_or_404, get_station, require_roles
from app.config import get_settings
from app.db import get_db
from app.models import (
    ApplicantSubmission,
    AuditLog,
    IntakeBatch,
    IntakeDocument,
    IntakeSettings,
    ScanStation,
    Student,
    User,
)
from app.models.enums import (
    IntakeDecision,
    IntakeDocType,
    IntakeReviewAction,
    IntakeSource,
    IntakeStatus,
    Role,
)
from app.services import audit
from app.services import intake as screening
from app.services.scan_images import InvalidScanImage, compress_page
from app.services.scanning import IMAGE_MEDIA_TYPES
from app.storage import StoredFileMissing, get_storage
from app.timeutil import utcnow

router = APIRouter(tags=["intake"])
staff = require_roles(Role.REGISTRAR, Role.ADMIN)
admin = require_roles(Role.ADMIN)

IMAGE_MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg"}
NOTE = ("Passed screening means no automatic check flagged the document. It does not mean the "
        "document is authentic; staff make the final decision.")
ENTITY = "intake_document"


def load_thresholds(db: Session) -> IntakeSettings | screening.Thresholds:
    """The admin's settings row, else the contract defaults."""
    return db.get(IntakeSettings, 1) or screening.DEFAULTS


def _thresholds_dict(source) -> dict:
    return {k: getattr(source, k) for k in ("min_doc_type_confidence", "max_indicator_severity",
                                            "min_layout_score", "min_similarity_score")}


# ---- station (API key) ----------------------------------------------------------------------

def _batch_summary(batch: IntakeBatch) -> dict:
    docs = batch.documents
    return {
        "batch_id": batch.id, "started_at": batch.started_at, "ended_at": batch.ended_at,
        "total": batch.total_docs, "passed_screening": batch.passed_count,
        "manual_review": batch.manual_review_count,
        "routed": sum(1 for d in docs if d.routed_at is not None),
        "unrouted": sum(1 for d in docs if d.routed_at is None),
        "routing_issues": sum(1 for d in docs if d.routing_jam or (
            d.routed_tray is not None and d.routed_tray != d.assigned_tray)),
    }


def _own_batch(db: Session, station: ScanStation, batch_id: int) -> IntakeBatch:
    batch = db.get(IntakeBatch, batch_id)
    if batch is None or batch.station_id != station.id:  # another station's batch: not found
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    return batch


@router.post("/station/intake/batches", status_code=status.HTTP_201_CREATED)
def start_batch(station: ScanStation = Depends(get_station), db: Session = Depends(get_db)):
    """A mixed-document stack (S-10). Any batch this station left open is ended first."""
    now = utcnow()
    left_open = list(db.scalars(select(IntakeBatch.id).where(
        IntakeBatch.station_id == station.id, IntakeBatch.ended_at.is_(None))))
    if left_open:
        db.execute(update(IntakeBatch).where(IntakeBatch.id.in_(left_open)).values(ended_at=now))
    batch = IntakeBatch(station_id=station.id, total_docs=0, passed_count=0,
                        manual_review_count=0)
    db.add(batch)
    db.flush()
    audit.record(db, "intake_batch_started", entity_type="intake_batch", entity_id=batch.id,
                 new_value={"station": station.name, "ended_open_batches": left_open})
    station.last_seen_at = now
    db.commit()
    return {"batch_id": batch.id}


@router.post("/station/intake/batches/{batch_id}/end")
def end_batch(batch_id: int, station: ScanStation = Depends(get_station),
              db: Session = Depends(get_db)):
    batch = _own_batch(db, station, batch_id)
    if batch.ended_at is None:  # ending twice is harmless (station retries)
        batch.ended_at = utcnow()
        summary = _batch_summary(batch)
        audit.record(db, "intake_batch_ended", entity_type="intake_batch", entity_id=batch.id,
                     new_value={"station": station.name,
                                **{k: summary[k] for k in ("total", "passed_screening",
                                                           "manual_review", "routing_issues")}})
        db.commit()
    return _batch_summary(batch)


@router.post("/station/intake/documents", status_code=status.HTTP_201_CREATED)
async def submit_intake_document(
    image: UploadFile = File(...),
    result: str = Form(...),
    batch_id: int | None = Form(None),
    sequence: int | None = Form(None, ge=0, le=100_000),
    station: ScanStation = Depends(get_station),
    db: Session = Depends(get_db),
):
    """One screened page: the backend decides passed_screening (Tray 1) / manual_review (2)."""
    try:
        data = screening.load_result(result)
        decision, tray, reasons = screening.decide(data, load_thresholds(db))
        parsed = screening.parse(data)
    except screening.InvalidScreeningResult as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None

    batch = None
    if batch_id is not None:
        batch = _own_batch(db, station, batch_id)
        if batch.ended_at is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Batch has ended")

    settings = get_settings()
    limit = settings.max_scan_image_bytes
    content = await image.read(limit + 1)
    if not any(content.startswith(m) for m in IMAGE_MAGIC) or len(content) > limit:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "image must be a JPEG or PNG under the size limit")
    try:  # CPU-bound: off the event loop
        stored, extension = await run_in_threadpool(
            compress_page, content, max_width=settings.scan_page_max_width,
            quality=settings.scan_page_jpeg_quality)
    except InvalidScanImage as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    del content

    doc = IntakeDocument(
        batch_id=batch.id if batch else None, station_id=station.id, sequence=sequence,
        doc_type=parsed.doc_type, doc_type_confidence=parsed.confidence,
        screening_result=data, decision=decision, reasons=reasons, assigned_tray=tray,
        routing_jam=False, status=IntakeStatus.SCREENED,
    )
    db.add(doc)
    db.flush()
    key = f"intake/{station.id}/{doc.id}.{extension}"
    get_storage().put(key, stored, IMAGE_MEDIA_TYPES[extension])
    doc.image_key = key
    if batch is not None:
        batch.total_docs += 1
        if decision == IntakeDecision.PASSED_SCREENING:
            batch.passed_count += 1
        else:
            batch.manual_review_count += 1
    # No extracted field values here: the audit log stays free of personal data.
    audit.record(db, "intake_document_screened", entity_type=ENTITY, entity_id=doc.id,
                 new_value={"station": station.name, "batch_id": doc.batch_id,
                            "sequence": sequence, "doc_type": parsed.doc_type.value,
                            "decision": decision.value, "tray": tray, "reasons": reasons,
                            "thresholds": _thresholds_dict(load_thresholds(db))})
    station.last_seen_at = utcnow()
    db.commit()
    return {"intake_id": doc.id, "decision": decision, "tray": tray, "reasons": reasons}


class RoutedIn(BaseModel):
    """What the gate actually did. `jam` (or no tray) = the page did not reach a tray."""

    tray: int | None = Field(default=None, ge=1, le=2)
    jam: bool = False
    detail: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _tray_or_jam(self) -> "RoutedIn":
        if self.tray is None and not self.jam:
            raise ValueError("tray (1 or 2) is required unless jam is true")
        return self


@router.post("/station/intake/documents/{intake_id}/routed")
def report_routed(intake_id: int, body: RoutedIn, station: ScanStation = Depends(get_station),
                  db: Session = Depends(get_db)):
    doc = db.get(IntakeDocument, intake_id)
    if doc is None or doc.station_id != station.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Intake document not found")
    jam = body.jam or body.tray is None
    if doc.routed_at is not None:
        if doc.routed_tray == body.tray and doc.routing_jam == jam:  # a retried report
            return _routed_out(doc)
        raise HTTPException(status.HTTP_409_CONFLICT, "Routing was already reported")

    old = {"decision": doc.decision.value, "reasons": list(doc.reasons)}
    doc.routed_tray = body.tray
    doc.routed_at = utcnow()
    doc.routing_jam = jam
    added = []
    if jam:
        added.append(screening.ROUTING_JAM)
    elif body.tray != doc.assigned_tray:
        added.append(screening.ROUTED_TRAY_MISMATCH)
    if added:
        # The physical page is not where the decision said: a person must look at it.
        if doc.decision == IntakeDecision.PASSED_SCREENING and doc.batch is not None:
            doc.batch.passed_count -= 1
            doc.batch.manual_review_count += 1
        doc.decision = IntakeDecision.MANUAL_REVIEW
        doc.reasons = [*doc.reasons, *[r for r in added if r not in doc.reasons]]
    audit.record(db, "intake_routing_issue" if added else "intake_document_routed",
                 entity_type=ENTITY, entity_id=doc.id, old_value=old if added else None,
                 new_value={"station": station.name, "assigned_tray": doc.assigned_tray,
                            "routed_tray": body.tray, "jam": jam, "detail": body.detail,
                            "decision": doc.decision.value, "reasons": doc.reasons})
    db.commit()
    return _routed_out(doc)


def _routed_out(doc: IntakeDocument) -> dict:
    return {"intake_id": doc.id, "decision": doc.decision, "assigned_tray": doc.assigned_tray,
            "routed_tray": doc.routed_tray, "jam": doc.routing_jam, "reasons": doc.reasons}


# ---- staff (registrar, admin) ---------------------------------------------------------------

def _row(doc: IntakeDocument) -> dict:
    return {
        "id": doc.id, "source": doc.source, "submission_id": doc.submission_id,
        "batch_id": doc.batch_id, "sequence": doc.sequence,
        "doc_type": doc.doc_type, "doc_type_confidence": doc.doc_type_confidence,
        "decision": doc.decision, "reasons": doc.reasons, "assigned_tray": doc.assigned_tray,
        "routed_tray": doc.routed_tray, "routing_jam": doc.routing_jam, "status": doc.status,
        "created_at": doc.created_at, "reviewed_at": doc.reviewed_at,
        "fields_reviewed_at": doc.fields_reviewed_at, "student_id": doc.student_id,
        "linked_at": doc.linked_at,
    }


@router.get("/intake/documents")
def intake_queue(
    state: IntakeStatus | None = Query(None, alias="status"),
    doc_type: IntakeDocType | None = None,
    batch_id: int | None = None,
    decision: IntakeDecision | None = None,
    student_id: int | None = None,
    source: IntakeSource | None = None,
    limit: int = 100,
    _: User = Depends(staff),
    db: Session = Depends(get_db),
):
    """Review queue, newest first. `student_id`: the documents linked to one student;
    `source`: paper from the station or online uploads (docs/applicant-intake.md)."""
    query = select(IntakeDocument)
    if source is not None:
        query = query.where(IntakeDocument.source == source)
    if student_id is not None:
        query = query.where(IntakeDocument.student_id == student_id)
    if state is not None:
        query = query.where(IntakeDocument.status == state)
    if doc_type is not None:
        query = query.where(IntakeDocument.doc_type == doc_type)
    if batch_id is not None:
        query = query.where(IntakeDocument.batch_id == batch_id)
    if decision is not None:
        query = query.where(IntakeDocument.decision == decision)
    query = query.order_by(IntakeDocument.id.desc()).limit(min(max(limit, 1), 500))
    return [_row(d) for d in db.scalars(query)]


def _block(result: dict, key: str) -> dict | None:
    value = result.get(key)
    return value if isinstance(value, dict) else None


@router.get("/intake/documents/{intake_id}")
def intake_detail(intake_id: int, _: User = Depends(staff), db: Session = Depends(get_db)):
    doc = get_or_404(db, IntakeDocument, intake_id)
    result = doc.screening_result or {}
    reviewer = db.get(User, doc.reviewed_by) if doc.reviewed_by else None
    history = db.execute(
        select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.entity_type == ENTITY, AuditLog.entity_id == str(doc.id))
        .order_by(AuditLog.id)).all()
    station = db.get(ScanStation, doc.station_id)
    student = db.get(Student, doc.student_id) if doc.student_id else None
    submission = db.get(ApplicantSubmission, doc.submission_id) if doc.submission_id else None
    applicant = db.get(User, submission.applicant_id) if submission else None
    return {
        **_row(doc),
        "station": station.name if station else None,
        # Online uploads: every page is served by GET /applicant-submissions/{id}/pages/{n}.
        "submission": {"id": submission.id, "pages": len(submission.pages),
                       "declared_type": submission.declared_type,
                       "issuing_school": submission.issuing_school,
                       "applicant_email": applicant.email if applicant else None,
                       "submitted_at": submission.submitted_at} if submission else None,
        "reasons_explained": [{"code": r, "text": screening.REASON_TEXT.get(r, r)}
                              for r in doc.reasons],
        "indicators": [i for i in result.get("indicators") or [] if isinstance(i, dict)],
        "quality": _block(result, "quality"),
        "checks": _block(result, "checks"),
        "similarity": _block(result, "similarity"),
        "predicted_doc_type": (_block(result, "doc_type") or {}).get("predicted"),
        "fields": _block(result, "fields"),
        "screening_result": result,
        "has_image": doc.image_key is not None,
        "student": {"id": student.id, "student_number": student.student_number,
                    "name": f"{student.last_name}, {student.first_name}"} if student else None,
        "review": {"reviewed_by": reviewer.email if reviewer else None,
                   "reviewed_at": doc.reviewed_at, "reason": doc.review_reason},
        "history": [{"timestamp": log.timestamp, "action": log.action, "user_email": email,
                     "entity_type": log.entity_type, "details": log.new_value}
                    for log, email in history],
        "note": NOTE,
    }


@router.get("/intake/documents/{intake_id}/image")
def intake_image(intake_id: int, _: User = Depends(staff), db: Session = Depends(get_db)):
    """Streamed by the API: storage URLs never reach the browser."""
    doc = get_or_404(db, IntakeDocument, intake_id)
    if doc.image_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No image")
    try:
        content = get_storage().get(doc.image_key)
    except StoredFileMissing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image missing") from None
    media = IMAGE_MEDIA_TYPES.get(doc.image_key.rsplit(".", 1)[-1], "image/jpeg")
    return Response(content, media_type=media, headers={"Cache-Control": "no-store"})


class IntakeReviewIn(BaseModel):
    action: IntakeReviewAction
    reason: str | None = Field(default=None, max_length=2000)


REVIEW_STATUS = {
    IntakeReviewAction.ACCEPT: IntakeStatus.ACCEPTED,
    IntakeReviewAction.REJECT: IntakeStatus.REJECTED,
    IntakeReviewAction.REQUEST_RESUBMISSION: IntakeStatus.RESUBMISSION_REQUESTED,
}


@router.post("/intake/documents/{intake_id}/review")
def review_intake(intake_id: int, body: IntakeReviewIn, request: Request,
                  actor: User = Depends(staff), db: Session = Depends(get_db)):
    """Accept, reject or request a resubmission. A reason is required to reject, to request
    a resubmission, and to accept a document that needs manual review (overriding a flag)."""
    doc = get_or_404(db, IntakeDocument, intake_id)
    if doc.status != IntakeStatus.SCREENED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already reviewed")
    reason = (body.reason or "").strip() or None
    needs_reason = (body.action != IntakeReviewAction.ACCEPT
                    or doc.decision == IntakeDecision.MANUAL_REVIEW)
    if needs_reason and (reason is None or len(reason) < 3):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "A reason (at least 3 characters) is required for this action")
    old_status = doc.status
    doc.status = REVIEW_STATUS[body.action]
    doc.reviewed_by = actor.id
    doc.reviewed_at = utcnow()
    doc.review_reason = reason
    audit.record(db, "intake_document_reviewed", user_id=actor.id, entity_type=ENTITY,
                 entity_id=doc.id, old_value={"status": old_status.value},
                 new_value={"status": doc.status.value, "action": body.action.value,
                            "decision": doc.decision.value, "reason": reason},
                 ip_address=client_ip(request))
    db.commit()
    return _row(doc)


# ---- settings (admin) -----------------------------------------------------------------------

class IntakeSettingsIn(BaseModel):
    min_doc_type_confidence: float = Field(ge=0, le=1)
    max_indicator_severity: float = Field(gt=0, le=1)
    min_layout_score: float = Field(ge=0, le=1)
    min_similarity_score: float = Field(ge=0, le=1)
    reason: str = Field(min_length=3, max_length=500)


def _settings_out(source) -> dict:
    return {**_thresholds_dict(source), "defaults": _thresholds_dict(screening.DEFAULTS),
            "updated_at": getattr(source, "updated_at", None)}


@router.get("/intake/settings")
def get_intake_settings(_: User = Depends(admin), db: Session = Depends(get_db)):
    return _settings_out(load_thresholds(db))


@router.put("/intake/settings")
def put_intake_settings(body: IntakeSettingsIn, request: Request, actor: User = Depends(admin),
                        db: Session = Depends(get_db)):
    row = db.get(IntakeSettings, 1)
    old = _thresholds_dict(row or screening.DEFAULTS)
    if row is None:
        row = IntakeSettings(id=1)
        db.add(row)
    new = body.model_dump(exclude={"reason"})
    for key, value in new.items():
        setattr(row, key, value)
    row.updated_by = actor.id
    audit.record(db, "intake_settings_changed", user_id=actor.id, entity_type="intake_settings",
                 entity_id=1, old_value=old, new_value={**new, "reason": body.reason},
                 ip_address=client_ip(request))
    db.commit()
    db.refresh(row)
    return _settings_out(row)
