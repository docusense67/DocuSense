"""Applicant intake: documents from another school, uploaded online (docs/applicant-intake.md).

An applicant (Role.APPLICANT) uploads the pages of a TOR or COE. The OCR worker on the station
reads them (station key, lease) and the backend turns the reading into an IntakeDocument with
source "online": from there on it goes through the same staff review, field review and
student link as a paper document from the intake station.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONType
from app.models.enums import (
    ApplicantSubmissionStatus,
    IntakeDocType,
    ReferenceLabel,
    ReferenceStatus,
    ReferenceUse,
    SecondPassStatus,
)


class ApplicantSubmission(Base):
    __tablename__ = "applicant_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    declared_type: Mapped[IntakeDocType] = mapped_column(
        Enum(IntakeDocType, native_enum=False, length=40))
    issuing_school: Mapped[str] = mapped_column(String(200))
    status: Mapped[ApplicantSubmissionStatus] = mapped_column(
        Enum(ApplicantSubmissionStatus, native_enum=False, length=20),
        default=ApplicantSubmissionStatus.DRAFT, index=True)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # OCR job: the worker holds a lease (token stored hashed) until it completes or expires.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_station_id: Mapped[int | None] = mapped_column(ForeignKey("scan_stations.id"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)  # last worker error (no document text)
    intake_document_id: Mapped[int | None] = mapped_column(ForeignKey("intake_documents.id"))
    # Set when registrar staff created the submission on the applicant's behalf (audited).
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    pages: Mapped[list["ApplicantPage"]] = relationship(
        back_populates="submission", order_by="ApplicantPage.sequence")
    second_pass: Mapped["ApplicantSecondPass | None"] = relationship(
        back_populates="submission")


class ApplicantPage(Base):
    """One page image (a PDF upload is split into its pages when it arrives)."""

    __tablename__ = "applicant_pages"
    __table_args__ = (UniqueConstraint("submission_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("applicant_submissions.id"),
                                               index=True)
    sequence: Mapped[int] = mapped_column(Integer)  # 1-based page order
    storage_key: Mapped[str] = mapped_column(String(255))  # app/storage.py key
    sha256: Mapped[str] = mapped_column(String(64))  # of the uploaded file (duplicate check)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    submission: Mapped[ApplicantSubmission] = relationship(back_populates="pages")


class ApplicantSecondPass(Base):
    """The second OCR pass over an accepted online document's pages, with independent
    preprocessing. Its reading is kept here, apart from the first pass (which stays in the
    intake document's screening_result); staff see where the two passes disagree."""

    __tablename__ = "applicant_second_passes"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("applicant_submissions.id"),
                                               unique=True)
    status: Mapped[SecondPassStatus] = mapped_column(
        Enum(SecondPassStatus, native_enum=False, length=20),
        default=SecondPassStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_station_id: Mapped[int | None] = mapped_column(ForeignKey("scan_stations.id"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)  # last worker error (no document text)
    reading: Mapped[dict | None] = mapped_column(JSONType)  # same schema as the first pass
    disagreements: Mapped[list | None] = mapped_column(JSONType)  # field names only
    queued_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    submission: Mapped[ApplicantSubmission] = relationship(back_populates="second_pass")


class ApplicantReference(Base):
    """A labelled reference sample of one issuing school's TOR or COE (or of an unrelated
    document, label "other"). The OCR worker reads it like an upload; staff approve the
    reading. The latest approved genuine-sample training version for a school and type is the
    reference layout an upload is compared with. A match is not proof of authenticity."""

    __tablename__ = "applicant_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    issuing_school: Mapped[str] = mapped_column(String(200))
    school_key: Mapped[str] = mapped_column(String(200), index=True)  # normalised name
    doc_type: Mapped[IntakeDocType] = mapped_column(
        Enum(IntakeDocType, native_enum=False, length=40))
    label: Mapped[ReferenceLabel] = mapped_column(
        Enum(ReferenceLabel, native_enum=False, length=20))
    use: Mapped[ReferenceUse] = mapped_column(Enum(ReferenceUse, native_enum=False, length=20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey("applicant_references.id"))
    status: Mapped[ReferenceStatus] = mapped_column(
        Enum(ReferenceStatus, native_enum=False, length=20),
        default=ReferenceStatus.QUEUED, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)  # why approved / retired
    # {"required_fields": [...], "field_labels": {field: [aliases]}, "layout_tolerance": 0.15}
    rules: Mapped[dict] = mapped_column(JSONType, default=dict)
    page_keys: Mapped[list] = mapped_column(JSONType, default=list)  # app/storage.py keys
    reading: Mapped[dict | None] = mapped_column(JSONType)  # the worker's reading
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64))
    lease_station_id: Mapped[int | None] = mapped_column(ForeignKey("scan_stations.id"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
