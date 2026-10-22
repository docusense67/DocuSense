"""Epic 2: academic records, row-level access, grades, locking, corrections, imports."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    AuditLog,
    ClassSection,
    Enrollment,
    Grade,
    Student,
    Teacher,
    Term,
    User,
)
from app.models.enums import Role
from app.seed import DEMO_PASSWORD, seed

DOMAIN = "school.edu.ph"


@pytest.fixture
def seeded(db):
    seed(db)
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


def student(db, number: str) -> Student:
    return db.scalar(select(Student).where(Student.student_number == number))


def section_for(db, subject_code: str) -> ClassSection:
    return next(s for s in db.scalars(select(ClassSection)) if s.subject.code == subject_code)


def enrollment_of(db, number: str, subject_code: str) -> Enrollment:
    return db.scalar(select(Enrollment).where(
        Enrollment.student_id == student(db, number).id,
        Enrollment.class_section_id == section_for(db, subject_code).id))


def audit_actions(db) -> list[str]:
    return [a.action for a in db.scalars(select(AuditLog))]


# ---- row-level access -----------------------------------------------------------------------

def test_student_sees_own_records_with_gwa(seeded, as_user):
    client = as_user("maria.santos")
    records = client.get("/api/students/me/records").json()
    assert [r["semester"] for r in records] == ["1st", "2nd"]
    assert Decimal(records[0]["gwa"]) == Decimal("1.44")   # 1.4375 rounded half-up
    assert Decimal(records[1]["gwa"]) == Decimal("1.61")   # 17.75 / 11 units
    first_term = {s["subject_code"]: s["grade"] for s in records[0]["subjects"]}
    assert first_term == {"GE101": "1.75", "GE102": "1.25", "IT101": "1.25", "IT102": "1.50"}


def test_student_cannot_reach_other_students(seeded, as_user):
    client = as_user("maria.santos")
    other = student(seeded, "2025-00002")
    assert client.get(f"/api/students/{other.id}").status_code == 403
    assert client.get(f"/api/students/{other.id}/records").status_code == 403
    assert client.get("/api/students").status_code == 403
    assert client.get("/api/sections").status_code == 403


def test_teacher_only_sees_own_sections(seeded, as_user):
    other = Teacher(employee_number="EMP-0002", full_name="Other Teacher")
    seeded.add(other)
    seeded.flush()
    foreign = section_for(seeded, "IT103")
    foreign.teacher_id = other.id
    seeded.commit()

    client = as_user("teacher")
    codes = {s["subject_code"] for s in client.get("/api/sections").json()}
    assert "IT103" not in codes and len(codes) == 7
    assert client.get(f"/api/sections/{foreign.id}/grades").status_code == 403


# ---- encoding -------------------------------------------------------------------------------

def test_teacher_encodes_grade_in_open_term(seeded, as_user):
    client = as_user("teacher")
    section = section_for(seeded, "IT103")
    enrollment = enrollment_of(seeded, "2025-00001", "IT103")
    res = client.put(f"/api/sections/{section.id}/grades",
                     json={"entries": [{"enrollment_id": enrollment.id, "grade": "1.25"}]})
    assert res.status_code == 200 and res.json() == {"saved": 1}
    seeded.refresh(enrollment.grade)
    assert enrollment.grade.grade_value == Decimal("1.25")
    assert "grade_edited" in audit_actions(seeded)


def test_invalid_entry_saves_nothing(seeded, as_user):
    client = as_user("teacher")
    section = section_for(seeded, "IT103")
    good = enrollment_of(seeded, "2025-00001", "IT103")
    bad = enrollment_of(seeded, "2025-00002", "IT103")
    res = client.put(f"/api/sections/{section.id}/grades", json={"entries": [
        {"enrollment_id": good.id, "grade": "1.00"},
        {"enrollment_id": bad.id, "grade": "1.26"},
    ]})
    assert res.status_code == 422
    assert res.json()["errors"][0]["enrollment_id"] == bad.id
    seeded.refresh(good.grade)
    assert good.grade.grade_value == Decimal("1.50")  # unchanged


def test_registrar_edit_requires_reason(seeded, as_user):
    client = as_user("registrar")
    section = section_for(seeded, "IT103")
    enrollment = enrollment_of(seeded, "2025-00001", "IT103")
    body = {"entries": [{"enrollment_id": enrollment.id, "grade": "2.00"}]}
    assert client.put(f"/api/sections/{section.id}/grades", json=body).status_code == 422
    body["reason"] = "Encoding error on class record"
    assert client.put(f"/api/sections/{section.id}/grades", json=body).status_code == 200


def test_locked_term_blocks_direct_edits(seeded, as_user):
    client = as_user("teacher")
    section = section_for(seeded, "IT101")  # 1st semester is locked in the seed
    enrollment = enrollment_of(seeded, "2025-00001", "IT101")
    res = client.put(f"/api/sections/{section.id}/grades",
                     json={"entries": [{"enrollment_id": enrollment.id, "grade": "1.00"}]})
    assert res.status_code == 409


# ---- corrections after lock -----------------------------------------------------------------

def test_grade_change_request_flow(seeded, as_user):
    grade = enrollment_of(seeded, "2025-00001", "IT101").grade
    client = as_user("teacher")
    body = {"grade_id": grade.id, "new_grade": "1.00", "reason": "Computation error"}
    res = client.post("/api/grade-change-requests", json=body)
    assert res.status_code == 201
    request_id = res.json()["id"]
    assert client.post("/api/grade-change-requests", json=body).status_code == 409  # duplicate
    assert client.post(f"/api/grade-change-requests/{request_id}/approve").status_code == 403

    client = as_user("registrar")
    res = client.post(f"/api/grade-change-requests/{request_id}/approve")
    assert res.status_code == 200 and res.json()["status"] == "approved"
    seeded.refresh(grade)
    assert grade.grade_value == Decimal("1.00")
    assert {"grade_change_requested", "grade_change_approved"} <= set(audit_actions(seeded))


def test_change_request_only_for_locked_grades(seeded, as_user):
    grade = enrollment_of(seeded, "2025-00001", "IT103").grade
    client = as_user("teacher")
    res = client.post("/api/grade-change-requests",
                      json={"grade_id": grade.id, "new_grade": "1.00", "reason": "Typo"})
    assert res.status_code == 409


def test_reject_keeps_grade(seeded, as_user):
    grade = enrollment_of(seeded, "2025-00001", "IT101").grade
    as_user("teacher").post("/api/grade-change-requests",
                            json={"grade_id": grade.id, "new_grade": "1.00", "reason": "Typo"})
    client = as_user("registrar")
    request_id = client.get("/api/grade-change-requests?status=pending").json()[0]["id"]
    res = client.post(f"/api/grade-change-requests/{request_id}/reject",
                      json={"reason": "Not supported by class record"})
    assert res.json()["status"] == "rejected"
    seeded.refresh(grade)
    assert grade.grade_value == Decimal("1.25")


# ---- submit + lock --------------------------------------------------------------------------

def test_submit_and_lock_term(seeded, as_user):
    teacher = as_user("teacher")
    section = section_for(seeded, "IT103")
    assert teacher.post(f"/api/sections/{section.id}/submit").status_code == 200
    assert teacher.post(f"/api/terms/{section.term_id}/lock", json={}).status_code == 403

    registrar = as_user("registrar")
    assert registrar.post(f"/api/terms/{section.term_id}/lock", json={}).status_code == 200
    term = seeded.get(Term, section.term_id)
    seeded.refresh(term)
    assert term.is_grades_locked
    grades = seeded.scalars(select(Grade).join(Enrollment)
                            .where(Enrollment.class_section_id == section.id)).all()
    assert all(g.is_locked for g in grades)


def test_lock_blocks_when_grades_missing_unless_forced(seeded, as_user):
    seeded.delete(enrollment_of(seeded, "2025-00001", "IT103").grade)
    seeded.commit()
    client = as_user("registrar")
    term_id = section_for(seeded, "IT103").term_id
    assert client.post(f"/api/terms/{term_id}/lock", json={}).status_code == 409
    assert client.post(f"/api/terms/{term_id}/lock", json={"force": True}).status_code == 200


# ---- students -------------------------------------------------------------------------------

def test_update_student_requires_reason_and_audits_fields(seeded, as_user):
    client = as_user("registrar")
    maria = student(seeded, "2025-00001")
    assert client.patch(f"/api/students/{maria.id}", json={"year_level": 2}).status_code == 422
    res = client.patch(f"/api/students/{maria.id}",
                       json={"year_level": 2, "reason": "Promoted to 2nd year"})
    assert res.status_code == 200
    entry = seeded.scalars(select(AuditLog).where(AuditLog.action == "student_updated")).one()
    assert entry.old_value == {"year_level": 1}
    assert entry.new_value == {"year_level": 2, "reason": "Promoted to 2nd year"}


def test_create_student_with_login(seeded, as_user):
    client = as_user("registrar")
    program_id = client.get("/api/programs").json()[0]["id"]
    res = client.post("/api/students", json={
        "student_number": "2025-00099", "first_name": "José", "last_name": "Peña",
        "program_id": program_id, "email": f"jose.pena@{DOMAIN}",
        "temporary_password": "temp-password-1",
    })
    assert res.status_code == 201 and res.json()["user_id"] is not None
    dup = client.post("/api/students", json={
        "student_number": "2025-00099", "first_name": "X", "last_name": "Y"})
    assert dup.status_code == 409


STUDENTS_CSV = (
    "student_number,first_name,middle_name,last_name,program_code,birthdate,year_level\n"
    "2025-00100,Liza,Santos,Ramos,BSIT,2006-05-14,1\n"
    "2025-00101,Mark,,Villanueva,bsit,,2\n"
)


def test_import_students_csv(seeded, as_user):
    client = as_user("registrar")
    files = {"file": ("students.csv", STUDENTS_CSV, "text/csv")}
    dry = client.post("/api/students/import?dry_run=true", files=files).json()
    assert dry == {"imported": 0, "valid_rows": 2, "dry_run": True}
    assert student(seeded, "2025-00100") is None

    res = client.post("/api/students/import", files=files)
    assert res.json()["imported"] == 2
    assert student(seeded, "2025-00101").program_id is not None


def test_import_students_reports_every_row_error(seeded, as_user):
    client = as_user("registrar")
    bad = (
        "student_number,first_name,last_name,program_code,birthdate,year_level\n"
        "2025-00001,Dup,Licate,BSIT,,1\n"          # row 2: exists already
        "2025-00200,,Nofirst,BSXX,14/05/2006,9\n"  # row 3: four problems
        "2025-00201,Ok,Row,BSIT,,1\n"              # row 4: fine
    )
    res = client.post("/api/students/import", files={"file": ("s.csv", bad, "text/csv")})
    assert res.status_code == 422
    errors = {e["row"]: e["errors"] for e in res.json()["errors"]}
    assert set(errors) == {2, 3}
    assert len(errors[3]) == 4
    assert student(seeded, "2025-00201") is None  # all-or-nothing


def test_import_rejects_missing_columns(seeded, as_user):
    client = as_user("registrar")
    res = client.post("/api/students/import",
                      files={"file": ("s.csv", "student_number,first_name\n1,a\n", "text/csv")})
    assert res.status_code == 422
    assert "last_name" in res.json()["detail"]


def test_import_grades_csv(seeded, as_user):
    client = as_user("teacher")
    section = section_for(seeded, "IT104")
    csv_text = "student_number,grade\n2025-00001,1.00\n2025-00003,2.50\n"
    res = client.post(f"/api/sections/{section.id}/grades/import",
                      files={"file": ("grades.csv", csv_text, "text/csv")})
    assert res.status_code == 200 and res.json()["saved"] == 2
    ana = enrollment_of(seeded, "2025-00003", "IT104").grade
    seeded.refresh(ana)
    assert ana.grade_value == Decimal("2.50")


# ---- catalog --------------------------------------------------------------------------------

def test_catalog_setup_by_registrar(seeded, as_user):
    client = as_user("registrar")
    program = client.post("/api/programs", json={"code": "BSCS", "name": "BS Computer Science"})
    assert program.status_code == 201
    assert client.post("/api/programs", json={"code": "BSCS", "name": "Dup"}).status_code == 409
    term = client.post("/api/terms", json={"academic_year": "2026-2027", "semester": "1st"})
    assert term.status_code == 201
    bad_term = client.post("/api/terms", json={"academic_year": "2026-2028", "semester": "1st"})
    assert bad_term.status_code == 422
    subject = client.post("/api/subjects", json={"code": "CS101", "title": "Discrete Math",
                                                 "units": "3"}).json()
    section = client.post("/api/sections", json={"subject_id": subject["id"],
                                                  "term_id": term.json()["id"],
                                                  "section_code": "BSCS-1A"})
    assert section.status_code == 201
    maria = student(seeded, "2025-00001")
    enroll = client.post("/api/enrollments", json={"student_id": maria.id,
                                                   "class_section_id": section.json()["id"]})
    assert enroll.status_code == 201


def test_teacher_cannot_edit_catalog(seeded, as_user):
    client = as_user("teacher")
    assert client.post("/api/programs", json={"code": "X", "name": "X"}).status_code == 403


# ---- stations -------------------------------------------------------------------------------

def test_admin_registers_station_and_key_is_shown_once(seeded, as_user):
    assert as_user("registrar").post("/api/stations", json={"name": "S1"}).status_code == 403
    client = as_user("admin")
    res = client.post("/api/stations", json={"name": "Registrar Station 1"})
    assert res.status_code == 201
    assert len(res.json()["api_key"]) > 30
    assert "api_key" not in client.get("/api/stations").json()[0]
    assert seeded.scalar(select(User).where(User.role == Role.ADMIN)) is not None


def test_enroll_a_class_list_by_student_number(seeded, as_user):
    client = as_user("registrar")
    term = client.post("/api/terms", json={"academic_year": "2026-2027", "semester": "1st"}).json()
    subject = client.get("/api/subjects").json()[0]
    section = client.post("/api/sections", json={"subject_id": subject["id"], "term_id": term["id"],
                                                 "section_code": "BSIT-2A"}).json()
    url = f"/api/sections/{section['id']}/enroll"
    bad = client.post(url, json={"student_numbers": ["2025-00001", "2099-99999"]})
    assert bad.status_code == 422 and "2099-99999: no such student" in bad.json()["detail"]
    assert client.get(f"/api/sections/{section['id']}/enrollments").json() == []  # all-or-nothing
    ok = client.post(url, json={"student_numbers": ["2025-00001", " 2025-00002 ", "2025-00001"]})
    assert ok.json() == {"enrolled": 2}
    again = client.post(url, json={"student_numbers": ["2025-00002"]})
    assert again.status_code == 422 and "already enrolled" in again.json()["detail"]
