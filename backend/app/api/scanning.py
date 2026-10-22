"""Scan station and validation operator endpoints (Sections 5I-5M, 9-10, 15).

Station endpoints authenticate with the station's API key (X-Station-Key) and never receive
student data: a lookup returns only template zones and the issued page count.
Operator endpoints need a registrar session with the validation permission.
"""

import csv
import html
import io
import json
from datetime import date, timedelta

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import client_ip, get_or_404, get_station, require_validator
from app.config import get_settings
from app.db import get_db
from app.documents.pdf import RendererUnavailable, render_pdf
from app.models import (
    DocumentRequest,
    GeneratedDocument,
    ScannedDocument,
    ScanSession,
    ScanStation,
    User,
    ValidationRecord,
)
from app.models.enums import DocumentType, ScanMode, ValidationResult
from app.services import audit
from app.services.scan_images import InvalidScanImage, compress_page
from app.services.scanning import (
    IMAGE_MEDIA_TYPES,
    find_document,
    record_scan,
    scan_count,
    scanned_number,
)
from app.services.validation import FOOTER_NOTE
from app.storage import StoredFileMissing, get_storage
from app.timeutil import as_utc, local_day_start, utcnow

router = APIRouter(tags=["scanning"])

IMAGE_TYPES = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg"}
STATION_STATES = {"ready", "scanning", "jam", "double_feed", "offline"}


# ---- station (API key) ----------------------------------------------------------------------

class LookupIn(BaseModel):
    qr_token: str | None = Field(default=None, max_length=100)
    document_number: str | None = Field(default=None, max_length=40)


class StatusIn(BaseModel):
    state: str
    detail: str | None = Field(default=None, max_length=500)


def _open_session(db: Session, station: ScanStation) -> ScanSession | None:
    return db.scalar(select(ScanSession).where(ScanSession.station_id == station.id,
                                               ScanSession.ended_at.is_(None))
                     .order_by(ScanSession.id.desc()))


@router.get("/station/session")
def station_session(station: ScanStation = Depends(get_station), db: Session = Depends(get_db)):
    """The session the operator opened on the dashboard for this station (mode + id)."""
    session = _open_session(db, station)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No open scan session. Start one from the operator dashboard.")
    return {"session_id": session.id, "mode": session.mode, "next_position": session.total_docs + 1}


@router.post("/station/status")
def station_status(body: StatusIn, station: ScanStation = Depends(get_station),
                   db: Session = Depends(get_db)):
    if body.state not in STATION_STATES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown scanner state")
    station.last_status = body.state
    station.last_status_detail = body.detail
    station.last_seen_at = utcnow()
    db.commit()
    return {"ok": True}


@router.post("/station/lookup")
def station_lookup(body: LookupIn, station: ScanStation = Depends(get_station),
                   db: Session = Depends(get_db)):
    doc, _ = find_document(db, body.qr_token, body.document_number)
    if doc is None:
        return {"found": False}
    return {"found": True, "document_number": doc.document_number,
            "document_type": doc.template.document_type, "page_count": doc.page_count,
            "field_zones": doc.template.field_zones}  # zones of the version it was printed with


@router.post("/station/sessions/{session_id}/documents", status_code=status.HTTP_201_CREATED)
async def submit_scan(
    session_id: int,
    payload: str = Form(...),
    pages: list[UploadFile] = File(...),
    station: ScanStation = Depends(get_station),
    db: Session = Depends(get_db),
):
    session = get_or_404(db, ScanSession, session_id)
    if session.station_id != station.id or session.ended_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Session is not open for this station")
    try:
        extracted = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "payload must be JSON") from None
    if not isinstance(extracted, dict) or not pages or len(pages) > 50:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid scan submission")

    settings = get_settings()
    limit = settings.max_scan_image_bytes
    images = []
    for upload in pages:
        content = await upload.read(limit + 1)
        kind = next((ext for magic, ext in IMAGE_TYPES.items() if content.startswith(magic)),
                    None)
        if kind is None or len(content) > limit:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "Pages must be PNG or JPEG under the size limit")
        try:  # CPU-bound: off the event loop
            images.append(await run_in_threadpool(
                compress_page, content, max_width=settings.scan_page_max_width,
                quality=settings.scan_page_jpeg_quality))
        except InvalidScanImage as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
        del content

    scanned, record = record_scan(db, session=session, station=station, extracted=extracted,
                                  images=images)
    db.commit()
    return {"scanned_document_id": scanned.id, "position": scanned.position_in_stack,
            "result": record.result, "reasons": record.reasons}


# ---- operator (registrar with validation permission) ----------------------------------------

class SessionIn(BaseModel):
    mode: ScanMode
    station_id: int


class ReviewIn(BaseModel):
    final_result: ValidationResult
    remarks: str = Field(min_length=3, max_length=2000)


def _summary(scanned: ScannedDocument) -> dict:
    record = scanned.validation
    return {
        "scanned_document_id": scanned.id, "position": scanned.position_in_stack,
        "document_number": scanned_number(scanned),
        "matched": scanned.matched_document_id is not None,
        "result": record.result if record else None,
        "final_result": record.final_result if record else None,
        "top_reason": record.reasons[0] if record and record.reasons else None,
        "scanned_at": scanned.created_at,
    }


# The station service reports every 15 s; a minute of silence means it is not running.
# Shown as "no signal", never blocking: the one-shot command does not send heartbeats.
STATION_SILENT_AFTER = timedelta(seconds=60)


@router.get("/scan-stations")
def list_stations(_: User = Depends(require_validator), db: Session = Depends(get_db)):
    now = utcnow()
    return [{"id": s.id, "name": s.name, "is_active": s.is_active, "status": s.last_status,
             "status_detail": s.last_status_detail, "last_seen_at": s.last_seen_at,
             "silent": s.last_seen_at is None
             or now - as_utc(s.last_seen_at) > STATION_SILENT_AFTER}
            for s in db.scalars(select(ScanStation).where(ScanStation.is_active.is_(True)))]


@router.post("/scan-sessions", status_code=status.HTTP_201_CREATED)
def start_session(body: SessionIn, request: Request, operator: User = Depends(require_validator),
                  db: Session = Depends(get_db)):
    station = get_or_404(db, ScanStation, body.station_id)
    if not station.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Station is deactivated")
    if station.last_status == "offline":  # UC4: scanner offline blocks scanning
        detail = f" ({station.last_status_detail})" if station.last_status_detail else ""
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Scanner is offline{detail}. Reconnect it, then start again.")
    now = utcnow()
    db.execute(update(ScanSession).where(ScanSession.station_id == station.id,
                                         ScanSession.ended_at.is_(None)).values(ended_at=now))
    session = ScanSession(mode=body.mode, operator_id=operator.id, station_id=station.id)
    db.add(session)
    db.flush()
    audit.record(db, "scan_session_started", user_id=operator.id, entity_type="scan_session",
                 entity_id=session.id, new_value={"mode": body.mode, "station": station.name},
                 ip_address=client_ip(request))
    db.commit()
    # Section 9: two operator-directed trays; the dashboard tells the operator which one.
    return {"session_id": session.id, "mode": session.mode,
            "output_tray": "Single" if body.mode == ScanMode.SINGLE else "Bulk"}


@router.post("/scan-sessions/{session_id}/end")
def end_session(session_id: int, request: Request, operator: User = Depends(require_validator),
                db: Session = Depends(get_db)):
    session = get_or_404(db, ScanSession, session_id)
    if session.ended_at is None:
        session.ended_at = utcnow()
        audit.record(db, "scan_session_ended", user_id=operator.id, entity_type="scan_session",
                     entity_id=session.id, new_value={"total_docs": session.total_docs},
                     ip_address=client_ip(request))
        db.commit()
    return {"session_id": session.id, "ended_at": session.ended_at}


@router.get("/scan-sessions/{session_id}")
def get_session(session_id: int, _: User = Depends(require_validator),
                db: Session = Depends(get_db)):
    """Bulk batch table (Section 16): position, document number, result, top reason, totals."""
    session = get_or_404(db, ScanSession, session_id)
    docs = sorted(session.scanned_documents, key=lambda d: (d.position_in_stack or 0, d.id))
    rows = [_summary(d) for d in docs]
    totals = {r.value: 0 for r in ValidationResult}
    for row in rows:
        if row["result"]:
            totals[row["result"]] += 1
    return {"session_id": session.id, "mode": session.mode, "started_at": session.started_at,
            "ended_at": session.ended_at, "total": len(rows), "totals": totals,
            "documents": rows}


class ScanFilter(BaseModel):
    """Section 16: recent scans filtered by result, type and date; exports by date range."""

    result: ValidationResult | None = None
    document_type: DocumentType | None = None
    date_from: date | None = None
    date_to: date | None = None

    def apply(self, query):
        if self.result is not None:
            query = query.where(ValidationRecord.result == self.result)
        if self.document_type is not None:
            query = (query.join(GeneratedDocument,
                                GeneratedDocument.id == ScannedDocument.matched_document_id)
                     .join(DocumentRequest, DocumentRequest.id == GeneratedDocument.request_id)
                     .where(DocumentRequest.document_type == self.document_type))
        if self.date_from is not None:
            query = query.where(ScannedDocument.created_at >= local_day_start(self.date_from))
        if self.date_to is not None:
            query = query.where(ScannedDocument.created_at
                                < local_day_start(self.date_to + timedelta(days=1)))
        return query


@router.get("/validations")
def recent_validations(filters: ScanFilter = Depends(), limit: int = 50,
                       _: User = Depends(require_validator), db: Session = Depends(get_db)):
    query = filters.apply(select(ScannedDocument).join(ValidationRecord))
    query = query.order_by(ScannedDocument.id.desc())
    return [_summary(d) for d in db.scalars(query.limit(min(max(limit, 1), 200)))]


def _field_family(name: str) -> str:
    """'grade IT101 (1st Semester, ...)' -> 'grade', so failures group by kind of field."""
    for family in ("grade", "units", "term GWA"):
        if name.startswith(family):
            return family
    return name


@router.get("/validations/stats")
def validation_stats(filters: ScanFilter = Depends(), _: User = Depends(require_validator),
                     db: Session = Depends(get_db)):
    """Result counts and which fields fail most (helps tell OCR trouble from tampering)."""
    results = {r.value: 0 for r in ValidationResult}
    failures: dict[str, int] = {}
    query = filters.apply(select(ValidationRecord).join(
        ScannedDocument, ScannedDocument.id == ValidationRecord.scanned_document_id))
    for record in db.scalars(query):
        results[record.result.value] += 1
        for f in record.field_results or []:
            if f.get("status") != "match":
                family = _field_family(f["field"])
                failures[family] = failures.get(family, 0) + 1
    breakdown = sorted(failures.items(), key=lambda kv: -kv[1])
    return {"results": results, "total": sum(results.values()),
            "failed_fields": [{"field": k, "count": v} for k, v in breakdown]}


@router.get("/validations/export")
def export_validations(
    format: str = "csv",
    session_id: int | None = None,
    filters: ScanFilter = Depends(),
    _: User = Depends(require_validator),
    db: Session = Depends(get_db),
):
    """Batch or date-range results as CSV or PDF (Section 16 'Export')."""
    query = filters.apply(select(ScannedDocument).join(ValidationRecord))
    query = query.order_by(ScannedDocument.id)
    if session_id is not None:
        query = query.where(ScannedDocument.session_id == session_id)
    documents = list(db.scalars(query.limit(5000)))
    rows = [_summary(d) for d in documents]
    for row, scanned in zip(rows, documents, strict=True):
        record = scanned.validation
        row.update(reasons=record.reasons, field_results=record.field_results,
                   reviewed_by=record.reviewed_by, review_remarks=record.review_remarks,
                   extracted_fields=scanned.extracted_fields,
                   ocr_confidence=scanned.ocr_confidence)
    if session_id:
        name = f"validation-session-{session_id}"
    elif filters.date_from or filters.date_to:
        name = f"validation-{filters.date_from or 'start'}-to-{filters.date_to or 'today'}"
    else:
        name = "validation-results"

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["position", "scanned_at", "document_number", "result",
                         "reviewed_result", "reason", "all_reasons", "field_discrepancies",
                         "reviewed_by", "review_remarks", "ocr_confidence", "extracted_fields"])
        for r in rows:
            writer.writerow([r["position"], r["scanned_at"], r["document_number"] or "",
                             r["result"], r["final_result"] or "", r["top_reason"] or "",
                             json.dumps(r["reasons"], ensure_ascii=False),
                             json.dumps(r["field_results"], ensure_ascii=False), r["reviewed_by"],
                             r["review_remarks"], r["ocr_confidence"],
                             json.dumps(r["extracted_fields"], ensure_ascii=False)])
        return Response(buffer.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})

    if format != "pdf":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "format must be csv or pdf")
    body = "".join(
        f"<tr><td>{r['position'] or ''}</td><td>{html.escape(r['document_number'] or '?')}</td>"
        f"<td>{r['result']}</td><td>{r['final_result'] or ''}</td>"
        f"<td>{html.escape(r['top_reason'] or '')}</td></tr>" for r in rows)
    page = (f"<html><body style='font-family:sans-serif;font-size:9pt'>"
            f"<h2>Validation report</h2><p>{len(rows)} document(s). {FOOTER_NOTE}</p>"
            "<table border='1' cellspacing='0' cellpadding='4' style='width:100%;"
            "border-collapse:collapse'><tr><th>#</th><th>Document</th><th>Result</th>"
            f"<th>Reviewed</th><th>Reason</th></tr>{body}</table></body></html>")
    report_keys = ("reasons", "field_results", "reviewed_by", "review_remarks",
                   "ocr_confidence", "extracted_fields")
    details = []
    for row in rows:
        number = html.escape(str(row["document_number"] or "?"))
        serialized = html.escape(json.dumps({key: row[key] for key in report_keys},
                                            indent=2, ensure_ascii=False))
        details.append(f"<h3>Document {number}</h3>"
                       "<pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"
                       f"{serialized}</pre>")
    page = page.replace("</body>", "".join(details) + "</body>")
    try:
        pdf = render_pdf(page, "Validation report", utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    except RendererUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None
    return Response(pdf.content, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.get("/validations/{scanned_id}")
def validation_detail(scanned_id: int, _: User = Depends(require_validator),
                      db: Session = Depends(get_db)):
    scanned = get_or_404(db, ScannedDocument, scanned_id)
    record = scanned.validation
    doc = (db.get(GeneratedDocument, scanned.matched_document_id)
           if scanned.matched_document_id is not None else None)
    return {
        **_summary(scanned),
        "reasons": record.reasons,
        "fields": record.field_results,  # printed vs official, status, confidence
        "review": {"final_result": record.final_result, "remarks": record.review_remarks,
                   "reviewed_by": record.reviewed_by},
        "document": None if doc is None else {
            "id": doc.id, "document_number": doc.document_number, "status": doc.status,
            "document_type": doc.template.document_type,
            "student_name": doc.data_snapshot.get("student_name"),
            "times_scanned": scan_count(db, doc.id),
        },
        "page_images": len(scanned.image_paths),
        "ocr_pages": [
            {"page": index + 1,
             "text": page["ocr_text"][:50_000]
                if isinstance(page.get("ocr_text"), str) else None,
             "truncated": page.get("ocr_text_truncated") is True,
             "error": "Full-page OCR could not finish." if page.get("ocr_text_error") else None}
            for index, page in enumerate((scanned.extracted_fields or {}).get("pages", []))
            if isinstance(page, dict)
        ],
        # Zones (mm on A4) of the template version it was printed with, so the dashboard
        # can show each field's crop of the scan next to printed vs. official values.
        "zones": doc.template.field_zones if doc is not None else None,
        "note": FOOTER_NOTE,
    }


@router.get("/validations/{scanned_id}/pages/{page}")
def validation_page(scanned_id: int, page: int, _: User = Depends(require_validator),
                    db: Session = Depends(get_db)):
    scanned = get_or_404(db, ScannedDocument, scanned_id)
    if not 1 <= page <= len(scanned.image_paths):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such page")
    key = scanned.image_paths[page - 1]
    try:
        content = get_storage().get(key)
    except StoredFileMissing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image missing") from None
    media = IMAGE_MEDIA_TYPES.get(key.rsplit(".", 1)[-1], "image/jpeg")
    return Response(content, media_type=media, headers={"Cache-Control": "no-store"})


@router.post("/validations/{scanned_id}/review")
def review_validation(scanned_id: int, body: ReviewIn, request: Request,
                      operator: User = Depends(require_validator),
                      db: Session = Depends(get_db)):
    """Confirm a NEEDS_REVIEW as VALID or INVALID. The automatic result is kept (Section 15)."""
    record = get_or_404(db, ScannedDocument, scanned_id).validation
    if record.result != ValidationResult.NEEDS_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only NEEDS_REVIEW results are reviewed")
    if record.final_result is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already reviewed")
    if body.final_result == ValidationResult.NEEDS_REVIEW:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Choose VALID or INVALID")
    record.final_result = body.final_result
    record.review_remarks = body.remarks
    record.reviewed_by = operator.id
    audit.record(db, "validation_reviewed", user_id=operator.id, entity_type="scanned_document",
                 entity_id=scanned_id,
                 new_value={"automatic": record.result.value,
                            "final": body.final_result.value, "remarks": body.remarks},
                 ip_address=client_ip(request))
    db.commit()
    return {"scanned_document_id": scanned_id, "result": record.result,
            "final_result": record.final_result}
