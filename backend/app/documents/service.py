"""Template validation and document generation (Sections 5D, 5F, 6, 13)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents import identity
from app.documents.context import BuildOptions
from app.documents.pdf import RenderedPdf, render_pdf
from app.documents.registry import PLACEHOLDER_CATALOG, REGISTRY, institution_context
from app.documents.templating import check_template, render_html
from app.documents.zones import FieldZones
from app.models import DocumentRequest, DocumentTemplate, GeneratedDocument, Student
from app.models.enums import DocumentType, TemplateStatus
from app.services import audit
from app.storage import get_storage

Renderer = Callable[[str, str, str], RenderedPdf]


class GenerationError(Exception):
    pass


@dataclass
class TemplateValidation:
    placeholders: list[str] = field(default_factory=list)
    unknown_placeholders: list[str] = field(default_factory=list)
    missing_placeholders: list[str] = field(default_factory=list)
    missing_zones: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def can_activate(self) -> bool:
        return not (self.unknown_placeholders or self.missing_placeholders
                    or self.missing_zones or self.errors)


def validate_template(doc_type: DocumentType, html: str, zones: dict | None) -> TemplateValidation:
    """Section 13: unknown or missing placeholders, or undefined OCR zones, block activation."""
    spec = REGISTRY[doc_type]
    check = check_template(html, set(PLACEHOLDER_CATALOG), set(spec.required_placeholders))
    result = TemplateValidation(
        placeholders=check.placeholders,
        unknown_placeholders=check.unknown_placeholders,
        missing_placeholders=check.missing_placeholders,
        errors=list(check.errors),
    )
    if zones is None:
        result.missing_zones = sorted(spec.required_zones)
    else:
        try:
            parsed = FieldZones.model_validate(zones)
            result.missing_zones = sorted(spec.required_zones - set(parsed.fields))
        except ValidationError as e:
            result.errors.append(f"Invalid field zones: {e.errors()[0]['msg']}")
    return result


def active_template(db: Session, doc_type: DocumentType) -> DocumentTemplate | None:
    return db.scalar(select(DocumentTemplate).where(
        DocumentTemplate.document_type == doc_type,
        DocumentTemplate.status == TemplateStatus.ACTIVE))


def render_snapshot(template_html: str, snapshot: dict, document_number: str, qr_token: str,
                    renderer: Renderer | None = None) -> RenderedPdf:
    """Render a template with an already-built snapshot (no database writes)."""
    digest = identity.snapshot_hash(snapshot)
    html = render_html(template_html, {
        **snapshot,
        **institution_context(),
        "qr_code": identity.qr_data_uri(identity.qr_payload(qr_token)),
        "data_hash_short": identity.short_hash(digest),
    })
    left, right = identity.footer_text(document_number, digest)
    return (renderer or render_pdf)(html, left, right)


def render_preview(db: Session, template: DocumentTemplate, student: Student | None,
                   renderer: Renderer | None = None) -> RenderedPdf:
    """Sample data by default; a real student's data when given. Nothing is issued."""
    spec = REGISTRY[template.document_type]
    if student is None:
        values = spec.sample_context()
    else:
        values = spec.build_context(db, student, BuildOptions(issued_on=date.today(),
                                                              purpose="Preview"))
    number = f"{template.document_type.value}-PREVIEW-000000"
    snapshot = {**values, "document_number": number}
    return render_snapshot(template.html_content, snapshot, number, "PREVIEW-NOT-VALID", renderer)


def generate_document(
    db: Session,
    request: DocumentRequest,
    *,
    actor_id: int | None,
    issued_on: date | None = None,
    renderer: Renderer | None = None,
) -> GeneratedDocument:
    """Fill the active template, render the PDF, and save the frozen snapshot. Caller commits.

    Raises MissingDataError (from the context builder) or GenerationError; in both cases
    nothing is written, so no document is ever produced with a blank required field.
    """
    issued_on = issued_on or date.today()
    spec = REGISTRY[request.document_type]
    template = active_template(db, request.document_type)
    if template is None:
        raise GenerationError(f"No active {request.document_type.value} template")

    student = db.get(Student, request.student_id)
    values = spec.build_context(db, student,
                                BuildOptions(issued_on=issued_on, purpose=request.purpose))

    number = identity.next_document_number(db, request.document_type, issued_on.year)
    token = identity.new_qr_token()
    snapshot = {**values, "document_type": request.document_type.value,
                "document_number": number}
    pdf = render_snapshot(template.html_content, snapshot, number, token, renderer)

    relative = Path("documents") / f"{number}.pdf"
    get_storage().put(relative.as_posix(), pdf.content, "application/pdf")

    document = GeneratedDocument(
        document_number=number,
        request=request,  # via the relationship, so request.generated_documents stays current
        template_id=template.id,
        data_snapshot=snapshot,
        data_hash=identity.snapshot_hash(snapshot),
        qr_token=token,
        pdf_path=relative.as_posix(),
        page_count=pdf.page_count,
    )
    db.add(document)
    db.flush()
    audit.record(db, "document_generated", user_id=actor_id, entity_type="generated_document",
                 entity_id=document.id,
                 new_value={"document_number": number, "request_id": request.id,
                            "template_id": template.id, "template_version": template.version,
                            "pages": pdf.page_count, "data_hash": document.data_hash})
    return document
