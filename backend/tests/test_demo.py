"""SAMPLE demo data: manage seed-demo / purge-demo (app/demo.py)."""

from contextlib import nullcontext

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import demo, manage
from app.config import get_settings
from app.db import Base, get_db
from app.documents.pdf import RenderedPdf, RendererUnavailable
from app.main import app
from app.models import (
    AuditLog,
    ClassSection,
    DocumentRequest,
    DocumentTemplate,
    Enrollment,
    GeneratedDocument,
    Grade,
    IntakeBatch,
    IntakeDocument,
    Program,
    ScannedDocument,
    ScanSession,
    ScanStation,
    SectionAdviser,
    Student,
    Subject,
    Teacher,
    Term,
    User,
)
from app.models.enums import (
    IntakeDecision,
    IntakeDocType,
    RequestStatus,
    Role,
    ScanMode,
)
from app.security import hash_token
from app.seed import seed as seed_real
from app.storage import get_storage

TABLES = [Program, Subject, Term, ClassSection, SectionAdviser, Teacher, Student, Enrollment,
          Grade, DocumentRequest, GeneratedDocument, DocumentTemplate, ScanStation, ScanSession,
          ScannedDocument, IntakeBatch, IntakeDocument, User]


def fake_render(html: str, left: str, right: str) -> RenderedPdf:
    return RenderedPdf(content=b"%PDF-1.7 " + left.encode(), page_count=1)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Like conftest's, with foreign keys enforced (purge must delete in a valid order)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    app.dependency_overrides[get_db] = lambda: (yield session)
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr("app.documents.service.render_pdf", fake_render)
    monkeypatch.setattr(manage, "SessionLocal", lambda: nullcontext(session))
    yield session
    app.dependency_overrides.clear()
    session.close()


def counts(db) -> dict[str, int]:
    db.expire_all()
    return {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in TABLES}


def run_seed(capsys, *options) -> tuple[int, str]:
    code = manage.main(["seed-demo", *options])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def passwords(path) -> dict[str, str]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        for i, part in enumerate(parts):
            if "@" in part and i + 1 < len(parts):
                rows[part] = parts[i + 1]
    return rows


def login(email: str, password: str) -> int:
    from fastapi.testclient import TestClient
    client = TestClient(app, base_url="https://testserver")
    return client.post("/api/auth/login", json={"email": email, "password": password}).status_code


def request_of(db, number: str) -> DocumentRequest:
    student = db.scalar(select(Student).where(Student.student_number == number))
    return db.scalar(select(DocumentRequest).where(DocumentRequest.student_id == student.id))


# ---- seed-demo ------------------------------------------------------------------------------

def test_seed_creates_a_coherent_sample_dataset(db, capsys, tmp_path):
    manage.main(["install-templates"])
    secrets_file = tmp_path / "demo-passwords.txt"
    code, out = run_seed(capsys, "--password-file", str(secrets_file))
    assert code == 0, out
    db.expire_all()

    assert [p.code for p in db.scalars(select(Program))] == ["SAMPLE-BSIT"]
    assert db.scalar(select(func.count()).select_from(Subject)) == 8
    first, second = db.scalars(select(Term).order_by(Term.academic_year)).all()
    assert first.is_grades_locked and not first.is_current
    assert second.is_current and not second.is_grades_locked
    assert db.scalar(select(func.count()).select_from(ClassSection)) == 8
    adviser = db.scalar(select(SectionAdviser))
    assert adviser.section_code == "SAMPLE-2A" and adviser.term_id == second.id
    students = db.scalars(select(Student)).all()
    assert len(students) == 4 and all(s.last_name == "SAMPLE" for s in students)
    assert db.scalar(select(func.count()).select_from(Enrollment)) == 32
    grades = db.scalars(select(Grade)).all()
    assert len(grades) == 16 + 8 and sum(g.is_locked for g in grades) == 16

    registrar = db.scalar(select(User).where(User.email == demo.REGISTRAR_EMAIL))
    assert registrar.role == Role.REGISTRAR and registrar.can_validate
    users = db.scalars(select(User)).all()
    assert len(users) == 7 and all(u.email.endswith("@demo.school.edu.ph") for u in users)
    assert not any(u.must_change_password for u in users)
    assert db.scalar(select(ScanStation)).name == demo.STATION_NAME

    assert request_of(db, "9999-00001").status == RequestStatus.RELEASED
    assert request_of(db, "9999-00002").status == RequestStatus.FOR_VERIFICATION
    assert request_of(db, "9999-00004").status == RequestStatus.REQUESTED
    rejected = request_of(db, "9999-00003")
    assert rejected.status == RequestStatus.REJECTED
    assert "Incomplete grade" in rejected.reject_reason
    documents = db.scalars(select(GeneratedDocument)).all()
    assert len(documents) == 2
    assert all(get_storage().get(d.pdf_path).startswith(b"%PDF") for d in documents)

    # Real audit trail, through the app's routes, plus the console-created accounts.
    actions = set(db.scalars(select(AuditLog.action)))
    assert {"user_created", "program_created", "term_locked", "grade_encoded",
            "request_submitted", "request_approved", "document_generated",
            "document_released", "section_adviser_assigned", "station_registered",
            "demo_data_seeded"} <= actions

    # Passwords: only in the file, one per account, each one works; plus the station key.
    creds = passwords(secrets_file)
    assert len(creds) == 7 and not any(p in out for p in creds.values())
    assert all(login(email, password) == 200 for email, password in creds.items())
    station_key = secrets_file.read_text(encoding="utf-8").splitlines()[-1].split()[-1]
    assert db.scalar(select(ScanStation)).api_key_hash == hash_token(station_key)


def test_seed_is_idempotent(db, capsys):
    manage.main(["install-templates"])
    code, first_out = run_seed(capsys)
    assert code == 0 and "Registrar (validation operator)" in first_out
    before = counts(db)
    hashes = {u.email: u.password_hash for u in db.scalars(select(User))}

    code, out = run_seed(capsys)
    assert code == 0
    assert "already present; nothing changed" in out and "Password" not in out
    assert counts(db) == before
    assert {u.email: u.password_hash for u in db.scalars(select(User))} == hashes


def test_seed_follows_the_schools_payment_and_manual_release_settings(db, capsys,
                                                                       monkeypatch):
    monkeypatch.setattr(get_settings(), "payment_required", True)
    monkeypatch.setattr(get_settings(), "auto_release_on_approval", False)
    manage.main(["install-templates"])
    code, out = run_seed(capsys)
    assert code == 0, out
    released = request_of(db, "9999-00001")
    assert released.status == RequestStatus.RELEASED and released.payment_confirmed


def test_reset_passwords_issues_new_ones(db, capsys, tmp_path):
    manage.main(["install-templates"])
    run_seed(capsys)
    secrets_file = tmp_path / "again.txt"
    code, _ = run_seed(capsys, "--reset-passwords", "--password-file", str(secrets_file))
    assert code == 0
    creds = passwords(secrets_file)
    assert len(creds) == 7
    assert all(login(email, password) == 200 for email, password in creds.items())


def test_without_a_pdf_renderer_requests_wait_and_a_later_run_finishes_them(db, capsys,
                                                                             monkeypatch):
    manage.main(["install-templates"])

    def unavailable(*args):
        raise RendererUnavailable("no WeasyPrint")

    monkeypatch.setattr("app.documents.service.render_pdf", unavailable)
    code, out = run_seed(capsys)
    assert code == 0 and "stay Pending" in out
    assert request_of(db, "9999-00001").status == RequestStatus.REQUESTED
    assert db.scalar(select(func.count()).select_from(GeneratedDocument)) == 0

    monkeypatch.setattr("app.documents.service.render_pdf", fake_render)
    code, out = run_seed(capsys)  # e.g. with --api on the deployed server
    assert code == 0, out
    db.expire_all()
    assert request_of(db, "9999-00001").status == RequestStatus.RELEASED
    assert request_of(db, "9999-00002").status == RequestStatus.FOR_VERIFICATION
    # The registrar's password was unknown to this run: a new one is issued and printed.
    assert demo.REGISTRAR_EMAIL in out


# ---- purge-demo -----------------------------------------------------------------------------

def add_intake(db, station: ScanStation, key: str) -> IntakeDocument:
    batch = IntakeBatch(station_id=station.id, total_docs=1)
    db.add(batch)
    db.flush()
    doc = IntakeDocument(batch_id=batch.id, station_id=station.id, sequence=1,
                         doc_type=IntakeDocType.PSA_BIRTH_CERTIFICATE, screening_result={},
                         decision=IntakeDecision.PASSED_SCREENING, reasons=[], assigned_tray=1,
                         image_key=key)
    db.add(doc)
    get_storage().put(key, b"\xff\xd8\xff", "image/jpeg")
    db.commit()
    return doc


def test_purge_removes_exactly_the_demo_data(db, capsys):
    seed_real(db)  # real-looking non-demo data: BSIT, three students, terms, templates
    pi = ScanStation(name="Pi Station", api_key_hash=hash_token("pi-key"))
    db.add(pi)
    db.commit()
    add_intake(db, pi, "intake/real/1.jpg")
    real = counts(db)
    real_audit = db.scalar(select(func.count()).select_from(AuditLog))

    code, out = run_seed(capsys)
    assert code == 0, out
    # The demo used the existing 2025-2026 2nd term as it is and created 2026-2027 1st.
    assert "already existed" in out
    station = db.scalar(select(ScanStation).where(ScanStation.name == demo.STATION_NAME))
    demo_doc = add_intake(db, station, f"intake/{station.id}/1.jpg")
    andrea = db.scalar(select(Student).where(Student.student_number == "9999-00001"))
    demo_doc.student_id = andrea.id
    registrar = db.scalar(select(User).where(User.email == demo.REGISTRAR_EMAIL))
    session = ScanSession(mode=ScanMode.SINGLE, operator_id=registrar.id, station_id=pi.id)
    db.add(session)
    db.flush()
    db.add(ScannedDocument(session_id=session.id, image_paths=["scans/demo/page-1.jpg"]))
    get_storage().put("scans/demo/page-1.jpg", b"\xff\xd8\xff", "image/jpeg")
    db.commit()
    demo_files = [d.pdf_path for d in db.scalars(select(GeneratedDocument))
                  if d.pdf_path] + [demo_doc.image_key, "scans/demo/page-1.jpg"]
    audit_before_purge = db.scalar(select(func.count()).select_from(AuditLog))

    assert manage.main(["purge-demo", "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert db.scalar(select(func.count()).select_from(Student)) == real["students"] + 4

    assert manage.main(["purge-demo", "--yes"]) == 0, capsys.readouterr()
    after = counts(db)
    demo_users = db.scalars(select(User).where(User.email.like("%@demo.school.edu.ph"))).all()
    assert len(demo_users) == 7
    assert all(u.email.startswith("purged-") and not u.is_active for u in demo_users)
    assert after == {**real, "users": real["users"] + 7}
    assert db.scalar(select(Term).where(Term.academic_year == "2025-2026",
                                        Term.semester == "2nd")) is not None
    assert db.scalar(select(Term).where(Term.academic_year == "2026-2027")) is None
    for key in demo_files:
        with pytest.raises(LookupError):
            get_storage().get(key)
    assert get_storage().get("intake/real/1.jpg") == b"\xff\xd8\xff"
    # Audit entries are never deleted; the purge itself is audited.
    audit_after = db.scalar(select(func.count()).select_from(AuditLog))
    assert audit_after > audit_before_purge > real_audit
    assert db.scalar(select(AuditLog).where(AuditLog.action == "demo_data_purged")) is not None

    # A second purge finds nothing; seeding again starts fresh with new accounts.
    assert manage.main(["purge-demo", "--yes"]) == 0
    assert "nothing" in capsys.readouterr().out
    code, out = run_seed(capsys)
    assert code == 0, out
    assert db.scalar(select(func.count()).select_from(Student)) == real["students"] + 4
    assert db.scalar(select(User).where(User.email == demo.REGISTRAR_EMAIL)).is_active


def test_purge_refuses_when_real_records_depend_on_demo_data(db, capsys):
    seed_real(db)
    manage.main(["install-templates"])
    run_seed(capsys)
    maria = db.scalar(select(Student).where(Student.student_number == "2025-00001"))
    section = db.scalar(select(ClassSection).where(ClassSection.section_code == "SAMPLE-2A"))
    db.add(Enrollment(student_id=maria.id, class_section_id=section.id))
    db.commit()
    before = counts(db)

    assert manage.main(["purge-demo", "--yes"]) == 1
    assert "not SAMPLE" in capsys.readouterr().err
    assert counts(db) == before


def test_purge_include_station_sweeps_its_intake_but_keeps_it(db, capsys):
    run_seed(capsys)
    pi = ScanStation(name="Pi Station", api_key_hash=hash_token("pi-key"))
    db.add(pi)
    db.commit()
    add_intake(db, pi, "intake/pi/1.jpg")

    assert manage.main(["purge-demo", "--yes", "--include-station", "Pi Station"]) == 0
    assert db.scalar(select(func.count()).select_from(IntakeDocument)) == 0
    assert db.scalar(select(ScanStation)).name == "Pi Station"
    assert manage.main(["purge-demo", "--yes", "--include-station", "No Such"]) == 1


def test_purge_refuses_when_stored_files_are_not_in_this_storage(db, capsys, monkeypatch,
                                                                 tmp_path):
    manage.main(["install-templates"])
    run_seed(capsys)
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path / "elsewhere"))
    assert manage.main(["purge-demo", "--yes"]) == 1
    assert "2 of 2 stored file(s) are not in the local folder" in capsys.readouterr().err
    assert db.scalar(select(func.count()).select_from(Student)) == 4
    assert manage.main(["purge-demo", "--yes", "--ignore-missing-files"]) == 0
    assert db.scalar(select(func.count()).select_from(Student)) == 0


def test_purge_asks_for_confirmation(db, capsys, monkeypatch):
    run_seed(capsys)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert manage.main(["purge-demo"]) == 1
    assert db.scalar(select(func.count()).select_from(Student)) == 4


def test_markers():
    assert demo.is_demo_email("Registrar.Sample@DEMO.school.edu.ph")
    assert not demo.is_demo_email("registrar@school.edu.ph")
    assert demo.is_demo_student(Student(student_number="2026-00001", first_name="Juan",
                                        last_name="Sample"))
    assert demo.is_demo_student(Student(student_number="SAMPLE-D001", first_name="Juan",
                                        last_name="Cruz"))
    assert not demo.is_demo_student(Student(student_number="2026-00001", first_name="Juan",
                                            last_name="Samplehouse"))
