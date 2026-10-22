"""SAMPLE demo data for progress videos and client demos. Never real records.

    python -m app.manage seed-demo  [--api URL] [--password-file PATH] [--reset-passwords]
    python -m app.manage purge-demo [--dry-run] [--yes] [--include-station NAME]

seed-demo builds a small, coherent dataset by driving the API's own routes (so every rule,
audit entry, document number and hash is the real one): a SAMPLE program with eight subjects
over two terms (the older term locked), two teachers (one is the class adviser of a block), a
registrar with the validation permission, four students with grades, document requests in
different states, and an intake station for `python -m scan_agent intake-demo`. Only the
registrar account and the station are created directly (an administrator would make them in
the web app; the console has no administrator login), audited "via manage seed-demo".

Requests are driven by the HTTP client given by `--api URL` (the deployed API, which renders
PDFs) or, by default, by the API in this process. A machine that cannot render PDFs
(WeasyPrint missing, e.g. Windows) leaves the requests that need a document Pending and says
so; re-run with `--api` to finish them. Re-running is safe: whatever exists is kept, only what
is missing is added. Passwords are random, shown once (or written to --password-file); demo
accounts already exist on a re-run keep theirs unless --reset-passwords.

Markers (purge-demo refuses anything without one):
    accounts   email ending in @demo.school.edu.ph
    people     a name containing the word SAMPLE (students, teachers); student numbers
               starting with SAMPLE also count (profiles created from intake documents)
    catalog    program and subject codes starting with SAMPLE; section codes SAMPLE-...
    stations   name starting with SAMPLE
    terms      created by a demo account (its term_created audit entry) and left empty

purge-demo deletes the demo records and their files (generated PDFs, intake and scan page
images), every intake document and scan session of a demo station (or of a station named
with --include-station), and every request, document and intake document of a demo student.
It aborts without changing anything when a real record depends on demo data (e.g. a real
student enrolled in a demo section). It cannot delete:
  - audit entries: audit_logs is append-only (Section 17/18). Demo entries stay and are
    identifiable by their accounts (@demo.school.edu.ph) and SAMPLE values, between the
    demo_data_seeded and demo_data_purged entries;
  - demo accounts: those audit entries reference them, so they are deactivated, given an
    unusable password and renamed purged-<id>-<name>@demo.school.edu.ph instead;
  - document and request numbers: the next real document may reuse a number printed on a
    demo PDF. Destroy demo printouts; their QR codes no longer match anything.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import time
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    ClassSection,
    DocumentRequest,
    Enrollment,
    GeneratedDocument,
    Grade,
    GradeChangeRequest,
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
    UserSession,
    ValidationRecord,
)
from app.models.enums import DocumentType, RequestStatus, Role
from app.security import hash_password, hash_token, new_token
from app.services import audit
from app.storage import StorageError, StoredFileMissing, get_storage

DEMO_DOMAIN = "demo.school.edu.ph"
MARK = "SAMPLE"
VIA = "manage seed-demo (SAMPLE demo data)"
STATION_NAME = "SAMPLE Demo Intake Station"
REGISTRAR_EMAIL = f"registrar.sample@{DEMO_DOMAIN}"

PROGRAM = {"code": "SAMPLE-BSIT", "name": "SAMPLE Bachelor of Science in Information Technology",
           "department": "SAMPLE College of Computing", "total_units": 150}
# (academic_year, semester, block section code). The first is locked, the second is current.
TERMS = [("2025-2026", "2nd", "SAMPLE-1A"), ("2026-2027", "1st", "SAMPLE-2A")]
# (code, title, units, term index, teacher index). Codes fit the TOR's OCR code pattern.
SUBJECTS = [
    ("SAMPLE101", "Introduction to Computing", "3.0", 0, 0),
    ("SAMPLE102", "Computer Programming 1", "3.0", 0, 0),
    ("SAMPLE103", "Purposive Communication", "3.0", 0, 0),
    ("SAMPLE104", "Physical Fitness", "2.0", 0, 0),
    ("SAMPLE201", "Computer Programming 2", "3.0", 1, 0),
    ("SAMPLE202", "Data Structures and Algorithms", "3.0", 1, 0),
    ("SAMPLE203", "Mathematics in the Modern World", "3.0", 1, 1),
    ("SAMPLE204", "Understanding the Self", "3.0", 1, 1),
]
# (employee number, full name, login email). Teacher 1 is the class adviser of the current block.
TEACHERS = [
    ("EMP-SAMPLE-01", "Prof. Liza SAMPLE Ramos", f"teacher.sample@{DEMO_DOMAIN}"),
    ("EMP-SAMPLE-02", "Prof. Ramon SAMPLE Villar", f"adviser.sample@{DEMO_DOMAIN}"),
]
ADVISER = (1, 1)  # (teacher index, term index)
# Grades per subject in SUBJECTS order; None = not encoded yet (the current term is open, and
# only the subjects of teacher 0 are encoded: the adviser's subjects can be encoded live).
STUDENTS = [
    ("9999-00001", "Andrea", "Cruz", "andrea.sample", "2007-03-14", "Female",
     ["1.25", "1.50", "1.75", "1.00", "1.50", "1.75", None, None]),
    ("9999-00002", "Benjo", "Reyes", "benjo.sample", "2006-11-02", "Male",
     ["2.00", "2.25", "1.75", "1.50", "2.25", "2.50", None, None]),
    # INC in the locked term: a TOR request is rejected by the eligibility rules.
    ("9999-00003", "Carla", "Mendoza", "carla.sample", "2007-06-27", "Female",
     ["1.75", "INC", "2.00", "1.25", "2.00", "1.75", None, None]),
    ("9999-00004", "Dario", "Santos", "dario.sample", "2006-09-19", "Male",
     ["2.50", "2.75", "2.25", "2.00", "2.75", "3.00", None, None]),
]
SUBMITTED = {"SAMPLE201"}  # current-term sections the teacher already submitted
# (student index, document type, purpose, how far to take it)
REQUESTS = [
    (0, DocumentType.TOR, "Employment", RequestStatus.RELEASED),
    (1, DocumentType.COE, "Scholarship application", RequestStatus.FOR_VERIFICATION),
    (3, DocumentType.TOR, "Transfer to another school", RequestStatus.REQUESTED),
    (2, DocumentType.TOR, "Board examination", RequestStatus.REQUESTED),  # auto-rejected (INC)
]

_WORD = re.compile(r"\bsample\b", re.IGNORECASE)


class DemoError(RuntimeError):
    pass


def is_demo_email(email: str) -> bool:
    return email.lower().endswith("@" + DEMO_DOMAIN)


def is_demo_student(s: Student) -> bool:
    names = " ".join(filter(None, (s.first_name, s.middle_name, s.last_name, s.suffix)))
    return bool(_WORD.search(names)) or s.student_number.upper().startswith(MARK)


def is_demo_teacher(t: Teacher) -> bool:
    return bool(_WORD.search(t.full_name)) or MARK in t.employee_number.upper()


def is_demo_station(st: ScanStation) -> bool:
    return st.name.upper().startswith(MARK)


def _password() -> str:
    return secrets.token_urlsafe(12)  # 16 characters


def _fresh(db: Session) -> Session:
    """Drop cached rows: the API (another session, or another server) changed the data."""
    db.commit()
    db.expire_all()
    return db


# =============================================================================================
# seed-demo
# =============================================================================================

class Api:
    """The app's HTTP API, logged in as one demo account per client."""

    def __init__(self, make_client: Callable[[], object], db: Session, out: Callable[[str], None]):
        self.make_client = make_client
        self.db = db
        self.out = out
        self.clients: dict[str, object] = {}
        self.passwords: dict[str, str] = {}  # final passwords set during this run
        self.temporary: dict[str, str] = {}  # logins just created with a temporary password

    def call(self, email: str, method: str, path: str, **kwargs) -> dict | list | None:
        res = self.session(email).request(method, path, **kwargs)
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail")
            except (ValueError, AttributeError):
                detail = res.text[:200]
            raise DemoError(f"{method} {path} as {email}: HTTP {res.status_code} {detail}")
        return res.json() if res.content else None

    def session(self, email: str):
        if email in self.clients:
            return self.clients[email]
        temporary = self.temporary.pop(email, None) or self._new_temporary(email)
        client = self.make_client()
        res = client.post("/api/auth/login", json={"email": email, "password": temporary})
        if res.status_code != 200:
            raise DemoError(f"Login as {email} failed (HTTP {res.status_code}). With --api, the "
                            "API must use the same database as ADGVS_DATABASE_URL.")
        final = _password()
        res = client.post("/api/auth/change-password",
                          json={"current_password": temporary, "new_password": final})
        if res.status_code != 204:
            raise DemoError(f"Password change for {email} failed (HTTP {res.status_code})")
        self.passwords[email] = final
        self.clients[email] = client
        return client

    def _new_temporary(self, email: str) -> str:
        """An existing demo account whose password this run does not know: issue a new one
        (the registrar resets teachers and students; the console resets the registrar)."""
        temporary = _password()
        db = _fresh(self.db)
        user = db.scalar(select(User).where(User.email == email))
        if user is None or not user.is_active:
            raise DemoError(f"Demo account {email} is missing or deactivated")
        if email == REGISTRAR_EMAIL:
            user.password_hash = hash_password(temporary)
            user.must_change_password = True
            user.failed_logins, user.is_locked, user.locked_at = 0, False, None
            audit.record(db, "password_reset", entity_type="user", entity_id=user.id,
                         new_value={"reason": "New password for the SAMPLE demo account",
                                    "via": VIA})
            db.commit()
        else:
            self.call(REGISTRAR_EMAIL, "POST", f"/api/users/{user.id}/reset-password",
                      json={"temporary_password": temporary,
                            "reason": "New password for the SAMPLE demo account"})
        return temporary


def in_process_client():
    """The API in this process (TestClient: no server, same database settings)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from starlette.testclient import TestClient

        from app.main import app
        return TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50000))


def remote_client_factory(url: str) -> Callable[[], object]:
    import httpx

    base = url.rstrip("/")

    def make():
        return httpx.Client(base_url=base, timeout=120.0, follow_redirects=False)

    return make


def wake(make_client: Callable[[], object], out, attempts: int = 12) -> None:
    """Render's free tier sleeps: wait until /api/health answers."""
    import httpx

    client = make_client()
    for attempt in range(attempts):
        try:
            if client.get("/api/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if attempt == 0:
            out("Waiting for the API to wake up...")
        time.sleep(10)
    raise DemoError("The API did not answer /api/health")


def renderer_available() -> bool:
    from app.documents import service
    from app.documents.pdf import RendererUnavailable

    try:
        service.render_pdf("<p>probe</p>", "probe", "probe")
    except RendererUnavailable:
        return False
    return True


@dataclass
class SeedReport:
    created: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, what: str, n: int = 1) -> None:
        if n:
            self.created[what] = self.created.get(what, 0) + n


def _last_purge_id(db: Session) -> int:
    return db.scalar(select(func.max(AuditLog.id)).where(
        AuditLog.action == "demo_data_purged")) or 0


def demo_user_ids(db: Session) -> set[int]:
    return {u.id for u in db.scalars(select(User).where(
        func.lower(User.email).like(f"%@{DEMO_DOMAIN}")))}


def demo_term_ids(db: Session) -> set[int]:
    """Terms a demo account created (term_created audit entries since the last purge)."""
    users = demo_user_ids(db)
    if not users:
        return set()
    rows = db.scalars(select(AuditLog.entity_id).where(
        AuditLog.action == "term_created", AuditLog.user_id.in_(users),
        AuditLog.id > _last_purge_id(db)))
    return {int(r) for r in rows if r and r.isdigit()}


def seed(db: Session, make_client: Callable[[], object], *, can_generate: bool,
         reset_passwords: bool = False, out: Callable[[str], None] = print
         ) -> tuple[SeedReport, dict[str, str], str | None]:
    """Create whatever part of the demo dataset is missing. Returns the report, the demo
    account passwords set in this run, and the station key if one was issued."""
    report = SeedReport()
    api = Api(make_client, db, out)
    reg = REGISTRAR_EMAIL

    # -- registrar (validation operator) and intake station: console-created --------------
    user = db.scalar(select(User).where(User.email == reg))
    if user is None:
        temporary = _password()
        user = User(email=reg, password_hash=hash_password(temporary), role=Role.REGISTRAR,
                    can_validate=True, must_change_password=True)
        db.add(user)
        db.flush()
        audit.record(db, "user_created", entity_type="user", entity_id=user.id,
                     new_value={"email": reg, "role": Role.REGISTRAR, "can_validate": True,
                                "via": VIA})
        db.commit()
        api.temporary[reg] = temporary
        report.add("accounts")
    elif not user.is_active or user.role != Role.REGISTRAR:
        raise DemoError(f"{reg} exists but is not an active registrar; fix it or run "
                        "purge-demo first")

    station_key = None
    station = db.scalar(select(ScanStation).where(ScanStation.name == STATION_NAME))
    if station is None or reset_passwords:
        station_key = new_token()
        if station is None:
            station = ScanStation(name=STATION_NAME, api_key_hash=hash_token(station_key))
            db.add(station)
            db.flush()
            audit.record(db, "station_registered", entity_type="scan_station",
                         entity_id=station.id, new_value={"name": STATION_NAME, "via": VIA})
            report.add("stations")
        else:
            station.api_key_hash = hash_token(station_key)
            audit.record(db, "station_key_reissued", entity_type="scan_station",
                         entity_id=station.id, new_value={"name": STATION_NAME, "via": VIA})
        db.commit()
    if not station.is_active:
        report.notes.append(f"Station {STATION_NAME!r} is deactivated (reactivate it or run "
                            "purge-demo and seed again)")

    # -- catalog ----------------------------------------------------------------------------
    program = db.scalar(select(Program).where(Program.code == PROGRAM["code"]))
    if program is None:
        program_id = api.call(reg, "POST", "/api/programs", json=PROGRAM)["id"]
        report.add("programs")
    else:
        program_id = program.id

    subject_ids = {}
    for code, title, units, _, _ in SUBJECTS:
        subject = _fresh(db).scalar(select(Subject).where(Subject.code == code))
        if subject is None:
            subject_ids[code] = api.call(reg, "POST", "/api/subjects", json={
                "code": code, "title": title, "units": units, "program_id": program_id})["id"]
            report.add("subjects")
        else:
            subject_ids[code] = subject.id

    term_ids = []
    for year, semester, _ in TERMS:
        term = _fresh(db).scalar(select(Term).where(Term.academic_year == year,
                                                    Term.semester == semester))
        if term is None:
            term_ids.append(api.call(reg, "POST", "/api/terms",
                                     json={"academic_year": year, "semester": semester})["id"])
            report.add("terms")
        else:
            term_ids.append(term.id)
    own_terms = demo_term_ids(_fresh(db))
    for i, term_id in enumerate(term_ids):
        if term_id not in own_terms:
            report.notes.append(f"Term {TERMS[i][0]} {TERMS[i][1]} already existed: used as it "
                                "is (not locked, not made current, never purged)")

    teacher_ids, teacher_emails = [], []
    for number, name, email in TEACHERS:
        teacher = _fresh(db).scalar(select(Teacher).where(Teacher.employee_number == number))
        if teacher is None:
            temporary = _password()
            teacher_ids.append(api.call(reg, "POST", "/api/teachers", json={
                "employee_number": number, "full_name": name,
                "department": PROGRAM["department"], "email": email,
                "temporary_password": temporary})["id"])
            api.temporary[email] = temporary
            report.add("teachers")
            report.add("accounts")
        else:
            teacher_ids.append(teacher.id)
        teacher_emails.append(email)

    section_ids = {}  # subject code -> class section id
    for code, _, _, term_index, teacher_index in SUBJECTS:
        block = TERMS[term_index][2]
        section = _fresh(db).scalar(select(ClassSection).where(
            ClassSection.subject_id == subject_ids[code],
            ClassSection.term_id == term_ids[term_index], ClassSection.section_code == block))
        if section is None:
            section_ids[code] = api.call(reg, "POST", "/api/sections", json={
                "subject_id": subject_ids[code], "term_id": term_ids[term_index],
                "section_code": block, "teacher_id": teacher_ids[teacher_index]})["id"]
            report.add("class sections")
        else:
            section_ids[code] = section.id

    adviser_teacher, adviser_term = ADVISER
    block = TERMS[adviser_term][2]
    if _fresh(db).scalar(select(SectionAdviser).where(
            SectionAdviser.term_id == term_ids[adviser_term],
            SectionAdviser.section_code == block)) is None:
        api.call(reg, "PUT", "/api/section-advisers", json={
            "term_id": term_ids[adviser_term], "section_code": block,
            "teacher_id": teacher_ids[adviser_teacher], "reason": "SAMPLE demo class adviser"})
        report.add("class advisers")

    # -- students, enrollments, grades -------------------------------------------------------
    student_ids, student_emails = [], []
    for number, first, middle, handle, birthdate, sex, _ in STUDENTS:
        email = f"{handle}@{DEMO_DOMAIN}"
        student = _fresh(db).scalar(select(Student).where(Student.student_number == number))
        if student is None:
            temporary = _password()
            student_ids.append(api.call(reg, "POST", "/api/students", json={
                "student_number": number, "first_name": first, "middle_name": middle,
                "last_name": MARK, "birthdate": birthdate, "sex": sex,
                "program_id": program_id, "year_level": 2, "admission_date": "2025-08-11",
                "email": email, "temporary_password": temporary})["id"])
            api.temporary[email] = temporary
            report.add("students")
            report.add("accounts")
        else:
            student_ids.append(student.id)
        student_emails.append(email)

    db = _fresh(db)
    locked = {t.id for t in db.scalars(select(Term).where(Term.id.in_(term_ids),
                                                          Term.is_grades_locked.is_(True)))}
    for code, _, _, term_index, _ in SUBJECTS:
        enrolled = set(_fresh(db).scalars(select(Enrollment.student_id).where(
            Enrollment.class_section_id == section_ids[code])))
        missing = [STUDENTS[i][0] for i, sid in enumerate(student_ids) if sid not in enrolled]
        if not missing:
            continue
        if term_ids[term_index] in locked:
            report.notes.append(f"{code}: term is locked, {len(missing)} student(s) not enrolled")
            continue
        api.call(reg, "POST", f"/api/sections/{section_ids[code]}/enroll",
                 json={"student_numbers": missing})
        report.add("enrollments", len(missing))

    for s_index, (code, _, _, term_index, teacher_index) in enumerate(SUBJECTS):
        if term_ids[term_index] in locked:
            continue
        email = teacher_emails[teacher_index]
        db = _fresh(db)
        rows = db.execute(select(Enrollment, Student).join(Student)
                          .where(Enrollment.class_section_id == section_ids[code])).all()
        graded = set(db.scalars(select(Grade.enrollment_id).where(
            Grade.enrollment_id.in_([e.id for e, _ in rows]))))
        number_to_grades = {s[0]: s[6] for s in STUDENTS}
        entries = [{"enrollment_id": e.id, "grade": number_to_grades[st.student_number][s_index]}
                   for e, st in rows if e.id not in graded
                   and number_to_grades.get(st.student_number, [None] * 8)[s_index] is not None]
        if entries:
            api.call(email, "PUT", f"/api/sections/{section_ids[code]}/grades",
                     json={"entries": entries})
            report.add("grades", len(entries))
        should_submit = term_index == 0 or code in SUBMITTED
        section = _fresh(db).get(ClassSection, section_ids[code])
        all_graded = all(STUDENTS[i][6][s_index] is not None for i in range(len(STUDENTS)))
        if should_submit and all_graded and section.grades_submitted_at is None:
            api.call(email, "POST", f"/api/sections/{section_ids[code]}/submit")

    db = _fresh(db)
    first_term = db.get(Term, term_ids[0])
    if term_ids[0] in own_terms and not first_term.is_grades_locked:
        api.call(reg, "POST", f"/api/terms/{term_ids[0]}/lock", json={"force": False})
        report.add("terms locked")
    db = _fresh(db)
    if term_ids[1] in own_terms and db.scalar(
            select(Term.id).where(Term.is_current.is_(True))) is None:
        api.call(reg, "POST", f"/api/terms/{term_ids[1]}/set-current")
    elif not db.get(Term, term_ids[1]).is_current:
        report.notes.append(f"Another term is current: COE requests of the SAMPLE students are "
                            f"rejected until {TERMS[1][0]} {TERMS[1][1]} is current")

    # -- document requests in different states ------------------------------------------------
    pending_generation = []
    for s_index, doc_type, purpose, target in REQUESTS:
        db = _fresh(db)
        request = db.scalar(select(DocumentRequest).where(
            DocumentRequest.student_id == student_ids[s_index],
            DocumentRequest.document_type == doc_type).order_by(DocumentRequest.id.desc()))
        if request is None:
            body = api.call(student_emails[s_index], "POST", "/api/requests",
                            json={"document_type": doc_type.value, "purpose": purpose})
            report.add("document requests")
            request_id, status = body["id"], RequestStatus(body["status"])
        else:
            request_id, status = request.id, request.status
        status = _advance(api, request_id, status, target, can_generate, report)
        if status == RequestStatus.REQUESTED and target != RequestStatus.REQUESTED:
            pending_generation.append(f"{STUDENTS[s_index][1]} {MARK} {doc_type.value}")
    if pending_generation:
        report.notes.append(
            "PDF rendering is unavailable here (WeasyPrint), so these requests stay Pending: "
            + ", ".join(pending_generation) + ". Re-run with --api <API URL> to generate them "
            "on the server, or approve them in the web app.")

    # Accounts created in this run that never had to act still hold a temporary password:
    # sign in once so they get a final one (shown below) like everyone else.
    for email in [reg, *teacher_emails, *student_emails]:
        if reset_passwords or email in api.temporary:
            api.session(email)

    if report.created:
        db = _fresh(db)
        audit.record(db, "demo_data_seeded", entity_type="demo_data",
                     new_value={"created": report.created, "via": VIA})
        db.commit()
    return report, api.passwords, station_key


def _advance(api: Api, request_id: int, status: RequestStatus, target: RequestStatus,
             can_generate: bool, report: SeedReport) -> RequestStatus:
    reg = REGISTRAR_EMAIL
    order = [RequestStatus.REQUESTED, RequestStatus.APPROVED, RequestStatus.FOR_VERIFICATION,
             RequestStatus.VERIFIED, RequestStatus.RELEASED]
    while status in order and target in order and order.index(status) < order.index(target):
        if status == RequestStatus.REQUESTED:
            if not can_generate:
                return status
            try:
                body = api.call(reg, "POST", f"/api/requests/{request_id}/approve")
            except DemoError as e:  # the school charges a fee (ADGVS_PAYMENT_REQUIRED)
                if "payment" not in str(e).lower():
                    raise
                api.call(reg, "POST", f"/api/requests/{request_id}/confirm-payment")
                body = api.call(reg, "POST", f"/api/requests/{request_id}/approve")
        elif status == RequestStatus.FOR_VERIFICATION:
            body = api.call(reg, "POST", f"/api/requests/{request_id}/verify")
        elif status == RequestStatus.VERIFIED:
            body = api.call(reg, "POST", f"/api/requests/{request_id}/release")
        else:  # APPROVED: generation is blocked
            report.notes.append(f"Request {request_id} is approved but generation is blocked; "
                                "see its processing note in the web app")
            return status
        new = RequestStatus(body["status"])
        report.add(f"requests moved to {new.value}")
        if new == status:
            return status
        status = new
    return status


ROLE_LABELS = {REGISTRAR_EMAIL: "Registrar (validation operator)",
               TEACHERS[0][2]: "Teacher", TEACHERS[1][2]: "Teacher + class adviser"}


def credentials_table(passwords: dict[str, str], station_key: str | None) -> str:
    emails = [REGISTRAR_EMAIL, *(t[2] for t in TEACHERS),
              *(f"{s[3]}@{DEMO_DOMAIN}" for s in STUDENTS)]
    rows = [("Role", "Login", "Password")]
    for email in emails:
        role = ROLE_LABELS.get(email, "Student")
        rows.append((role, email, passwords.get(email, "(unchanged: --reset-passwords "
                                                       "issues a new one)")))
    rows.append(("Intake station key", STATION_NAME,
                 station_key or "(unchanged: --reset-passwords issues a new one)"))
    widths = [max(len(r[i]) for r in rows) for i in range(3)]
    lines = ["SAMPLE demo accounts (shown once; keep them in a password manager, never in "
             "the repository)", ""]
    for i, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)).rstrip())
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines) + "\n"


def seed_main(argv: list[str], session_factory, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage seed-demo",
                                     description="Create the SAMPLE demo dataset (idempotent).")
    parser.add_argument("--api", metavar="URL",
                        help="drive this deployed API (it must use the same database as "
                             "ADGVS_DATABASE_URL); default: the API in this process")
    parser.add_argument("--password-file", metavar="PATH",
                        help="write the demo passwords and station key to this file instead of "
                             "printing them")
    parser.add_argument("--reset-passwords", action="store_true",
                        help="issue new passwords for every demo account and a new station key")
    args = parser.parse_args(argv)

    if args.api:
        make_client = remote_client_factory(args.api)
        wake(make_client, out)
        can_generate = True
    else:
        make_client = in_process_client
        can_generate = renderer_available()
    with session_factory() as db:
        try:
            report, passwords, station_key = seed(db, make_client, can_generate=can_generate,
                                                  reset_passwords=args.reset_passwords, out=out)
        except DemoError as e:
            print(f"seed-demo stopped: {e}\nRe-run it: what was created is kept.",
                  file=sys.stderr)
            return 1
    if report.created:
        out("SAMPLE demo data added: " + ", ".join(f"{what} {n}"
                                                     for what, n in report.created.items()))
    else:
        out("SAMPLE demo data already present; nothing changed.")
    for note in report.notes:
        out(f"Note: {note}")
    if passwords or station_key:
        table = credentials_table(passwords, station_key)
        if args.password_file:
            path = Path(args.password_file)
            path.write_text(table, encoding="utf-8", newline="")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            out(f"Demo passwords and station key written to {path} (delete it after use).")
        else:
            out("")
            out(table)
    return 0


# =============================================================================================
# purge-demo
# =============================================================================================

@dataclass
class PurgePlan:
    ids: dict[str, set[int]] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    users: list[User] = field(default_factory=list)
    stations_deleted: list[int] = field(default_factory=list)
    kept_terms: list[str] = field(default_factory=list)

    def get(self, name: str) -> set[int]:
        return self.ids.setdefault(name, set())

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.ids.items() if v}


def _ids(db: Session, stmt) -> set[int]:
    return set(db.scalars(stmt))


def plan_purge(db: Session, include_stations: Iterable[str] = ()) -> PurgePlan:
    """What purge-demo would delete, and the real records that block it (problems)."""
    plan = PurgePlan()
    include = set(include_stations)
    stations = list(db.scalars(select(ScanStation)))
    unknown = include - {s.name for s in stations}
    for name in sorted(unknown):
        plan.problems.append(f"No station named {name!r}")
    demo_stations = {s.id for s in stations if is_demo_station(s)}
    swept_stations = demo_stations | {s.id for s in stations if s.name in include}

    users = [u for u in db.scalars(select(User)) if is_demo_email(u.email)]
    user_ids = {u.id for u in users}
    plan.users = [u for u in users if u.is_active or not u.email.startswith("purged-")]

    students = list(db.scalars(select(Student)))
    demo_students = {s.id for s in students if is_demo_student(s)}
    for s in students:
        if s.id not in demo_students and s.user_id in user_ids:
            plan.problems.append(f"Student {s.student_number} (not SAMPLE) has a demo login")
    teachers = list(db.scalars(select(Teacher)))
    demo_teachers = {t.id for t in teachers if is_demo_teacher(t)}
    for t in teachers:
        if t.id not in demo_teachers and t.user_id in user_ids:
            plan.problems.append(f"Teacher {t.employee_number} (not SAMPLE) has a demo login")

    demo_programs = _ids(db, select(Program.id).where(func.upper(Program.code).like(f"{MARK}%")))
    demo_subjects = _ids(db, select(Subject.id).where(func.upper(Subject.code).like(f"{MARK}%")))
    sections = list(db.scalars(select(ClassSection)))
    demo_sections = {c.id for c in sections if c.subject_id in demo_subjects
                     or c.section_code.upper().startswith(MARK)}
    for c in sections:
        if c.id not in demo_sections and c.teacher_id in demo_teachers:
            plan.problems.append(f"Class section {c.id} ({c.section_code}, not SAMPLE) is taught "
                                 "by a SAMPLE teacher")
    for s in students:
        if s.id not in demo_students and s.program_id in demo_programs:
            plan.problems.append(f"Student {s.student_number} (not SAMPLE) is in a SAMPLE program")
    for subj in db.scalars(select(Subject).where(Subject.program_id.in_(demo_programs))):
        if subj.id not in demo_subjects:
            plan.problems.append(f"Subject {subj.code} (not SAMPLE) belongs to a SAMPLE program")

    enrollments = db.execute(select(Enrollment.id, Enrollment.student_id,
                                    Enrollment.class_section_id)).all()
    demo_enrollments = set()
    for eid, sid, cid in enrollments:
        if sid in demo_students:
            demo_enrollments.add(eid)
        elif cid in demo_sections:
            plan.problems.append(f"A student who is not SAMPLE (id {sid}) is enrolled in SAMPLE "
                                 f"class section {cid}")
    demo_grades = _ids(db, select(Grade.id).where(Grade.enrollment_id.in_(demo_enrollments)))
    changes = _ids(db, select(GradeChangeRequest.id).where(
        GradeChangeRequest.grade_id.in_(demo_grades)))

    advisers = list(db.scalars(select(SectionAdviser)))
    demo_advisers = set()
    for a in advisers:
        if a.section_code.upper().startswith(MARK):
            demo_advisers.add(a.id)
        elif a.teacher_id in demo_teachers:
            plan.problems.append(f"A SAMPLE teacher advises block {a.section_code} (not SAMPLE)")

    requests = _ids(db, select(DocumentRequest.id).where(
        DocumentRequest.student_id.in_(demo_students)))
    documents = list(db.scalars(select(GeneratedDocument).where(
        GeneratedDocument.request_id.in_(requests))))
    document_ids = {d.id for d in documents}

    sessions = _ids(db, select(ScanSession.id).where(or_(
        ScanSession.operator_id.in_(user_ids), ScanSession.station_id.in_(swept_stations))))
    scanned = list(db.scalars(select(ScannedDocument).where(or_(
        ScannedDocument.session_id.in_(sessions),
        ScannedDocument.matched_document_id.in_(document_ids)))))
    scanned_ids = {s.id for s in scanned}
    validations = _ids(db, select(ValidationRecord.id).where(
        ValidationRecord.scanned_document_id.in_(scanned_ids)))

    intake = list(db.scalars(select(IntakeDocument).where(or_(
        IntakeDocument.station_id.in_(swept_stations),
        IntakeDocument.student_id.in_(demo_students)))))
    for doc in intake:
        if doc.student_id is not None and doc.student_id not in demo_students:
            plan.problems.append(f"Intake document {doc.id} of a SAMPLE/listed station is linked "
                                 f"to a student who is not SAMPLE (id {doc.student_id})")
    batches = _ids(db, select(IntakeBatch.id).where(IntakeBatch.station_id.in_(swept_stations)))

    # Terms: only those a demo account created, and only once nothing real is left in them.
    for term_id in sorted(demo_term_ids(db)):
        term = db.get(Term, term_id)
        if term is None:
            continue
        real_sections = db.scalar(select(func.count()).select_from(ClassSection).where(
            ClassSection.term_id == term_id, ClassSection.id.not_in(demo_sections)))
        real_advisers = db.scalar(select(func.count()).select_from(SectionAdviser).where(
            SectionAdviser.term_id == term_id, SectionAdviser.id.not_in(demo_advisers)))
        if real_sections or real_advisers:
            plan.kept_terms.append(f"{term.academic_year} {term.semester.value}")
        else:
            plan.get("terms").add(term_id)

    plan.ids.update({
        "validation_records": validations, "scanned_documents": scanned_ids,
        "scan_sessions": sessions, "intake_documents": {d.id for d in intake},
        "intake_batches": batches, "generated_documents": document_ids,
        "document_requests": requests, "grade_change_requests": changes,
        "grades": demo_grades, "enrollments": demo_enrollments,
        "section_advisers": demo_advisers, "class_sections": demo_sections,
        "students": demo_students, "teachers": demo_teachers, "subjects": demo_subjects,
        "programs": demo_programs})
    plan.stations_deleted = sorted(demo_stations)
    plan.files = sorted({d.pdf_path for d in documents if d.pdf_path}
                        | {d.image_key for d in intake if d.image_key}
                        | {key for s in scanned for key in (s.image_paths or [])})
    return plan


# Children before parents (foreign keys).
DELETE_ORDER = [
    ("validation_records", ValidationRecord), ("scanned_documents", ScannedDocument),
    ("scan_sessions", ScanSession), ("intake_documents", IntakeDocument),
    ("intake_batches", IntakeBatch), ("generated_documents", GeneratedDocument),
    ("document_requests", DocumentRequest), ("grade_change_requests", GradeChangeRequest),
    ("grades", Grade), ("enrollments", Enrollment), ("section_advisers", SectionAdviser),
    ("class_sections", ClassSection), ("students", Student), ("teachers", Teacher),
    ("subjects", Subject), ("programs", Program), ("terms", Term),
]


def _chunks(ids: set[int], size: int = 500) -> Iterable[list[int]]:
    ordered = sorted(ids)
    for i in range(0, len(ordered), size):
        yield ordered[i:i + size]


def execute_purge(db: Session, plan: PurgePlan) -> dict[str, int]:
    """Delete the plan in one transaction, then the files. Returns the counts."""
    if plan.problems:
        raise DemoError("Refusing to purge: " + "; ".join(plan.problems))
    counts = plan.counts()
    for name, model in DELETE_ORDER:
        for chunk in _chunks(plan.ids.get(name, set())):
            db.execute(delete(model).where(model.id.in_(chunk)))
    stations = 0
    for station_id in plan.stations_deleted:
        station = db.get(ScanStation, station_id)
        in_use = any(db.scalar(select(func.count()).select_from(m)
                               .where(m.station_id == station_id))
                     for m in (ScanSession, IntakeBatch, IntakeDocument))
        if in_use:  # cannot happen after the deletes above; keep it off, never delete it
            station.is_active = False
        else:
            db.delete(station)
            stations += 1
    if stations:
        counts["scan_stations"] = stations
    for user in plan.users:
        old = user.email
        local = f"purged-{user.id}-{old.split('@')[0]}"[:64]
        user.email = f"{local}@{DEMO_DOMAIN}"
        user.is_active = False
        user.can_validate = False
        user.password_hash = hash_password(secrets.token_urlsafe(32))
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))
        audit.record(db, "user_deactivated", entity_type="user", entity_id=user.id,
                     old_value={"email": old},
                     new_value={"email": user.email,
                                "reason": "SAMPLE demo data purged (manage purge-demo)"})
    if plan.users:
        counts["accounts deactivated"] = len(plan.users)
    audit.record(db, "demo_data_purged", entity_type="demo_data",
                 new_value={"deleted": counts, "files": len(plan.files),
                            "via": "manage purge-demo"})
    db.commit()
    return counts


def storage_label() -> str:
    from app.config import get_settings

    settings = get_settings()
    if settings.storage_backend == "supabase":
        return f"Supabase bucket {settings.storage_bucket!r} at {settings.supabase_url}"
    return f"local folder {Path(settings.storage_dir).resolve()}"


def missing_files(keys: list[str]) -> list[str]:
    """Stored files the plan names that this storage does not have: wrong storage settings
    would otherwise delete the rows and leave the files behind, unreferenced."""
    storage, missing = get_storage(), []
    for key in keys:
        try:
            storage.get(key)
        except (StoredFileMissing, ValueError):
            missing.append(key)
    return missing


def delete_files(keys: list[str]) -> tuple[int, list[str]]:
    storage = get_storage()
    deleted, failed = 0, []
    for key in keys:
        try:
            deleted += storage.delete(key)
        except (StorageError, ValueError) as e:
            failed.append(f"{key}: {e}")
    return deleted, failed


def purge_main(argv: list[str], session_factory, out: Callable[[str], None] = print,
               ask: Callable[[str], str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage purge-demo",
                                     description="Remove the SAMPLE demo data.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be removed")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--include-station", action="append", default=[], metavar="NAME",
                        help="also remove every intake document and scan session of this "
                             "station (one used to scan SAMPLE documents); the station stays")
    parser.add_argument("--ignore-missing-files", action="store_true",
                        help="purge even if some stored files are not found in this storage")
    args = parser.parse_args(argv)
    with session_factory() as db:
        plan = plan_purge(db, args.include_station)
        counts = plan.counts()
        if plan.problems:
            print("Refusing to purge: real records depend on demo data. Nothing was changed.",
                  file=sys.stderr)
            for problem in plan.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        out("SAMPLE demo data to remove:")
        for name, n in counts.items():
            out(f"  {name:<24}{n}")
        if plan.stations_deleted:
            out(f"  {'scan_stations':<24}{len(plan.stations_deleted)}")
        out(f"  {'stored files':<24}{len(plan.files)} (in the {storage_label()})")
        if plan.users:
            out(f"  {'accounts to deactivate':<24}{len(plan.users)} "
                "(kept: the audit trail references them)")
        for term in plan.kept_terms:
            out(f"  kept term {term}: real class sections use it")
        if not counts and not plan.users and not plan.stations_deleted:
            out("  nothing")
            return 0
        try:
            missing = missing_files(plan.files)
        except StorageError as e:
            print(f"Storage unavailable ({e}); nothing was changed.", file=sys.stderr)
            return 1
        if missing:
            print(f"{len(missing)} of {len(plan.files)} stored file(s) are not in the "
                  f"{storage_label()}. Check ADGVS_STORAGE_BACKEND and the Supabase settings "
                  "(the same as the API's), or pass --ignore-missing-files.", file=sys.stderr)
            if not args.ignore_missing_files:
                print("Nothing was changed.", file=sys.stderr)
                return 1
        if args.dry_run:
            out("Dry run: nothing was changed.")
            return 0
        answer = "PURGE" if args.yes else (ask or input)("Type PURGE to remove it: ").strip()
        if answer != "PURGE":
            out("Cancelled: nothing was changed.")
            return 1
        execute_purge(db, plan)
    deleted, failed = delete_files(plan.files)
    out(f"Removed. {deleted} stored file(s) deleted. Audit entries stay (append-only); demo "
        "accounts are deactivated and renamed purged-<id>-...")
    for failure in failed:
        out(f"  file not deleted: {failure}")
    return 2 if failed else 0
