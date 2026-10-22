"""Incoming enrollment documents screened at the intake station (Part I).

Contract: docs/intake/screening-contract.md. The station only measures; the backend decides
(`services/intake.decide`) and assigns the tray. A staff member then accepts, rejects or asks
for a resubmission. "Passed screening" is never a statement that a document is authentic.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONType
from app.models.enums import IntakeDecision, IntakeDocType, IntakeStatus


class IntakeBatch(Base):
    """A mixed-document stack fed through the station in one go (S-10)."""

    __tablename__ = "intake_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("scan_stations.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_docs: Mapped[int] = mapped_column(Integer, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    manual_review_count: Mapped[int] = mapped_column(Integer, default=0)

    documents: Mapped[list["IntakeDocument"]] = relationship(back_populates="batch")


class IntakeDocument(Base):
    __tablename__ = "intake_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("intake_batches.id"), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("scan_stations.id"), index=True)
    sequence: Mapped[int | None] = mapped_column(Integer)
    doc_type: Mapped[IntakeDocType] = mapped_column(
        Enum(IntakeDocType, native_enum=False, length=40), index=True)
    doc_type_confidence: Mapped[float | None] = mapped_column(Float)
    # The screening JSON exactly as the station sent it (unknown keys included).
    screening_result: Mapped[dict] = mapped_column(JSONType)
    # Effective decision: a routing mismatch or jam turns passed_screening into manual_review.
    decision: Mapped[IntakeDecision] = mapped_column(
        Enum(IntakeDecision, native_enum=False, length=20), index=True)
    reasons: Mapped[list] = mapped_column(JSONType, default=list)  # reason codes
    assigned_tray: Mapped[int] = mapped_column(Integer)  # 1 or 2, from the decision
    routed_tray: Mapped[int | None] = mapped_column(Integer)  # what the gate reported
    routed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    routing_jam: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[IntakeStatus] = mapped_column(
        Enum(IntakeStatus, native_enum=False, length=30), default=IntakeStatus.SCREENED,
        index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text)
    image_key: Mapped[str | None] = mapped_column(String(255))  # app/storage.py key
    # Staff-reviewed field set (S-06): {"values": {key: str|None}, "changed": [keys]}. The
    # station's own reading stays untouched in screening_result["fields"].
    reviewed_fields: Mapped[dict | None] = mapped_column(JSONType)
    fields_reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    fields_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The student this enrollment document belongs to (S-07); a student has many documents.
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id"), index=True)
    linked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    batch: Mapped[IntakeBatch | None] = relationship(back_populates="documents")


class IntakeSettings(Base):
    """Single row (id 1): thresholds of the screening decision. Defaults = contract defaults."""

    __tablename__ = "intake_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    min_doc_type_confidence: Mapped[float] = mapped_column(Float, default=0.75)
    max_indicator_severity: Mapped[float] = mapped_column(Float, default=0.5)
    min_layout_score: Mapped[float] = mapped_column(Float, default=0.70)
    min_similarity_score: Mapped[float] = mapped_column(Float, default=0.60)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
