"""Per-student advisers (docs/applicant-intake.md, "Advisers"): one teacher-adviser per student
per term, assigned with an audited reason. Students see only their own adviser, teachers only
their own advisees. The S-08 class advisers of block sections are separate and unchanged.
"""

import pytest
from sqlalchemy import select

from app.models import AuditLog, SectionAdviser, Student, StudentAdviser, Teacher, Term
from app.models.enums import Semester
from tests.test_advisers import adviser, as_user, seeded  # noqa: F401

REASON = "Start of the 2nd semester"


def term(db, semester=Semester.SECOND) -> Term:
    return db.scalar(select(Term).where(Term.semester == semester))


def student(db, number) -> Student:
    return db.scalar(select(Student).where(Student.student_number == number))


def first_students(db, count=2) -> list[Student]:
    return list(db.scalars(select(Student).order_by(Student.id).limit(count)))


def assign(client, student_id, term_id, teacher_id, reason=REASON):
    body = {"student_id": student_id, "term_id": term_id, "teacher_id": teacher_id}
    if reason is not None:
        body["reason"] = reason
    return client.put("/api/student-advisers", json=body)


def test_registrar_assigns_a_students_adviser_with_an_audited_reason(seeded, as_user):
    db = seeded
    maria, juan = first_students(db)
    registrar = as_user("registrar")
    res = assign(registrar, maria.id, term(db).id, adviser(db).id)
    assert res.status_code == 200, res.text
    assert res.json()["teacher_name"] == "Prof. Gabriela Silang"
    assert res.json()["student_number"] == maria.student_number
    log = db.scalar(select(AuditLog).where(AuditLog.action == "student_adviser_assigned"))
    assert log.old_value == {"teacher_id": None}
    assert log.new_value == {"student_id": maria.id, "term_id": term(db).id,
                             "teacher_id": adviser(db).id, "reason": REASON}

    # Same adviser again: nothing changes, nothing logged.
    assign(registrar, maria.id, term(db).id, adviser(db).id)
    assert len(db.scalars(select(AuditLog).where(
        AuditLog.action == "student_adviser_assigned")).all()) == 1
    # One adviser per student and term: changing it replaces the row.
    other = db.scalar(select(Teacher).where(Teacher.employee_number == "EMP-0001"))
    assign(registrar, maria.id, term(db).id, other.id)
    rows = db.scalars(select(StudentAdviser).where(StudentAdviser.student_id == maria.id)).all()
    assert [r.teacher_id for r in rows] == [other.id]
    # Another term is another assignment.
    assign(registrar, maria.id, term(db, Semester.FIRST).id, adviser(db).id)
    listed = registrar.get("/api/student-advisers").json()
    assert len(listed) == 2
    only = registrar.get(f"/api/student-advisers?term_id={term(db).id}").json()
    assert [r["teacher_id"] for r in only] == [other.id]
    # Removing it needs a reason too.
    assert assign(registrar, maria.id, term(db).id, None, reason=None).status_code == 422
    assert assign(registrar, maria.id, term(db).id, None).json() is None
    assert db.scalar(select(AuditLog).where(
        AuditLog.action == "student_adviser_removed")).old_value == {"teacher_id": other.id}
    assert juan


@pytest.mark.parametrize(("change", "code"), [
    ({"reason": None}, 422), ({"reason": "x"}, 422), ({"student_id": 999_999}, 404),
    ({"term_id": 999_999}, 404), ({"teacher_id": 999_999}, 404),
])
def test_assignment_is_validated(seeded, as_user, change, code):
    db = seeded
    body = {"student_id": first_students(db)[0].id, "term_id": term(db).id,
            "teacher_id": adviser(db).id, "reason": REASON, **change}
    body = {k: v for k, v in body.items() if v is not None}
    assert as_user("registrar").put("/api/student-advisers", json=body).status_code == code


def test_students_see_their_own_adviser_and_teachers_their_own_advisees(seeded, as_user):
    db = seeded
    maria, juan = first_students(db)
    other = db.scalar(select(Teacher).where(Teacher.employee_number == "EMP-0001"))
    registrar = as_user("registrar")
    assign(registrar, maria.id, term(db).id, adviser(db).id)
    assign(registrar, juan.id, term(db).id, other.id)

    mine = as_user("maria.santos").get("/api/students/me/advisers").json()
    assert mine == [{"term_id": term(db).id, "term": "2025-2026 2nd", "is_current": True,
                     "teacher_name": "Prof. Gabriela Silang", "department": None}]
    theirs = as_user("juan.delacruz").get("/api/students/me/advisers").json()
    assert [a["teacher_name"] for a in theirs] == ["Prof. Jose Rizal Mercado"]

    advisees = as_user("adviser").get("/api/advisees").json()
    assert [a["student_id"] for a in advisees] == [maria.id]
    assert set(advisees[0]) == {"student_id", "student_number", "student_name", "program_code",
                                "year_level", "term_id", "term", "is_current"}
    assert [a["student_id"] for a in as_user("teacher").get("/api/advisees").json()] == [juan.id]


def test_who_may_call_the_adviser_routes(seeded, as_user):
    db = seeded
    for handle in ("teacher", "maria.santos"):
        client = as_user(handle)
        assert client.get("/api/student-advisers").status_code == 403
        assert assign(client, first_students(db)[0].id, term(db).id,
                      adviser(db).id).status_code == 403
    assert as_user("registrar").get("/api/advisees").status_code == 403
    assert as_user("teacher").get("/api/students/me/advisers").status_code == 403
    admin = as_user("admin")
    options = admin.get("/api/student-advisers/options").json()
    assert {t["name"] for t in options["teachers"]} >= {"Prof. Gabriela Silang"}
    assert len(options["terms"]) == 2 and options["students"]
    assert assign(admin, first_students(db)[0].id, term(db).id,
                  adviser(db).id).status_code == 200


def test_class_advisers_stay_separate(seeded, as_user):
    """Assigning a student's adviser does not make them the class adviser (S-08)."""
    db = seeded
    assign(as_user("registrar"), first_students(db)[0].id, term(db).id, adviser(db).id)
    assert db.scalar(select(SectionAdviser)) is None
    assert as_user("adviser").get("/api/advisory").json() == []
