from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, JSONType
from app.models.enums import DocumentType, GeneratedDocumentStatus, RequestStatus, TemplateStatus


class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = (UniqueConstraint("document_type", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, native_enum=False, length=10)
    )
    version: Mapped[int] = mapped_column(Integer)
    html_content: Mapped[str] = mapped_column(Text)
    placeholder_list: Mapped[list] = mapped_column(JSONType, default=list)
    field_zones: Mapped[dict | None] = mapped_column(JSONType)  # OCR zone map for this version
    status: Mapped[TemplateStatus] = mapped_column(
        Enum(TemplateStatus, native_enum=False, length=10), default=TemplateStatus.DRAFT
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentRequest(Base):
    __tablename__ = "document_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, native_enum=False, length=10)
    )
    purpose: Mapped[str] = mapped_column(String(200))
    copies: Mapped[int] = mapped_column(Integer, default=1)
    remarks: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, native_enum=False, length=20), default=RequestStatus.REQUESTED
    )
    payment_confirmed: Mapped[bool] = mapped_column(default=False)
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # registrar walk-ins
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    # Why generation is blocked (missing data, no active template); registrar is alerted.
    processing_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    student: Mapped["Student"] = relationship()  # noqa: F821
    generated_documents: Mapped[list["GeneratedDocument"]] = relationship(
        back_populates="request", order_by="GeneratedDocument.id"
    )


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("document_requests.id"), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("document_templates.id"))
    data_snapshot: Mapped[dict] = mapped_column(JSONType)  # never updated after approval
    data_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 hex
    qr_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pdf_path: Mapped[str | None] = mapped_column(String(500))
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[GeneratedDocumentStatus] = mapped_column(
        Enum(GeneratedDocumentStatus, native_enum=False, length=20),
        default=GeneratedDocumentStatus.GENERATED,
    )
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    request: Mapped[DocumentRequest] = relationship(back_populates="generated_documents")
    template: Mapped[DocumentTemplate] = relationship()
