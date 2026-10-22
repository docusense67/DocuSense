"""Record a scanned document and its validation result (Sections 5I-5L)."""

from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.documents.context import BuildOptions, MissingDataError
from app.documents.registry import REGISTRY
from app.models import (
    GeneratedDocument,
    ScannedDocument,
    ScanSession,
    ScanStation,
    ValidationRecord,
)
from app.services import audit
from app.services.validation import Identity, Thresholds, evaluate
from app.storage import get_storage

IMAGE_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg"}


def find_document(db: Session, qr_token: str | None,
                  document_number: str | None) -> tuple[GeneratedDocument | None, Identity]:
    """QR token first (Level 0); fall back to the OCR'd document number.

    The backend decides how the document was identified; the agent only reports what it
    read. A QR that was read but belongs to no issued document is reported as such, never
    silently replaced by the printed number."""
    if qr_token:
        doc = db.scalar(select(GeneratedDocument).where(GeneratedDocument.qr_token == qr_token))
        if doc is not None:
            return doc, "qr"
    identity: Identity = "foreign_qr" if qr_token else "number"
    if document_number:
        return db.scalar(select(GeneratedDocument).where(
            GeneratedDocument.document_number == document_number.strip().upper())), identity
    return None, identity


def current_records(db: Session, doc: GeneratedDocument) -> dict:
    """The document's values rebuilt from today's records, for the 'changed since issue' check."""
    request = doc.request
    try:
        values = REGISTRY[request.document_type].build_context(
            db, request.student, BuildOptions(issued_on=date.today(), purpose=request.purpose))
    except MissingDataError as e:
        return {"_unavailable": e.missing}  # never equal to the snapshot -> review
    return {**values, "document_type": request.document_type.value,
            "document_number": doc.document_number}


def scanned_number(scanned: ScannedDocument) -> str | None:
    """The matched document number, else whatever number OCR read off the paper."""
    extracted = scanned.extracted_fields or {}
    return extracted.get("lookup_document_number") or extracted.get("document_number_read")


def scan_count(db: Session, document_id: int) -> int:
    return db.scalar(select(func.count()).select_from(ScannedDocument)
                     .where(ScannedDocument.matched_document_id == document_id)) or 0


def record_scan(db: Session, *, session: ScanSession, station: ScanStation, extracted: dict,
                images: list[tuple[bytes, str]]) -> tuple[ScannedDocument, ValidationRecord]:
    settings = get_settings()
    doc, identity = find_document(db, extracted.get("qr_token"),
                                  extracted.get("document_number_read"))

    outcome = evaluate(
        identity=identity,
        document_found=doc is not None,
        document_status=doc.status if doc else None,
        issued_page_count=doc.page_count if doc else None,
        snapshot=doc.data_snapshot if doc else None,
        data_hash=doc.data_hash if doc else None,
        extracted=extracted,
        current_records=current_records(db, doc) if doc else None,
        thresholds=Thresholds(confidence=settings.ocr_confidence_threshold,
                              name_similarity=settings.name_review_similarity,
                              grade_scale=tuple(settings.grade_scale)),
        # the zones of the version it was printed with, not today's active template
        template_zones=doc.template.field_zones if doc else None,
    )

    confidences = [f.confidence for f in outcome.fields if f.confidence is not None]
    scanned = ScannedDocument(
        session_id=session.id, position_in_stack=extracted.get("position"),
        matched_document_id=doc.id if doc else None, extracted_fields=extracted,
        ocr_confidence=min(confidences) if confidences else None, image_paths=[],
    )
    db.add(scanned)
    db.flush()

    folder = Path("scans") / str(session.id) / str(scanned.id)
    storage = get_storage()
    paths = []
    for number, (content, extension) in enumerate(images, start=1):
        relative = (folder / f"page-{number}.{extension}").as_posix()
        storage.put(relative, content, IMAGE_MEDIA_TYPES[extension])
        paths.append(relative)
    scanned.image_paths = paths

    record = ValidationRecord(scanned_document_id=scanned.id, result=outcome.result,
                              field_results=outcome.field_dicts(), reasons=outcome.reasons)
    db.add(record)
    session.total_docs += 1
    db.flush()
    audit.record(db, "document_scanned", user_id=session.operator_id,
                 entity_type="generated_document" if doc else "scanned_document",
                 entity_id=doc.id if doc else scanned.id,
                 new_value={"result": outcome.result.value, "session_id": session.id,
                            "station": station.name, "scanned_document_id": scanned.id,
                            "document_number": doc.document_number if doc else
                            extracted.get("document_number_read")})
    return scanned, record
