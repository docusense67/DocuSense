"""Synthetic OCR test set: N students with varied names and records, their TOR/COE PDFs
generated exactly as in production, and the expected values for scan_agent.evaluate.

    python scripts/make_ocr_set.py OUT_DIR [N]      (needs WeasyPrint: Linux/Docker/CI)

Writes OUT_DIR/pdfs/NNN-<document number>.pdf and OUT_DIR/expected/<document number>.json.
The data is fictional. This is a pre-check before the real-paper run on the station
(Section 21 asks for 100+ real scans); it finds weak fields early, it does not replace it.
"""

import json
import os
import random
import shutil
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ADGVS_STORAGE_DIR", tempfile.mkdtemp(prefix="ocr-set-"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.documents.service import generate_document  # noqa: E402
from app.models import (  # noqa: E402
    ClassSection,
    DocumentRequest,
    Enrollment,
    Grade,
    Program,
    Student,
    Subject,
    Term,
)
from app.models.enums import DocumentType, GradeRemark, Semester  # noqa: E402
from app.seed import seed  # noqa: E402

FIRST = ["Maria", "Jose", "Juan Carlos", "Ma. Cristina", "Niño", "Angelica", "Mark Anthony",
         "Kristine Joy", "Rafael", "Lourdes", "John Paul", "Rhea Mae", "Emmanuel", "Precious",
         "Joshua", "Patricia Ann", "Nathaniel", "Divina Gracia", "Paolo", "Hazel"]
MIDDLE = ["Reyes", "Dizon", "Lopez", "Bautista", "Aquino", "Mendoza", "Castillo", "Villanueva",
          "Peña", "Ramos", None]
LAST = ["Santos", "Dela Cruz", "Garcia", "Peñaflor", "Dimaculangan", "Macapagal",
        "De los Santos", "Ocampo", "Navarro", "Tolentino", "Ibañez", "Sy", "Buenaventura",
        "Del Rosario", "Lim"]
SUFFIX = ["Jr.", "III", "Sr."]
PROGRAMS = [("BSCS", "Bachelor of Science in Computer Science"),
            ("BSED-ENG", "Bachelor of Secondary Education major in English"),
            ("BSA", "Bachelor of Science in Accountancy")]
PURPOSES = ["Employment", "Scholarship application", "Board examination",
            "Transfer to another school", "Visa application", "Personal copy"]
# Earlier completed terms, each with its own subjects (code, title, units).
EXTRA_TERMS = [
    ("2023-2024", Semester.FIRST, [("GE104", "Readings in Philippine History", "3"),
                                   ("FIL101", "Kontekstwalisadong Komunikasyon sa Filipino", "3"),
                                   ("NSTP1", "National Service Training Program 1", "3"),
                                   ("MATH101", "College Algebra", "3"),
                                   ("PE102", "Rhythmic Activities", "2"),
                                   ("CS101", "Discrete Structures 1", "3")]),
    ("2023-2024", Semester.SECOND, [("GE105", "The Contemporary World", "3"),
                                    ("NSTP2", "National Service Training Program 2", "3"),
                                    ("CS102", "Object-Oriented Programming", "3"),
                                    ("MATH102", "Plane Trigonometry", "3"),
                                    ("PE103", "Individual and Dual Sports", "2")]),
    ("2024-2025", Semester.FIRST, [("GE106", "Art Appreciation", "3"),
                                   ("CS201", "Data Communications and Networking", "3"),
                                   ("CS202", "Information Management", "3"),
                                   ("STAT101", "Probability and Statistics", "3"),
                                   ("PE104", "Team Sports", "2"),
                                   ("ELEC1", "Human-Computer Interaction", "3")]),
    ("2024-2025", Semester.SECOND, [("GE107", "Science, Technology and Society", "3"),
                                    ("CS203", "Operating Systems", "3"),
                                    ("CS204", "Software Engineering 1", "3"),
                                    ("RIZAL", "Life and Works of Rizal", "3"),
                                    ("ELEC2", "Web Systems and Technologies", "3")]),
]
SCALE = ["1.00", "1.25", "1.50", "1.75", "2.00", "2.25", "2.50", "2.75", "3.00", "5.00"]
SCALE_WEIGHTS = [6, 10, 14, 16, 16, 14, 10, 6, 5, 1]


def build(db, n: int, rng: random.Random) -> list[DocumentRequest]:
    seed(db)
    programs = list(db.scalars(select(Program)))
    for code, name in PROGRAMS:
        program = Program(code=code, name=name, department="College of Arts and Sciences")
        db.add(program)
        programs.append(program)
    db.flush()

    # Completed terms, oldest first: the extra ones, then the seed's locked 1st sem 2025-2026.
    terms: list[list[ClassSection]] = []
    for year, semester, subjects in EXTRA_TERMS:
        term = Term(academic_year=year, semester=semester, is_grades_locked=True)
        db.add(term)
        db.flush()
        sections = []
        for code, title, units in subjects:
            subject = Subject(code=code, title=title, units=Decimal(units))
            db.add(subject)
            db.flush()
            section = ClassSection(subject_id=subject.id, term_id=term.id, section_code="A")
            db.add(section)
            sections.append(section)
        db.flush()
        terms.append(sections)
    seed_locked, current = [], []
    for section in db.scalars(select(ClassSection).join(Term)
                              .where(Term.academic_year == "2025-2026")):
        (seed_locked if section.term.is_grades_locked else current).append(section)
    terms.append(seed_locked)

    requests = []
    for i in range(n):
        student = Student(
            student_number=f"{rng.choice([2021, 2022, 2023, 2024])}-{rng.randint(1, 99999):05d}",
            first_name=rng.choice(FIRST), middle_name=rng.choice(MIDDLE),
            last_name=rng.choice(LAST), suffix=rng.choice(SUFFIX) if rng.random() < 0.08 else None,
            program_id=rng.choice(programs).id, year_level=rng.randint(1, 4))
        if db.scalar(select(Student.id).where(Student.student_number == student.student_number)):
            student.student_number = f"2020-{i:05d}"
        db.add(student)
        db.flush()
        completed = terms[-rng.randint(1, len(terms)):]
        for section in [s for t in completed for s in t] + current:
            enrollment = Enrollment(student_id=student.id, class_section_id=section.id)
            db.add(enrollment)
            db.flush()
            if rng.random() < 0.02:
                value, remark = None, GradeRemark.INC
            else:
                value = Decimal(rng.choices(SCALE, SCALE_WEIGHTS)[0])
                remark = GradeRemark.PASSED if value <= Decimal("3.00") else GradeRemark.FAILED
            db.add(Grade(enrollment_id=enrollment.id, grade_value=value, remarks=remark,
                         is_locked=section not in current))
        doc_type = DocumentType.TOR if i % 5 < 2 else DocumentType.COE  # 40% TOR, 60% COE
        request = DocumentRequest(reference_no=f"REQ-OCR-{i:04d}", student_id=student.id,
                                  document_type=doc_type, purpose=rng.choice(PURPOSES))
        db.add(request)
        db.flush()
        requests.append(request)
    return requests


def main(out_dir: str, n: int) -> None:
    out = Path(out_dir)
    for sub in ("pdfs", "expected"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    rng = random.Random(20261005)

    pages = {"TOR": 0, "COE": 0}
    for i, request in enumerate(build(db, n, rng)):
        doc = generate_document(db, request, actor_id=None, issued_on=date(2026, 10, 5))
        shutil.copy(Path(get_settings().storage_dir) / doc.pdf_path,
                    out / "pdfs" / f"{i:03d}-{doc.document_number}.pdf")
        (out / "expected" / f"{doc.document_number}.json").write_text(json.dumps(
            {"snapshot": doc.data_snapshot, "pages": doc.page_count}, ensure_ascii=False,
            indent=1), encoding="utf-8")
        pages[request.document_type.value] += doc.page_count
    db.commit()
    print(f"{n} documents ({pages['TOR']} TOR pages, {pages['COE']} COE pages) in {out}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 100)
