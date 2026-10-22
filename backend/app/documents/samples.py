"""Render sample TOR/COE PDFs from the demo seed, for visual review and OCR zone tuning.

Run:  python -m app.documents.samples OUT_DIR
Needs WeasyPrint system libraries (Docker/Linux). CI uploads OUT_DIR as an artifact.
"""

import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.documents.registry import REGISTRY
from app.documents.service import active_template, generate_document, render_snapshot
from app.models import DocumentRequest, Student
from app.models.enums import DocumentType
from app.seed import seed
from app.storage import get_storage


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    seed(db)

    maria = db.scalar(select(Student).where(Student.student_number == "2025-00001"))
    for doc_type in (DocumentType.TOR, DocumentType.COE):
        request = DocumentRequest(reference_no=f"REQ-SAMPLE-{doc_type.value}",
                                  student_id=maria.id, document_type=doc_type,
                                  purpose="Employment")
        db.add(request)
        db.flush()
        document = generate_document(db, request, actor_id=None, issued_on=date(2026, 10, 5))
        content = get_storage().get(document.pdf_path)
        (out / f"{document.document_number}.pdf").write_bytes(content)
        (out / f"{document.document_number}.json").write_text(json.dumps({
            "pages": document.page_count, "data_hash": document.data_hash,
            "qr_token": document.qr_token, "snapshot": document.data_snapshot,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{document.document_number}: {document.page_count} page(s)")

    # Synthetic 8-term TOR to check page breaks, repeated table headers and page markers.
    long_tor = REGISTRY[DocumentType.TOR].sample_context()
    subjects = long_tor["terms"][0]["subjects"] * 2
    long_tor["terms"] = [
        {**long_tor["terms"][0], "academic_year": f"{2022 + i // 2}-{2023 + i // 2}",
         "semester": ("1st Semester", "2nd Semester")[i % 2], "subjects": subjects}
        for i in range(8)
    ]
    number = "TOR-SAMPLE-LONG"
    pdf = render_snapshot(active_template(db, DocumentType.TOR).html_content,
                          {**long_tor, "document_number": number}, number, "SAMPLE")
    (out / f"{number}.pdf").write_bytes(pdf.content)
    print(f"{number}: {pdf.page_count} page(s)")
    db.commit()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample-pdfs")
