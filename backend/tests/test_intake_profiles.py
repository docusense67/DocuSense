"""S-06/S-07 API: review extracted fields, match or create a student, link the document.

Builds on the screening fixtures of test_intake.py. All people here are synthetic SAMPLEs.
"""

import json

import pytest
from sqlalchemy import select

from app.models import AuditLog, IntakeDocument, Student
from tests.test_intake import DOMAIN, audits, env, isolated, submit  # noqa: F401
from tests.test_intake_decision import clean


def read(value, confidence=0.9):
    return {"value": value, "confidence": confidence}


PSA_FIELDS = {
    "child_last_name": read("SAMPLEDELACRUZ", 0.95), "child_first_name": read("JUANITO", 0.91),
    "child_middle_name": read("PEREZQ", 0.6), "sex": read("M", 0.8),
    "date_of_birth": read("MARCH 5, 2010", 0.7), "place_of_birth": read("QUEZONVILLE", 0.85),
    "mother_first_name": read("MARIAQ"), "mother_last_name": read("PEREZQ"),
    "father_first_name": read("JOSEQ"), "father_last_name": read("SAMPLEDELACRUZ"),
}
CARD_FIELDS = {"learner_name": read("SAMPLEDELACRUZ, JUANITO P.", 0.8),
               "lrn": read("4242 4242 4242"), "school_year": read("2025-2026"),
               "grade": read("Grade 10"), "section": read("SAMPLE-RIZAL")}
# Every reviewed value that must never appear in an audit entry.
PERSONAL = ("SAMPLEDELACRUZ", "Sampledelacruz", "JUANITO", "Juanito", "PEREZQ", "2010-03-05",
            "QUEZONVILLE", "Quezonville", "MARIAQ", "JOSEQ", "424242424242")


def psa(**changes):
    result = clean(**changes)
    result["fields"] = dict(PSA_FIELDS)
    return result


def card(**changes):
    result = clean(doc_type__predicted="jhs_report_card", **changes)
    result["fields"] = dict(CARD_FIELDS)
    return result


def url(intake_id, tail=""):
    return f"/api/intake/documents/{intake_id}{tail}"


def reviewed_psa(**overrides):
    return {"values": {"last_name": "Sampledelacruz", "first_name": "Juanito",
                       "middle_name": "Perezq", "sex": "Male", "birth_date": "2010-03-05",
                       "birth_place": "Quezonville", "mother_name": "Mariaq Perezq",
                       "father_name": "Joseq Sampledelacruz", **overrides}}


def assert_no_values_in_audit(db):
    text = json.dumps([[a.old_value, a.new_value] for a in db.scalars(select(AuditLog))])
    for value in PERSONAL:
        assert value not in text, value


# ---- the whole flow ---------------------------------------------------------------------------

def test_review_fields_create_student_and_link(env):
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    db = env["db"]

    out = client.get(url(intake_id, "/fields")).json()
    assert out["editable"] is True and out["blocked"] is None and out["reviewed"] is None
    assert [s["key"] for s in out["schema"]][:3] == ["last_name", "first_name", "middle_name"]
    assert out["extracted"]["birth_date"]["value"] == "2010-03-05"
    assert out["extracted"]["birth_date"]["confidence"] == 0.7
    assert out["extracted"]["mother_name"]["value"] == "MARIAQ PEREZQ"

    res = client.put(url(intake_id, "/fields"), json=reviewed_psa())
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "data_reviewed" and out["reviewed"]["reviewed_by"] == \
        f"registrar@{DOMAIN}"
    doc = db.get(IntakeDocument, intake_id)
    assert doc.screening_result["fields"] == PSA_FIELDS  # the station's reading is untouched
    assert doc.reviewed_fields["values"]["last_name"] == "Sampledelacruz"
    assert doc.reviewed_by is not None  # a passed document is accepted by reviewing it
    [accepted] = audits(db, "intake_document_reviewed")
    assert accepted.new_value["implicit"] and accepted.new_value["status"] == "accepted"
    [entry] = audits(db, "intake_fields_reviewed")
    assert entry.old_value == {"status": "accepted", "doc_type": "psa_birth_certificate"}
    # names changed in case only still count as corrected: the stored value differs
    assert {"last_name", "first_name", "middle_name", "birth_place"} <= \
        set(entry.new_value["fields_corrected"])
    assert "sex" not in entry.new_value["fields_corrected"]  # "M" read, "Male" kept
    assert entry.new_value["fields_edited"] is None  # first review

    assert client.get(url(intake_id, "/candidates")).json()["candidates"] == []
    res = client.post(url(intake_id, "/create-student"),
                      json={"student_number": "2026-90001", "year_level": 1})
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["status"] == "linked" and out["student"]["student_number"] == "2026-90001"
    student = db.get(Student, out["student"]["id"])
    assert (student.last_name, student.first_name, student.middle_name) == (
        "Sampledelacruz", "Juanito", "Perezq")
    assert student.birthdate.isoformat() == "2010-03-05" and student.sex == "Male"
    assert student.birth_place == "Quezonville" and student.mother_name == "Mariaq Perezq"
    assert student.father_name == "Joseq Sampledelacruz" and student.lrn is None
    [created] = audits(db, "student_created")
    assert created.new_value["source"] == "intake_document"
    assert "birthdate" in created.new_value["fields_from_document"]
    [linked] = audits(db, "intake_document_linked")
    assert linked.new_value["how"] == "created" and linked.new_value["student_id"] == student.id

    # The student's record lists the document; the detail names the student.
    rows = client.get(f"/api/intake/documents?student_id={student.id}").json()
    assert [r["id"] for r in rows] == [intake_id] and rows[0]["status"] == "linked"
    assert client.get(url(intake_id)).json()["student"]["id"] == student.id
    history = [h["action"] for h in client.get(url(intake_id)).json()["history"]]
    assert history == ["intake_document_screened", "intake_document_reviewed",
                       "intake_fields_reviewed", "intake_document_linked"]
    assert_no_values_in_audit(db)


def test_a_student_can_have_many_documents(env):
    client = env["login"]("registrar")
    first = submit(env, psa()).json()["intake_id"]
    client.put(url(first, "/fields"), json=reviewed_psa())
    student_id = client.post(url(first, "/create-student"),
                             json={"student_number": "2026-90002"}).json()["student"]["id"]
    second = submit(env, card()).json()["intake_id"]
    res = client.put(url(second, "/fields"), json={"values": {
        "last_name": "Sampledelacruz", "first_name": "Juanito", "lrn": "424242424242",
        "school_year": "2025-2026", "grade_level": "10", "general_average": "91.25"}})
    assert res.status_code == 200, res.text
    [candidate] = client.get(url(second, "/candidates")).json()["candidates"]
    # same name, no birth date on a report card: a possible match, so a reason is needed
    assert candidate["student_id"] == student_id and candidate["match"] == "name"
    assert client.post(url(second, "/link"), json={"student_id": student_id}).status_code == 422
    res = client.post(url(second, "/link"), json={"student_id": student_id,
                                                  "reason": "Same learner, checked the PSA"})
    assert res.status_code == 200 and res.json()["status"] == "linked"
    rows = client.get(f"/api/intake/documents?student_id={student_id}").json()
    assert sorted(r["id"] for r in rows) == [first, second]
    assert_no_values_in_audit(env["db"])


# ---- validation -------------------------------------------------------------------------------

def test_invalid_fields_are_refused_field_by_field_and_nothing_is_saved(env):
    intake_id = submit(env, card()).json()["intake_id"]
    client = env["login"]("registrar")
    res = client.put(url(intake_id, "/fields"), json={"values": {
        "last_name": "", "first_name": "Juan1", "lrn": "12345", "school_year": "2025-2027",
        "general_average": "101", "birth_date": "2010-01-01"}})
    assert res.status_code == 422
    errors = res.json()["detail"]["errors"]
    assert set(errors) == {"last_name", "first_name", "lrn", "school_year", "general_average",
                           "birth_date"}  # birth_date is not on a report card
    doc = env["db"].get(IntakeDocument, intake_id)
    assert doc.reviewed_fields is None and doc.status == "screened"
    assert audits(env["db"], "intake_fields_reviewed") == []


def test_unknown_document_type_must_be_chosen_first(env):
    intake_id = submit(env, clean(doc_type__predicted="unknown")).json()["intake_id"]
    client = env["login"]("registrar")
    client.post(url(intake_id, "/review"), json={"action": "accept", "reason": "Is a PSA copy"})
    out = client.get(url(intake_id, "/fields")).json()
    assert out["schema"] == [] and "unknown" not in out["doc_types"]
    preview = client.get(url(intake_id, "/fields?doc_type=good_moral_certificate")).json()
    assert "date_issued" in [s["key"] for s in preview["schema"]]
    body = {"values": {"last_name": "Sample", "first_name": "Juanito"}}
    assert client.put(url(intake_id, "/fields"), json=body).status_code == 422
    res = client.put(url(intake_id, "/fields"), json={**body, "doc_type": "good_moral_certificate"})
    assert res.status_code == 200 and res.json()["doc_type"] == "good_moral_certificate"
    [entry] = audits(env["db"], "intake_fields_reviewed")
    assert entry.old_value["doc_type"] == "unknown"
    assert entry.new_value["doc_type"] == "good_moral_certificate"


def test_re_reviewing_records_which_fields_were_edited(env):
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    client.put(url(intake_id, "/fields"), json=reviewed_psa())
    res = client.put(url(intake_id, "/fields"), json=reviewed_psa(birth_place="Sampletown"))
    assert res.status_code == 200
    entry = audits(env["db"], "intake_fields_reviewed")[-1]
    assert entry.old_value["status"] == "data_reviewed"
    assert entry.new_value["fields_edited"] == ["birth_place"]
    assert "Sampletown" not in json.dumps(entry.new_value)


# ---- state rules ------------------------------------------------------------------------------

def test_a_flagged_document_must_be_accepted_before_its_data_is_used(env):
    intake_id = submit(env, psa(quality__ok=False)).json()["intake_id"]
    client = env["login"]("registrar")
    out = client.get(url(intake_id, "/fields")).json()
    assert out["editable"] is False and "accept it" in out["blocked"]
    assert client.put(url(intake_id, "/fields"), json=reviewed_psa()).status_code == 409
    client.post(url(intake_id, "/review"),
                json={"action": "accept", "reason": "Original checked at the window"})
    res = client.put(url(intake_id, "/fields"), json=reviewed_psa())
    assert res.status_code == 200 and res.json()["status"] == "data_reviewed"
    assert len(audits(env["db"], "intake_document_reviewed")) == 1  # no second, implicit one


@pytest.mark.parametrize("action", ["reject", "request_resubmission"])
def test_rejected_or_resubmission_documents_cannot_be_used(env, action):
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    client.post(url(intake_id, "/review"), json={"action": action, "reason": "Photocopy only"})
    out = client.get(url(intake_id, "/fields")).json()
    assert out["editable"] is False and out["blocked"]
    assert client.put(url(intake_id, "/fields"), json=reviewed_psa()).status_code == 409
    assert client.get(url(intake_id, "/candidates")).status_code == 409
    assert client.post(url(intake_id, "/link"), json={"student_id": 1}).status_code == 409
    assert client.post(url(intake_id, "/create-student"),
                       json={"student_number": "2026-90003"}).status_code == 409


def test_link_and_create_need_reviewed_fields(env):
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    client.post(url(intake_id, "/review"), json={"action": "accept"})
    assert client.get(url(intake_id, "/candidates")).status_code == 409
    assert client.post(url(intake_id, "/create-student"),
                       json={"student_number": "2026-90004"}).status_code == 409


def test_linked_documents_are_locked_until_unlinked(env):
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    client.put(url(intake_id, "/fields"), json=reviewed_psa())
    student_id = client.post(url(intake_id, "/create-student"),
                             json={"student_number": "2026-90005"}).json()["student"]["id"]
    assert client.put(url(intake_id, "/fields"), json=reviewed_psa()).status_code == 409
    assert client.post(url(intake_id, "/link"), json={"student_id": student_id}).status_code == 409
    assert client.post(url(intake_id, "/create-student"),
                       json={"student_number": "2026-90006"}).status_code == 409
    assert client.post(url(intake_id, "/unlink"), json={"reason": "x"}).status_code == 422
    res = client.post(url(intake_id, "/unlink"), json={"reason": "Linked to the wrong learner"})
    assert res.status_code == 200 and res.json()["status"] == "data_reviewed"
    assert res.json()["student"] is None
    [entry] = audits(env["db"], "intake_document_unlinked")
    assert entry.old_value == {"status": "linked", "student_id": student_id}
    assert client.post(url(intake_id, "/unlink"), json={"reason": "again"}).status_code == 409
    # the profile created from it stays; linking it back is a strong match
    [candidate] = client.get(url(intake_id, "/candidates")).json()["candidates"]
    assert candidate["student_id"] == student_id and candidate["strength"] == "strong"
    assert client.post(url(intake_id, "/link"), json={"student_id": student_id}).status_code == 200


# ---- matching and creation rules -------------------------------------------------------------

def add(db, number, last, first, birthdate=None, lrn=None):
    student = Student(student_number=number, last_name=last, first_name=first,
                      birthdate=birthdate, lrn=lrn)
    db.add(student)
    db.commit()
    return student.id


def test_candidates_by_lrn_then_name_and_birth_date(env):
    from datetime import date
    db = env["db"]
    by_name = add(db, "S-100", "Sampledelacruz", "Juanito", date(2010, 3, 5))
    by_lrn = add(db, "S-101", "Sampledelacruz", "Juanito", lrn="424242424242")
    add(db, "S-102", "Unrelated", "Person")
    intake_id = submit(env, card()).json()["intake_id"]
    client = env["login"]("registrar")
    client.put(url(intake_id, "/fields"), json={"values": {
        "last_name": "SAMPLEDELACRUZ", "first_name": "JUANITO", "lrn": "424242424242"}})
    found = client.get(url(intake_id, "/candidates")).json()["candidates"]
    assert [(c["student_id"], c["match"], c["strength"]) for c in found] == [
        (by_lrn, "lrn", "strong"), (by_name, "name", "possible")]
    # a strong match links without a reason; creating a new profile needs confirmation
    assert client.post(url(intake_id, "/create-student"),
                       json={"student_number": "2026-90007"}).status_code == 409
    res = client.post(url(intake_id, "/link"), json={"student_id": by_lrn})
    assert res.status_code == 200
    [entry] = audits(db, "intake_document_linked")
    assert entry.new_value["match"] == "lrn" and entry.new_value["reason"] is None


def test_linking_an_unsuggested_student_needs_a_reason(env):
    other = add(env["db"], "S-200", "Unrelated", "Person")
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    client.put(url(intake_id, "/fields"), json=reviewed_psa())
    assert client.post(url(intake_id, "/link"), json={"student_id": other}).status_code == 422
    assert client.post(url(intake_id, "/link"), json={"student_id": 99999}).status_code == 404
    res = client.post(url(intake_id, "/link"),
                      json={"student_id": other, "reason": "Name changed after adoption"})
    assert res.status_code == 200
    [entry] = audits(env["db"], "intake_document_linked")
    assert entry.new_value["match"] is None
    assert entry.new_value["reason"] == "Name changed after adoption"


def test_create_new_profile_despite_candidates_when_confirmed(env):
    from datetime import date
    add(env["db"], "S-300", "Sampledelacruz", "Juanito", date(2011, 1, 1))
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    client.put(url(intake_id, "/fields"), json=reviewed_psa())
    body = {"student_number": "2026-90008"}
    assert client.post(url(intake_id, "/create-student"), json=body).status_code == 409
    # an existing student number is refused like on the master list
    taken = {"student_number": "S-300", "confirm_new": True}
    assert client.post(url(intake_id, "/create-student"), json=taken).status_code == 409
    res = client.post(url(intake_id, "/create-student"), json={**body, "confirm_new": True})
    assert res.status_code == 201, res.text


def test_duplicate_lrn_is_refused(env):
    add(env["db"], "S-400", "Different", "Learner", lrn="424242424242")
    intake_id = submit(env, card()).json()["intake_id"]
    client = env["login"]("registrar")
    client.put(url(intake_id, "/fields"), json={"values": {
        "last_name": "Sampledelacruz", "first_name": "Juanito", "lrn": "424242424242"}})
    res = client.post(url(intake_id, "/create-student"),
                      json={"student_number": "2026-90009", "confirm_new": True})
    assert res.status_code == 409 and "LRN" in res.json()["detail"]


def test_create_student_rejects_bad_profile_input(env):
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"]("registrar")
    client.put(url(intake_id, "/fields"), json=reviewed_psa())
    for body in ({"student_number": ""}, {"student_number": "2026-1", "year_level": 9},
                 {"student_number": "2026-1", "program_id": 99999}):
        assert client.post(url(intake_id, "/create-student"), json=body).status_code in {404, 422}
    assert env["db"].get(IntakeDocument, intake_id).status == "data_reviewed"


# ---- master list: the new student columns -----------------------------------------------------

def test_master_list_accepts_lrn_and_parents_and_checks_lrn(env):
    client = env["login"]("registrar")
    base = {"student_number": "2026-91000", "first_name": "Juanito", "last_name": "Sample"}
    assert client.post("/api/students", json={**base, "lrn": "12345"}).status_code == 422
    res = client.post("/api/students", json={**base, "lrn": "424242424242",
                                              "birth_place": "Sampletown",
                                              "mother_name": "Mariaq Sample"})
    assert res.status_code == 201 and res.json()["lrn"] == "424242424242"
    dup = {**base, "student_number": "2026-91001", "lrn": "424242424242"}
    assert client.post("/api/students", json=dup).status_code == 409
    other = client.post("/api/students", json={**base, "student_number": "2026-91002"}).json()
    res = client.patch(f"/api/students/{other['id']}",
                       json={"lrn": "424242424242", "reason": "Typo fix"})
    assert res.status_code == 409


# ---- RBAC -------------------------------------------------------------------------------------

@pytest.mark.parametrize(("handle", "expected"), [
    ("registrar", 200), ("admin", 200), ("teacher", 403), ("maria.santos", 403)])
def test_who_may_review_fields(env, handle, expected):
    intake_id = submit(env, psa()).json()["intake_id"]
    client = env["login"](handle)
    assert client.get(url(intake_id, "/fields")).status_code == expected
    res = client.put(url(intake_id, "/fields"), json=reviewed_psa())
    assert res.status_code == expected
