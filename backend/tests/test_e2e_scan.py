"""End to end (Section 23 demo, steps 8-17): real document image -> scan agent OCR ->
station API -> validation result. Needs Tesseract and the scan-agent package installed.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

pytest.importorskip("cv2")
pytest.importorskip("pypdfium2")
pytest.importorskip("pytesseract")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pypdfium2  # noqa: E402

from app.models import (  # noqa: E402
    DocumentRequest,
    DocumentTemplate,
    GeneratedDocument,
    ScanSession,
    ScanStation,
    Student,
    User,
)
from app.models.enums import (  # noqa: E402
    DocumentType,
    GeneratedDocumentStatus,
    RequestStatus,
    ScanMode,
    TemplateStatus,
)
from app.security import hash_token  # noqa: E402
from app.seed import seed  # noqa: E402

try:
    from scan_agent.capture.base import CapturedPage
    from scan_agent.client import BackendClient
    from scan_agent.config import tesseract_cmd
    from scan_agent.pipeline import process_stack

    tesseract_cmd()
except (ImportError, RuntimeError) as e:  # agent not installed or no Tesseract
    pytest.skip(f"scan agent unavailable: {e}", allow_module_level=True)

FIXTURES = Path(__file__).parents[2] / "scan-agent" / "tests" / "fixtures"
STATION_KEY = "e2e-station-key"


def render(name: str) -> list[np.ndarray]:
    return [np.array(p.render(scale=300 / 72).to_pil().convert("L"))
            for p in pypdfium2.PdfDocument(str(FIXTURES / name))]


def overprint_grade(image: np.ndarray) -> np.ndarray:
    """Cover IT101's printed 1.25 and print 1.00 over it (UC6)."""
    image = image.copy()
    mm = 300 / 25.4
    row_y = 84 + 7.4 + 6.4 + 2 * 7.4
    x0, x1 = int(148 * mm), int(164 * mm)
    y0, y1 = int((row_y + 1.2) * mm), int((row_y + 6.0) * mm)
    image[y0:y1, x0:x1] = 255
    cv2.putText(image, "1.00", (x0 + 40, y1 - 12), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    return image


@pytest.fixture
def station(db, client):
    """Recreate in this DB the TOR that CI issued for the fixture, plus a station + session."""
    seed(db)
    fixture = json.loads((FIXTURES / "TOR-2026-000001.json").read_text(encoding="utf-8"))
    maria = db.scalar(select(Student).where(Student.student_number == "2025-00001"))
    template = db.scalar(select(DocumentTemplate).where(
        DocumentTemplate.document_type == DocumentType.TOR,
        DocumentTemplate.status == TemplateStatus.ACTIVE))
    request = DocumentRequest(reference_no="REQ-E2E", student_id=maria.id,
                              document_type=DocumentType.TOR, purpose="Employment",
                              status=RequestStatus.RELEASED)
    db.add(request)
    db.flush()
    db.add(GeneratedDocument(
        document_number=fixture["snapshot"]["document_number"], request_id=request.id,
        template_id=template.id, data_snapshot=fixture["snapshot"],
        data_hash=fixture["data_hash"], qr_token=fixture["qr_token"],
        page_count=fixture["pages"], status=GeneratedDocumentStatus.RELEASED,
    ))
    registrar = db.scalar(select(User).where(User.email == "registrar@school.edu.ph"))
    scan_station = ScanStation(name="E2E", api_key_hash=hash_token(STATION_KEY))
    db.add(scan_station)
    db.flush()
    db.add(ScanSession(mode=ScanMode.BULK, operator_id=registrar.id,
                       station_id=scan_station.id))
    db.commit()
    return BackendClient(station_key=STATION_KEY, http=client)


def run(agent: BackendClient, images: list[np.ndarray]) -> list[dict]:
    session = agent.session()
    pages = [CapturedPage(sequence=i, image=img) for i, img in enumerate(images, start=1)]
    return [agent.submit(session["session_id"], doc)
            for doc in process_stack(pages, agent.lookup)]


def test_genuine_printout_is_valid(station):
    [result] = run(station, render("TOR-2026-000001.pdf"))
    assert result["result"] == "VALID", result["reasons"]


def test_bulk_stack_genuine_tampered_fake(station):
    """Demo step 17: genuine, altered, and a document this system never issued."""
    genuine = render("TOR-2026-000001.pdf")
    tampered = [overprint_grade(genuine[0])]
    fake = render("TOR-SAMPLE-LONG.pdf")  # QR token and number exist nowhere in the DB
    results = run(station, genuine + tampered + fake)

    assert [r["position"] for r in results] == [1, 2, 3]
    assert results[0]["result"] == "VALID", results[0]["reasons"]
    assert results[1]["result"] == "INVALID", results[1]
    assert any("IT101" in reason for reason in results[1]["reasons"]), results[1]
    assert results[2]["result"] == "INVALID"
    assert "not issued by this institution" in results[2]["reasons"][0]
