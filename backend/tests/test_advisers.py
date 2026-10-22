"""Change S-08: class advisers per block section and what an adviser may see.

A block section is every class section of one term sharing a section code (BSIT-1A). The
registrar designates its adviser; the adviser may VIEW the rosters and grades of the block,
never encode or request changes there unless they also teach the subject.
"""

import pytest
from sqlalchemy import select

from app.models import AuditLog, ClassSection, Subject, Teacher, Term, User
from app.models.enums import Role, Semester
from app.security import hash_password
from app.seed import DEMO_PASSWORD, seed

DOMAIN = "school.edu.ph"


@pytest.fixture
def seeded(db):
    seed(db)
    adviser_user = User(email=f"adviser@{DOMAIN}", password_hash=hash_password(DEMO_PASSWORD),
                        role=Role.TEACHER)
    db.add(adviser_user)
    db.flush()
    db.add(Teacher(user_id=adviser_user.id, employee_number="EMP-0002",
                   full_name="Prof. Gabriela Silang"))
    for user in db.scalars(select(User)):
        user.must_change_password = False
    db.commit()
    return db


@pytest.fixture
def as_user(client):
    def _as(handle: str):
        client.post("/api/auth/logout")
        res = client.post("/api/auth/login",
                          json={"email": f"{handle}@{DOMAIN}", "password": DEMO_PASSWORD})
        assert res.status_code == 200, res.text
        return client
    return _as


def term(db, semester: Semester) -> Term:
    return db.scalar(select(Term).where(Term.semester == semester))


def adviser(db) -> Teacher:
    return db.scalar(select(Teacher).where(Teacher.employee_number == "EMP-0002"))


def section_for(db, subject_code: str) -> ClassSection:
    return next(s for s in db.scalars(select(ClassSection)) if s.subject.code == subject_code)


def designate(client, db, teacher_id: int | None, semester=Semester.SECOND, **extra):
    return client.put("/api/section-advisers", json={
        "term_id": term(db, semester).id, "section_code": "BSIT-1A", "teacher_id": teacher_id,
        **extra})


def test_registrar_designates_adviser_with_audit(seeded, as_user):
    registrar = as_user("registrar")
    blocks = registrar.get("/api/section-advisers").json()
    assert [(b["semester"], b["section_code"], b["subjects"], b["students"], b["teacher_id"])
            for b in blocks] == [("1st", "BSIT-1A", 4, 3, None), ("2nd", "BSIT-1A", 4, 3, None)]

    res = designate(registrar, seeded, adviser(seeded).id, reason="Start of 2nd semester")
    assert res.status_code == 200
    assert res.json()["teacher_name"] == "Prof. Gabriela Silang"
    log = seeded.scalar(select(AuditLog).where(AuditLog.action == "section_adviser_assigned"))
    assert log.old_value == {"teacher_id": None}
    assert log.new_value["teacher_id"] == adviser(seeded).id
    assert log.new_value["reason"] == "Start of 2nd semester"
    term_id = term(seeded, Semester.SECOND).id
    only = registrar.get(f"/api/section-advisers?term_id={term_id}").json()
    assert [b["teacher_id"] for b in only] == [adviser(seeded).id]

    # Same adviser again: nothing changes, nothing logged.
    designate(registrar, seeded, adviser(seeded).id)
    assert len(seeded.scalars(select(AuditLog).where(
        AuditLog.action == "section_adviser_assigned")).all()) == 1


def test_designation_validates_input_and_role(seeded, as_user):
    registrar = as_user("registrar")
    res = registrar.put("/api/section-advisers", json={
        "term_id": term(seeded, Semester.SECOND).id, "section_code": "NOPE-9Z",
        "teacher_id": adviser(seeded).id})
    assert res.status_code == 404
    assert designate(registrar, seeded, 999999).status_code == 404
    for handle in ("teacher", "maria.santos", "admin"):
        assert designate(as_user(handle), seeded, adviser(seeded).id).status_code == 403


def test_adviser_sees_advisory_class_read_only(seeded, as_user):
    designate(as_user("registrar"), seeded, adviser(seeded).id)
    client = as_user("adviser")

    sections = client.get("/api/sections").json()
    assert sorted(s["subject_code"] for s in sections) == ["GE103", "IT103", "IT104", "PE101"]
    assert all(s["advising"] and not s["teaching"] for s in sections)

    [advisory] = client.get("/api/advisory").json()
    assert advisory["section_code"] == "BSIT-1A" and advisory["semester"] == "2nd"
    assert [s["teacher_name"] for s in advisory["sections"]] == ["Prof. Jose Rizal Mercado"] * 4
    assert [s["student_number"] for s in advisory["students"]] == [
        "2025-00002", "2025-00003", "2025-00001"]  # by last name
    # Only permitted student information: no contact details, birthdate or account data.
    assert set(advisory["students"][0]) == {"student_id", "student_number", "student_name",
                                            "program_code", "year_level"}

    it103 = section_for(seeded, "IT103")
    rows = client.get(f"/api/sections/{it103.id}/grades").json()
    assert len(rows) == 3
    assert client.get(f"/api/sections/{it103.id}/enrollments").status_code == 200

    # Advising grants no writes: grades stay with the subject teacher.
    entry = {"entries": [{"enrollment_id": rows[0]["enrollment_id"], "grade": "1.00"}]}
    assert client.put(f"/api/sections/{it103.id}/grades", json=entry).status_code == 403
    assert client.post(f"/api/sections/{it103.id}/submit").status_code == 403
    assert client.post(f"/api/sections/{it103.id}/grades/import",
                       files={"file": ("g.csv", b"student_number,grade\n")}).status_code == 403
    assert client.post("/api/grade-change-requests", json={
        "grade_id": rows[0]["grade_id"], "new_grade": "1.00",
        "reason": "Not mine to change"}).status_code == 403


def test_teacher_cannot_see_sections_they_neither_advise_nor_teach(seeded, as_user):
    designate(as_user("registrar"), seeded, adviser(seeded).id)  # 2nd semester only
    # A different block in the same term, taught by someone else.
    other_block = ClassSection(subject_id=seeded.scalar(select(Subject.id)),
                               term_id=term(seeded, Semester.SECOND).id, section_code="BSIT-1B")
    seeded.add(other_block)
    seeded.commit()

    client = as_user("adviser")
    ids = {s["id"] for s in client.get("/api/sections").json()}
    first_sem = section_for(seeded, "IT101")  # BSIT-1A, but the 1st semester: not advised
    for section in (first_sem, other_block):
        assert section.id not in ids
        assert client.get(f"/api/sections/{section.id}/grades").status_code == 403
        assert client.get(f"/api/sections/{section.id}/enrollments").status_code == 403

    # The subject teacher still sees exactly the sections they teach, flagged as teaching.
    teacher = as_user("teacher").get("/api/sections").json()
    assert len(teacher) == 8 and all(s["teaching"] and not s["advising"] for s in teacher)
    assert as_user("teacher").get("/api/advisory").json() == []


def test_removing_the_adviser_removes_access(seeded, as_user):
    registrar = as_user("registrar")
    designate(registrar, seeded, adviser(seeded).id)
    res = designate(registrar, seeded, None, reason="Adviser on leave")
    assert res.status_code == 200 and res.json()["teacher_id"] is None
    logs = seeded.scalars(select(AuditLog).where(
        AuditLog.action == "section_adviser_assigned").order_by(AuditLog.id)).all()
    assert logs[-1].old_value == {"teacher_id": adviser(seeded).id}
    assert logs[-1].new_value["teacher_id"] is None

    client = as_user("adviser")
    assert client.get("/api/sections").json() == []
    assert client.get("/api/advisory").json() == []
    assert client.get(f"/api/sections/{section_for(seeded, 'IT103').id}/grades").status_code \
        == 403
