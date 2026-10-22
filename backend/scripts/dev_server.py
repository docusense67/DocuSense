"""DEV ONLY: run the local API with fictional demo records and real PDF rendering.
UI-only fixture rendering is available explicitly on machines without WeasyPrint.
Never used in production; the app never imports this file.

    cd backend && .venv/Scripts/python scripts/dev_server.py

- Demo accounts use password "ChangeMe-2026" with the forced change switched off.
- PDFs render from the requested record using WeasyPrint by default.
- ADGVS_DEMO_FIXTURE_PDFS=true explicitly enables fixed fixture PDFs for UI-only tests.
- Maria's TOR from the CI sample is pre-issued, so scanning that PDF comes back VALID.
- Station "Dev Station" uses API key "dev-station-key".
"""

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT.parent / "scan-agent" / "tests" / "fixtures"
os.environ.setdefault("ADGVS_DATABASE_URL", f"sqlite:///{ROOT / 'dev.db'}")
os.environ.setdefault("ADGVS_STORAGE_DIR", str(ROOT / "storage-dev"))
os.environ.setdefault("ADGVS_COOKIE_SECURE", "false")
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.documents import service  # noqa: E402
from app.documents.pdf import RenderedPdf  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    DocumentRequest,
    DocumentTemplate,
    GeneratedDocument,
    ScanStation,
    Student,
    User,
)
from app.models.enums import (  # noqa: E402
    DocumentType,
    GeneratedDocumentStatus,
    RequestStatus,
    TemplateStatus,
)
from app.security import hash_token  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services import audit  # noqa: E402
from app.storage import get_storage  # noqa: E402
from app.timeutil import utcnow  # noqa: E402


def fake_render(html: str, footer_left: str, footer_right: str) -> RenderedPdf:
    name = "COE-2026-000001.pdf" if footer_left.startswith("COE") else "TOR-2026-000001.pdf"
    return RenderedPdf((FIXTURES / name).read_bytes(), page_count=1)


def prepare() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        for user in db.scalars(select(User)):
            user.must_change_password = False
        fixture = json.loads((FIXTURES / "TOR-2026-000001.json").read_text(encoding="utf-8"))
        number = fixture["snapshot"]["document_number"]
        existing = select(GeneratedDocument).where(GeneratedDocument.document_number == number)
        demo_document = db.scalar(existing)
        if demo_document is None:
            maria = db.scalar(select(Student).where(Student.student_number == "2025-00001"))
            template = db.scalar(select(DocumentTemplate).where(
                DocumentTemplate.document_type == DocumentType.TOR,
                DocumentTemplate.status == TemplateStatus.ACTIVE))
            request = DocumentRequest(reference_no="REQ-DEV-000001", student_id=maria.id,
                                      document_type=DocumentType.TOR, purpose="Employment",
                                      status=RequestStatus.RELEASED)
            db.add(request)
            db.flush()
            demo_document = GeneratedDocument(
                document_number=number, request_id=request.id, template_id=template.id,
                data_snapshot=fixture["snapshot"], data_hash=fixture["data_hash"],
                qr_token=fixture["qr_token"], page_count=1,
                status=GeneratedDocumentStatus.RELEASED, pdf_path=None,
                released_at=utcnow(), download_expires_at=utcnow() + timedelta(days=30))
            db.add(demo_document)
            db.flush()
        # Repair only the known pre-issued fixture, never a user's generated document.
        if (not demo_document.pdf_path and demo_document.qr_token == fixture['qr_token']
                and demo_document.data_hash == fixture['data_hash']):
            relative = f'documents/{number}.pdf'
            get_storage().put(relative, (FIXTURES / f'{number}.pdf').read_bytes(),
                              'application/pdf')
            demo_document.pdf_path = relative
            audit.record(db, 'demo_document_file_restored', entity_type='generated_document',
                         entity_id=demo_document.id, new_value={'document_number': number})
        if db.scalar(select(ScanStation).where(ScanStation.name == "Dev Station")) is None:
            db.add(ScanStation(name="Dev Station", api_key_hash=hash_token("dev-station-key")))
        db.commit()


def configure_renderer():
    from app.documents.pdf import render_pdf

    service.render_pdf = render_pdf
    if os.environ.get('ADGVS_DEMO_FIXTURE_PDFS', '').lower() == 'true':
        service.render_pdf = fake_render
        print('UI TEST MODE: fixed fixture PDFs; do not use for generated-document validation.')


if __name__ == "__main__":
    configure_renderer()
    prepare()
    print("DEV server: demo password 'ChangeMe-2026'; station key 'dev-station-key'")
    uvicorn.run(app, host="127.0.0.1", port=8000)
