"""Applicant intake, phase 2: second OCR pass, staff upload on an applicant's behalf, the
per-submission report, notifications and the application history of a former applicant.

Builds on the fixtures of test_applicant_intake.py; everything is a synthetic SAMPLE.
"""

import csv
import io
import json

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import ApplicantSecondPass, ApplicantSubmission, AuditLog, User
from app.models.enums import SecondPassStatus
from tests.test_applicant_intake import (  # noqa: F401
    DOMAIN,
    JPEG,
    applicant,
    assert_no_values_in_audit,
    claim,
    complete,
    env,
    isolated,
    reading,
    second_applicant,
    submitted,
    upload,
    worker,
)
from tests.test_applicant_references_api import claim_kind
from tests.test_documents import weasyprint_available

REASON = "Checked with the issuing school"


def read_and_accept(env, handle="applicant"):
    """An upload read by the worker and accepted by the registrar: (submission, intake id)."""
    sub_id = submitted(env, handle)
    intake_id = complete(env, claim(env)).json()["intake_id"]
    staff = env["login"]("registrar")
    res = staff.post(f"/api/intake/documents/{intake_id}/review",
                     json={"action": "accept", "reason": REASON})
    assert res.status_code == 200, res.text
    return sub_id, intake_id


def second_reading(student_number="OLD-2020-0042"):
    return reading(fields={
        "student_name": {"value": "samplereyes,  luzviminda c.", "confidence": None},
        "student_number": {"value": student_number, "confidence": None},
        "school_name": {"value": "SAMPLE COLLEGE OF ARTS", "confidence": None}})


def finish_second(env, job, body):
    return worker(env, "POST", f"/{job['submission_id']}/second-pass",
                  json={"lease_token": job["lease_token"], **body})


# ---- second OCR pass --------------------------------------------------------------------------

def test_accepting_an_online_document_queues_a_second_pass(env):
    sub_id, intake_id = read_and_accept(env)
    db = env["db"]
    second = db.scalar(select(ApplicantSecondPass))
    assert second.submission_id == sub_id and second.status == SecondPassStatus.QUEUED
    assert claim(env) is None  # a phase 1 worker never gets it
    job = claim_kind(env, "second")
    assert job["kind"] == "second" and job["submission_id"] == sub_id and job["pages"] == 1
    assert claim_kind(env, "second") is None  # leased

    page = worker(env, "GET", f"/{sub_id}/pages/1", headers={"X-Lease-Token": job["lease_token"]})
    assert page.status_code == 200
    wrong = worker(env, "GET", f"/{sub_id}/pages/1", headers={"X-Lease-Token": "nope"})
    assert wrong.status_code == 409
    # The second pass can't be finished on the first pass's route.
    assert complete(env, job).status_code == 409
    assert finish_second(env, job, {"reading": {"schema": 1}}).status_code == 422  # lease kept

    res = finish_second(env, job, {"reading": second_reading("OLD-2020-0047")})
    assert res.status_code == 200, res.text
    assert res.json()["disagreements"] == ["student_number"]  # case and spacing don't count
    assert finish_second(env, job, {"reading": second_reading()}).status_code == 409

    detail = env["login"]("registrar").get(f"/api/intake/documents/{intake_id}").json()
    second_view = detail["second_pass"]
    assert second_view["status"] == "done"
    assert second_view["disagreements"] == [
        {"field": "student_number", "first": "OLD-2020-0042", "second": "OLD-2020-0047"}]
    # The first pass stays where it was; the second one is stored apart.
    assert detail["fields"]["student_number"]["value"] == "OLD-2020-0042"
    actions = [h["action"] for h in detail["history"]]
    assert "applicant_second_pass_queued" in actions
    assert "applicant_second_pass_disagrees" in actions
    log = db.scalar(select(AuditLog).where(AuditLog.action == "applicant_second_pass_disagrees"))
    assert log.new_value["disagreements"] == ["student_number"]
    assert "OLD-2020-0047" not in json.dumps(log.new_value)
    assert_no_values_in_audit(db)


def test_second_pass_agreeing_and_queued_once(env):
    sub_id, intake_id = read_and_accept(env)
    job = claim_kind(env, "second")
    assert finish_second(env, job, {"reading": second_reading()}).json()["disagreements"] == []
    db = env["db"]
    assert db.scalar(select(AuditLog).where(
        AuditLog.action == "applicant_second_pass_completed")) is not None
    # Field review after the accept doesn't queue another one.
    staff = env["login"]("registrar")
    staff.put(f"/api/intake/documents/{intake_id}/fields", json={"values": {
        "last_name": "Samplereyes", "first_name": "Luzviminda"}})
    assert len(db.scalars(select(ApplicantSecondPass)).all()) == 1


def test_rejected_and_station_documents_get_no_second_pass(env):
    submitted(env)
    intake_id = complete(env, claim(env)).json()["intake_id"]
    env["login"]("registrar").post(f"/api/intake/documents/{intake_id}/review",
                                   json={"action": "reject", "reason": "Not a TOR"})
    assert env["db"].scalar(select(ApplicantSecondPass)) is None


def test_a_failed_second_pass_is_retried_then_queued_again_by_staff(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "applicant_max_attempts", 2)
    sub_id, _ = read_and_accept(env)
    first = claim_kind(env, "second")
    assert finish_second(env, first, {"error": "Tesseract crashed"}).json()["status"] == "queued"
    again = claim_kind(env, "second")
    assert again["attempt"] == 2
    assert finish_second(env, again, {"error": "Tesseract crashed"}).json()["status"] == "failed"
    assert claim_kind(env, "second") is None
    staff = env["login"]("registrar")
    row = staff.get("/api/applicant-submissions").json()[0]
    assert row["second_pass_status"] == "failed"
    res = staff.post(f"/api/applicant-submissions/{sub_id}/retry", json={"reason": "fixed"})
    assert res.json()["second_pass_status"] == "queued"
    assert claim_kind(env, "second")["attempt"] == 1
    assert staff.post(f"/api/applicant-submissions/{sub_id}/retry",
                      json={"reason": "again"}).status_code == 409


# ---- staff upload on an applicant's behalf ----------------------------------------------------

def applicant_id(env, handle="applicant"):
    return env["db"].scalar(select(User.id).where(User.email == f"{handle}@{DOMAIN}"))


def test_registrar_uploads_on_an_applicants_behalf(env):
    staff = env["login"]("registrar")
    body = {"applicant_id": applicant_id(env), "declared_type": "certificate_of_enrollment",
            "issuing_school": "Sample Institute", "consent": True}
    res = staff.post("/api/applicant-submissions", json=body)
    assert res.status_code == 201, res.text
    sub = res.json()
    assert sub["uploaded_by"] == f"registrar@{DOMAIN}" and sub["status"] == "draft"
    url = f"/api/applicant-submissions/{sub['id']}"
    assert staff.post(f"{url}/pages", files={"file": ("p.jpg", JPEG, "image/jpeg")}).json()[
        "pages"] == 1
    assert staff.delete(f"{url}/pages/1").json()["pages"] == 0
    staff.post(f"{url}/pages", files={"file": ("p.jpg", JPEG, "image/jpeg")})
    assert staff.post(f"{url}/submit").json()["status"] == "queued"
    assert staff.post(f"{url}/submit").status_code == 409

    db = env["db"]
    row = db.get(ApplicantSubmission, sub["id"])
    assert row.applicant_id == applicant_id(env) and row.uploaded_by is not None
    started = db.scalar(select(AuditLog).where(AuditLog.action == "applicant_submission_started"))
    assert started.user_id == row.uploaded_by
    assert started.new_value["on_behalf_of"] == applicant_id(env)
    # The applicant sees it among their own submissions.
    mine = applicant(env).get("/api/applicant/submissions").json()
    assert [s["id"] for s in mine] == [sub["id"]]
    assert "uploaded_by" not in mine[0]


@pytest.mark.parametrize(("change", "code"), [
    ({"consent": False}, 422),
    ({"declared_type": "psa_birth_certificate"}, 422),
    ({"applicant_id": None}, 404),  # replaced below by a student's login
    ({"applicant_id": 999_999}, 404),
])
def test_staff_upload_needs_an_applicant_login_and_consent(env, change, code):
    staff = env["login"]("registrar")
    body = {"applicant_id": applicant_id(env), "declared_type": "transcript_of_records",
            "issuing_school": "Sample College", "consent": True, **change}
    if body["applicant_id"] is None:
        body["applicant_id"] = applicant_id(env, "maria.santos")
    assert staff.post("/api/applicant-submissions", json=body).status_code == code


def test_only_staff_upload_on_behalf(env):
    body = {"applicant_id": applicant_id(env), "declared_type": "transcript_of_records",
            "issuing_school": "Sample College", "consent": True}
    for handle in ("teacher", "maria.santos", "applicant"):
        assert env["login"](handle).post("/api/applicant-submissions",
                                         json=body).status_code == 403
    assert env["login"]("admin").post("/api/applicant-submissions", json=body).status_code == 201


# ---- the per-submission report ----------------------------------------------------------------

def test_report_has_both_passes_findings_versions_and_decisions(env):
    sub_id, intake_id = read_and_accept(env)
    job = claim_kind(env, "second")
    finish_second(env, job, {"reading": second_reading("OLD-2020-0047")})
    staff = env["login"]("registrar")
    res = staff.get(f"/api/applicant-submissions/{sub_id}/report")
    assert res.status_code == 200 and "attachment" in res.headers["content-disposition"]
    data = res.json()
    assert data["submission"]["applicant_email"] == f"applicant@{DOMAIN}"
    assert data["first_pass"]["fields"]["student_number"]["value"] == "OLD-2020-0042"
    assert data["second_pass"]["disagreements"][0]["second"] == "OLD-2020-0047"
    assert data["decision"]["reasons"][0]["code"] == "no_reference_profile"
    assert data["versions"]["reference"] is None
    assert data["review"]["status"] == "accepted" and data["review"]["reason"] == REASON
    assert data["submission"]["attempts"] == 1
    assert "not proof of authenticity" in data["note"]
    assert {"applicant_submission_claimed", "intake_document_reviewed",
            "applicant_second_pass_disagrees"} <= {h["action"] for h in data["history"]}

    text = staff.get(f"/api/applicant-submissions/{sub_id}/report?format=csv").text
    rows = {r[0]: r[1] for r in csv.reader(io.StringIO(text))}
    assert rows["submission.id"] == str(sub_id)
    assert rows["second_pass.disagreements[0].field"] == "student_number"
    assert staff.get(f"/api/applicant-submissions/{sub_id}/report?format=xml").status_code == 422
    exported = env["db"].scalars(select(AuditLog).where(
        AuditLog.action == "applicant_submission_report_exported")).all()
    assert [e.new_value["format"] for e in exported] == ["json", "csv"]
    assert_no_values_in_audit(env["db"])


def test_report_of_a_submission_not_read_yet(env):
    sub_id = submitted(env)
    data = env["login"]("registrar").get(f"/api/applicant-submissions/{sub_id}/report").json()
    assert data["first_pass"] is None and data["decision"] is None
    assert data["submission"]["status"] == "queued"


def test_only_staff_get_the_report(env):
    sub_id = submitted(env)
    for handle in ("applicant", "teacher", "maria.santos"):
        res = env["login"](handle).get(f"/api/applicant-submissions/{sub_id}/report")
        assert res.status_code == 403


@pytest.mark.skipif(not weasyprint_available(), reason="WeasyPrint system libraries missing")
def test_report_as_pdf(env):
    sub_id, _ = read_and_accept(env)
    res = env["login"]("registrar").get(f"/api/applicant-submissions/{sub_id}/report?format=pdf")
    assert res.status_code == 200 and res.content.startswith(b"%PDF")


def test_report_pdf_without_the_renderer_says_so(env, monkeypatch):
    from app.api import applicant_intake as api
    from app.documents.pdf import RendererUnavailable

    def unavailable(*args, **kwargs):
        raise RendererUnavailable("no WeasyPrint here")

    monkeypatch.setattr(api, "render_pdf", unavailable)
    sub_id = submitted(env)
    res = env["login"]("registrar").get(f"/api/applicant-submissions/{sub_id}/report?format=pdf")
    assert res.status_code == 503
    # The HTML handed to the renderer escapes what was read.
    from app.services import applicant_report
    page = applicant_report.as_html({"note": "n", "submission": {"id": 1},
                                     "decision": {"decision": "manual_review", "reasons": [
                                         {"code": "x", "text": "<b>read</b>"}]}})
    assert "&lt;b&gt;read&lt;/b&gt;" in page and "<script" not in page


# ---- notifications ----------------------------------------------------------------------------

def titles(client):
    return [n["title"] for n in client.get("/api/notifications").json()]


def test_intake_notifications_for_staff_and_the_applicant_only(env, monkeypatch):
    other = second_applicant(env)
    sub_id, intake_id = read_and_accept(env)
    job = claim_kind(env, "second")
    finish_second(env, job, {"reading": second_reading("OLD-2020-0047")})
    monkeypatch.setattr(get_settings(), "applicant_max_attempts", 1)
    failing = submitted(env, other)
    complete(env, claim(env), {"error": "Tesseract crashed"})

    staff = env["login"]("registrar")
    seen = staff.get("/api/notifications").json()
    assert {"Online upload needs manual review", "Online upload could not be read",
            "OCR passes disagree: check the fields"} <= {n["title"] for n in seen}
    assert any(n["href"] == f"/intake/{intake_id}" for n in seen)
    assert all(REASON not in n["message"] for n in seen)

    mine = applicant(env).get("/api/notifications").json()
    assert [n["title"] for n in mine] == ["Your document was accepted",
                                          "Your document was read: waiting for the registrar's "
                                          "review"]
    assert all(n["href"] == "/" and REASON not in json.dumps(n) for n in mine)
    # The other applicant's submission failed: they hear nothing of the first one's.
    others = env["login"](other).get("/api/notifications").json()
    assert others == []
    # Marking someone else's event as read does nothing.
    client = env["login"](other)
    client.post("/api/notifications/read", json={"ids": [n["id"] for n in mine]})
    assert all(not n["read"] for n in applicant(env).get("/api/notifications").json())
    client = applicant(env)
    client.post("/api/notifications/read", json={"ids": [mine[0]["id"]]})
    assert [n["read"] for n in client.get("/api/notifications").json()] == [True, False]
    assert env["login"]("teacher").get("/api/notifications").json() == []
    assert failing


def test_rejection_and_resubmission_notifications_carry_no_staff_notes(env):
    other = second_applicant(env)
    first = submitted(env)
    intake_a = complete(env, claim(env)).json()["intake_id"]
    submitted(env, other)
    intake_b = complete(env, claim(env)).json()["intake_id"]
    staff = env["login"]("registrar")
    staff.post(f"/api/intake/documents/{intake_a}/review",
               json={"action": "reject", "reason": "Seal looks pasted on"})
    staff.post(f"/api/intake/documents/{intake_b}/review",
               json={"action": "request_resubmission", "reason": "Page 2 cut off"})
    mine = applicant(env).get("/api/notifications").json()
    assert mine[0]["title"] == "Your document was not accepted"
    assert "pasted" not in json.dumps(mine)
    theirs = env["login"](other).get("/api/notifications").json()
    assert theirs[0]["title"] == "Please submit your document again"
    assert "cut off" not in json.dumps(theirs)
    assert first


# ---- application history of a former applicant ------------------------------------------------

def test_a_former_applicant_keeps_a_read_only_history(env):
    sub_id, intake_id = read_and_accept(env)
    staff = env["login"]("registrar")
    staff.put(f"/api/intake/documents/{intake_id}/fields", json={"values": {
        "last_name": "Samplereyes", "first_name": "Luzviminda",
        "previous_student_number": "OLD-2020-0042"}})
    res = staff.post(f"/api/intake/documents/{intake_id}/create-student",
                     json={"student_number": "2026-90002"})
    assert res.status_code == 201, res.text

    student = env["login"]("applicant")  # the same login, now a student's
    history = student.get("/api/students/me/applications")
    assert history.status_code == 200
    [item] = history.json()
    assert item == {"id": sub_id, "declared_type": "transcript_of_records",
                    "issuing_school": "Sample College of Arts",
                    "status_text": "Accepted: linked to your student record",
                    "submitted_at": item["submitted_at"]}
    assert REASON not in json.dumps(item)
    # Read-only: the applicant routes are closed to a student login.
    assert student.post(f"/api/applicant/submissions/{sub_id}/withdraw").status_code == 403
    # Notifications about their own submissions stay with them.
    assert "Your document was accepted" in titles(student)
    # Another student sees nothing; staff and applicants can't call it.
    assert env["login"]("maria.santos").get("/api/students/me/applications").json() == []
    assert env["login"]("registrar").get("/api/students/me/applications").status_code == 403
