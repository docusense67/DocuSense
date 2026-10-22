from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONType
from app.models.enums import ScanMode, ValidationResult


class ScanStation(Base):
    """A registered scan station; authenticates with its own API key (Section 18)."""

    __tablename__ = "scan_stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    registered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Reported by the scan agent: ready / scanning / jam / double_feed / offline.
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_status_detail: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StationEventReceipt(Base):
    """Deduplicates bridge events even when several viewers poll the same station."""

    __tablename__ = "station_event_receipts"
    station_id: Mapped[int] = mapped_column(ForeignKey("scan_stations.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScanSession(Base):
    __tablename__ = "scan_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[ScanMode] = mapped_column(Enum(ScanMode, native_enum=False, length=10))
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    station_id: Mapped[int | None] = mapped_column(ForeignKey("scan_stations.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_docs: Mapped[int] = mapped_column(Integer, default=0)

    scanned_documents: Mapped[list["ScannedDocument"]] = relationship(back_populates="session")


class ScannedDocument(Base):
    __tablename__ = "scanned_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("scan_sessions.id"), index=True)
    position_in_stack: Mapped[int | None] = mapped_column(Integer)
    image_paths: Mapped[list] = mapped_column(JSONType, default=list)
    matched_document_id: Mapped[int | None] = mapped_column(ForeignKey("generated_documents.id"))
    extracted_fields: Mapped[dict | None] = mapped_column(JSONType)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ScanSession] = relationship(back_populates="scanned_documents")
    validation: Mapped["ValidationRecord | None"] = relationship(back_populates="scanned_document")


class ValidationRecord(Base):
    __tablename__ = "validation_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    scanned_document_id: Mapped[int] = mapped_column(
        ForeignKey("scanned_documents.id"), unique=True
    )
    result: Mapped[ValidationResult] = mapped_column(
        Enum(ValidationResult, native_enum=False, length=20)
    )
    field_results: Mapped[list] = mapped_column(JSONType, default=list)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)
    # Human decision on NEEDS_REVIEW; the automated `result` is never overwritten.
    final_result: Mapped[ValidationResult | None] = mapped_column(
        Enum(ValidationResult, native_enum=False, length=20)
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_remarks: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scanned_document: Mapped[ScannedDocument] = relationship(back_populates="validation")
