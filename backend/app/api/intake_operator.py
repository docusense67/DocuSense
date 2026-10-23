"""Operator console for the incoming-document (intake) station: read-only live view.

The validation operator (registrar with the validation permission, like the outgoing scan
station) watches the intake station on its TFT screen: station status, the running batch,
each document's type, screening decision, tray and plain-language reasons, and tray counts.

Never in these responses: extracted field values, indicator details, image URLs or the raw
screening result. Reviewing incoming documents stays with registrar staff (app/api/intake.py).
Wording: "passed screening" / "needs manual review" (CLAUDE.md, screening contract).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_validator
from app.api.scanning import STATION_SILENT_AFTER
from app.db import get_db
from app.models import IntakeBatch, IntakeDocument, ScanStation, User
from app.models.enums import IntakeDecision, IntakeSource
from app.services import audit
from app.services.intake import REASON_TEXT, SEVERE_INDICATOR
from app.timeutil import PH_TZ, as_utc, local_day_start, utcnow

router = APIRouter(prefix="/intake-station", tags=["intake operator"])

# The operator never sees indicators, so the reason must not point at them.
OPERATOR_TEXT = {**REASON_TEXT, SEVERE_INDICATOR: "The station flagged at least one feature of the "
                                                  "page for a person to look at."}

# What the agent reports on /station/status, as the operator console groups it.
STATE_GROUP = {"ready": "idle", "scanning": "scanning", "jam": "error",
               "double_feed": "error", "offline": "offline"}


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite drops the timezone on read: always answer in UTC with an offset."""
    return as_utc(dt) if dt is not None else None


def station_state(station: ScanStation, now: datetime) -> str:
    """idle / scanning / error / offline / no_signal / unknown.

    "no_signal": the station service reports every 15 s, so a minute of silence means it is
    not running (an explicit "offline" report wins, it carries the agent's reason)."""
    reported = station.last_status
    if reported == "offline":
        return "offline"
    if station.last_seen_at is None or now - as_utc(station.last_seen_at) > STATION_SILENT_AFTER:
        return "no_signal" if station.last_seen_at is not None else "unknown"
    return STATE_GROUP.get(reported or "", "unknown")


def _station_out(db: Session, station: ScanStation, now: datetime) -> dict:
    has_intake = db.scalar(select(exists().where(
        IntakeDocument.station_id == station.id,
        IntakeDocument.source == IntakeSource.STATION))) or \
        db.scalar(select(exists().where(IntakeBatch.station_id == station.id)))
    return {"id": station.id, "name": station.name, "is_active": station.is_active,
            "state": station_state(station, now), "status": station.last_status,
            "status_detail": station.last_status_detail, "last_seen_at": _utc(station.last_seen_at),
            "intake": bool(has_intake)}


def routing(doc: IntakeDocument) -> str:
    """Where the page physically is: pending (gate not reported yet), ok, jam, wrong_tray."""
    if doc.routing_jam:
        return "jam"
    if doc.routed_tray is None:
        return "pending"
    return "ok" if doc.routed_tray == tray_for(doc) else "wrong_tray"


def tray_for(doc: IntakeDocument) -> int:
    """The tray the page belongs in now. A jam or mismatch already made it manual_review."""
    return 1 if doc.decision == IntakeDecision.PASSED_SCREENING else 2


def document_out(doc: IntakeDocument) -> dict:
    """Operator summary of one page: type, decision, tray, reasons. No field values."""
    return {
        "id": doc.id, "batch_id": doc.batch_id, "sequence": doc.sequence,
        "doc_type": doc.doc_type, "decision": doc.decision, "tray": tray_for(doc),
        "assigned_tray": doc.assigned_tray, "routed_tray": doc.routed_tray,
        "routing": routing(doc), "status": doc.status, "created_at": _utc(doc.created_at),
        "reasons": [{"code": r, "text": OPERATOR_TEXT.get(r, r.replace("_", " "))}
                    for r in doc.reasons or []],
    }


def counts(docs: list[IntakeDocument]) -> dict:
    states = [routing(d) for d in docs]
    passed = sum(1 for d in docs if d.decision == IntakeDecision.PASSED_SCREENING)
    return {"total": len(docs), "passed_screening": passed, "manual_review": len(docs) - passed,
            "tray_1": passed, "tray_2": len(docs) - passed,
            "pending": states.count("pending"), "jams": states.count("jam"),
            "wrong_tray": states.count("wrong_tray")}


def batch_out(batch: IntakeBatch) -> dict:
    return {"batch_id": batch.id, "started_at": _utc(batch.started_at),
            "ended_at": _utc(batch.ended_at),
            "open": batch.ended_at is None, "counts": counts(list(batch.documents))}


def _station_or_404(db: Session, station_id: int) -> ScanStation:
    return get_or_404(db, ScanStation, station_id)


@router.get("/stations")
def intake_stations(_: User = Depends(require_validator), db: Session = Depends(get_db)):
    """Active stations; those that have screened incoming documents first."""
    now = utcnow()
    rows = [_station_out(db, s, now) for s in db.scalars(
        select(ScanStation).where(ScanStation.is_active.is_(True)).order_by(ScanStation.name))]
    return sorted(rows, key=lambda r: not r["intake"])


@router.get("/stations/{station_id}/live")
def intake_live(station_id: int, limit: int = Query(10, ge=1, le=50),
                _: User = Depends(require_validator), db: Session = Depends(get_db)):
    """One poll for the TFT screen: station status, current batch, latest pages, today."""
    station = _station_or_404(db, station_id)
    now = utcnow()
    batch = db.scalar(select(IntakeBatch).where(IntakeBatch.station_id == station.id)
                      .order_by(IntakeBatch.ended_at.is_(None).desc(), IntakeBatch.id.desc())
                      .limit(1))
    if batch is not None and batch.ended_at is None:  # running batch: its pages only
        scope = IntakeDocument.batch_id == batch.id
    else:  # between batches: the station's latest pages (a batch is optional per page)
        scope = IntakeDocument.station_id == station.id
    # Online uploads read by this station's OCR worker never went through its trays.
    paper = IntakeDocument.source == IntakeSource.STATION
    documents = db.scalars(select(IntakeDocument).where(scope, paper)
                           .order_by(IntakeDocument.id.desc()).limit(limit))
    today = list(db.scalars(select(IntakeDocument).where(
        IntakeDocument.station_id == station.id, paper,
        IntakeDocument.created_at >= local_day_start(now.astimezone(PH_TZ).date()))))
    return {
        "station": _station_out(db, station, now),
        "batch": batch_out(batch) if batch is not None else None,
        "documents": [document_out(d) for d in documents],
        "today": counts(today),
        "checked_at": now,
    }


@router.get("/stations/{station_id}/batches")
def intake_batches(station_id: int, limit: int = Query(10, ge=1, le=50),
                   _: User = Depends(require_validator), db: Session = Depends(get_db)):
    """Recent batches of a station, newest first, with counts per tray."""
    station = _station_or_404(db, station_id)
    batches = db.scalars(select(IntakeBatch).where(IntakeBatch.station_id == station.id)
                         .order_by(IntakeBatch.id.desc()).limit(limit))
    return [batch_out(b) for b in batches]


@router.get("/batches/{batch_id}")
def intake_batch(batch_id: int, _: User = Depends(require_validator),
                 db: Session = Depends(get_db)):
    batch = get_or_404(db, IntakeBatch, batch_id)
    station = db.get(ScanStation, batch.station_id)
    docs = sorted(batch.documents, key=lambda d: (d.sequence is None, d.sequence or 0, d.id))
    return {**batch_out(batch), "station": station.name if station else None,
            "documents": [document_out(d) for d in docs]}


@router.post("/batches/{batch_id}/end")
def end_intake_batch(batch_id: int, request: Request, operator: User = Depends(require_validator),
                     db: Session = Depends(get_db)):
    """The operator closes the stack (e.g. the station stopped without ending it). The
    station's next page for this batch gets 409 and it starts a new batch. Ending twice is
    harmless."""
    batch = get_or_404(db, IntakeBatch, batch_id)
    if batch.ended_at is None:
        batch.ended_at = utcnow()
        summary = counts(list(batch.documents))
        station = db.get(ScanStation, batch.station_id)
        audit.record(db, "intake_batch_ended", user_id=operator.id, entity_type="intake_batch",
                     entity_id=batch.id, ip_address=client_ip(request),
                     new_value={"station": station.name if station else None,
                                "ended_by": "operator",
                                **{k: summary[k] for k in ("total", "passed_screening",
                                                           "manual_review", "jams",
                                                           "wrong_tray")}})
        db.commit()
    return batch_out(batch)
