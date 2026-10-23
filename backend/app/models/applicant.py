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

from app.db import Base
from app.models.enums import ApplicantSubmissionStatus, IntakeDocType


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    pages: Mapped[list["ApplicantPage"]] = relationship(
        back_populates="submission", order_by="ApplicantPage.sequence")


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
