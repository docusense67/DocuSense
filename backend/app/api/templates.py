"""Document template management (Section 5D, 13). Registrar only."""

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_roles
from app.db import get_db
from app.documents.context import MissingDataError
from app.documents.pdf import RendererUnavailable
from app.documents.registry import PLACEHOLDER_CATALOG, REGISTRY
from app.documents.service import render_preview, validate_template
from app.documents.templating import TemplateRenderError
from app.models import DocumentTemplate, Student, User
from app.models.enums import DocumentType, Role, TemplateStatus
from app.services import audit

router = APIRouter(tags=["templates"])
registrar = require_roles(Role.REGISTRAR)

MAX_TEMPLATE_CHARS = 500_000


class TemplateIn(BaseModel):
    document_type: DocumentType
    html_content: str = Field(min_length=1, max_length=MAX_TEMPLATE_CHARS)
    field_zones: dict | None = None


class TemplateUpdateIn(BaseModel):
    html_content: str | None = Field(default=None, min_length=1, max_length=MAX_TEMPLATE_CHARS)
    field_zones: dict | None = None


class ValidationOut(BaseModel):
    placeholders: list[str]
    unknown_placeholders: list[str]
    missing_placeholders: list[str]
    missing_zones: list[str]
    errors: list[str]
    can_activate: bool


class TemplateOut(BaseModel):
    id: int
    document_type: DocumentType
    version: int
    status: TemplateStatus
    html_content: str
    field_zones: dict | None
    created_at: datetime
    validation: ValidationOut


def _out(template: DocumentTemplate) -> TemplateOut:
    check = validate_template(template.document_type, template.html_content,
                              template.field_zones)
    return TemplateOut(
        id=template.id, document_type=template.document_type, version=template.version,
        status=template.status, html_content=template.html_content,
        field_zones=template.field_zones, created_at=template.created_at,
        validation=ValidationOut(**asdict(check), can_activate=check.can_activate),
    )


def _draft(db: Session, template_id: int) -> DocumentTemplate:
    template = get_or_404(db, DocumentTemplate, template_id)
    if template.status != TemplateStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only draft templates can change; upload a new version instead")
    return template


@router.get("/document-types")
def document_types(_: User = Depends(registrar)):
    """What a template author needs: required placeholders and zones per type, plus the catalog."""
    return {
        "placeholder_catalog": PLACEHOLDER_CATALOG,
        "types": [
            {"code": spec.code, "label": spec.label,
             "required_placeholders": sorted(spec.required_placeholders),
             "required_zones": sorted(spec.required_zones)}
            for spec in REGISTRY.values()
        ],
    }


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(document_type: DocumentType | None = None, _: User = Depends(registrar),
                   db: Session = Depends(get_db)):
    query = select(DocumentTemplate).order_by(DocumentTemplate.document_type,
                                              DocumentTemplate.version.desc())
    if document_type is not None:
        query = query.where(DocumentTemplate.document_type == document_type)
    return [_out(t) for t in db.scalars(query)]


@router.get("/templates/{template_id}", response_model=TemplateOut)
def get_template(template_id: int, _: User = Depends(registrar), db: Session = Depends(get_db)):
    return _out(get_or_404(db, DocumentTemplate, template_id))


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def upload_template(body: TemplateIn, request: Request, actor: User = Depends(registrar),
                    db: Session = Depends(get_db)):
    """Creates the next draft version. Validation results come back with it."""
    latest = db.scalar(select(func.max(DocumentTemplate.version))
                       .where(DocumentTemplate.document_type == body.document_type))
    check = validate_template(body.document_type, body.html_content, body.field_zones)
    template = DocumentTemplate(
        document_type=body.document_type, version=(latest or 0) + 1,
        html_content=body.html_content, placeholder_list=check.placeholders,
        field_zones=body.field_zones, status=TemplateStatus.DRAFT, created_by=actor.id,
    )
    db.add(template)
    db.flush()
    audit.record(db, "template_uploaded", user_id=actor.id, entity_type="document_template",
                 entity_id=template.id,
                 new_value={"document_type": body.document_type, "version": template.version,
                            "can_activate": check.can_activate},
                 ip_address=client_ip(request))
    db.commit()
    return _out(template)


@router.patch("/templates/{template_id}", response_model=TemplateOut)
def update_draft(template_id: int, body: TemplateUpdateIn, request: Request,
                 actor: User = Depends(registrar), db: Session = Depends(get_db)):
    template = _draft(db, template_id)
    changes = body.model_dump(exclude_unset=True)
    if "html_content" in changes:
        template.html_content = body.html_content
    if "field_zones" in changes:
        template.field_zones = body.field_zones
    template.placeholder_list = validate_template(
        template.document_type, template.html_content, template.field_zones).placeholders
    audit.record(db, "template_updated", user_id=actor.id, entity_type="document_template",
                 entity_id=template.id, new_value={"changed": sorted(changes)},
                 ip_address=client_ip(request))
    db.commit()
    return _out(template)


@router.post("/templates/{template_id}/preview")
def preview_template(template_id: int, request: Request, student_id: int | None = None,
                     actor: User = Depends(registrar), db: Session = Depends(get_db)):
    """Render a PDF preview with sample data (or a real student's data). Nothing is issued."""
    template = get_or_404(db, DocumentTemplate, template_id)
    check = validate_template(template.document_type, template.html_content,
                              template.field_zones)
    if check.errors or check.unknown_placeholders:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            {"message": "Fix the template before previewing",
                             "errors": check.errors,
                             "unknown_placeholders": check.unknown_placeholders})
    student = get_or_404(db, Student, student_id) if student_id is not None else None
    try:
        pdf = render_preview(db, template, student)
    except MissingDataError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            {"message": "Student data incomplete", "missing": e.missing}) from None
    except TemplateRenderError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Render error: {e}") from None
    except RendererUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from None

    audit.record(db, "template_previewed", user_id=actor.id, entity_type="document_template",
                 entity_id=template.id, new_value={"student_id": student_id},
                 ip_address=client_ip(request))
    db.commit()
    return Response(pdf.content, media_type="application/pdf",
                    headers={"X-Page-Count": str(pdf.page_count),
                             "Content-Disposition": "inline; filename=preview.pdf"})


@router.post("/templates/{template_id}/activate", response_model=TemplateOut)
def activate_template(template_id: int, request: Request, actor: User = Depends(registrar),
                      db: Session = Depends(get_db)):
    """Make this version the one used for new documents; the previous one is retired.
    Documents already issued keep the version (and OCR zones) they were printed with."""
    template = _draft(db, template_id)
    check = validate_template(template.document_type, template.html_content,
                              template.field_zones)
    if not check.can_activate:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"message": "Template cannot be activated", **asdict(check)})

    previous = db.scalars(select(DocumentTemplate).where(
        DocumentTemplate.document_type == template.document_type,
        DocumentTemplate.status == TemplateStatus.ACTIVE)).all()
    for old in previous:
        old.status = TemplateStatus.RETIRED
        audit.record(db, "template_retired", user_id=actor.id, entity_type="document_template",
                     entity_id=old.id, ip_address=client_ip(request))
    template.status = TemplateStatus.ACTIVE
    audit.record(db, "template_activated", user_id=actor.id, entity_type="document_template",
                 entity_id=template.id,
                 new_value={"document_type": template.document_type,
                            "version": template.version},
                 ip_address=client_ip(request))
    db.commit()
    return _out(template)
