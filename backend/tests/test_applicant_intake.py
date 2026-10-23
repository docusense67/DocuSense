"""Applicant intake (docs/applicant-intake.md): online uploads, the OCR worker, staff review.

Builds on the screening fixtures of test_intake.py. All people here are synthetic SAMPLEs.
"""

import io
import json
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.models import ApplicantSubmission, AuditLog, IntakeDocument, Student, User
from app.models.enums import (
    ApplicantSubmissionStatus,
    IntakeDecision,
    IntakeDocType,
    IntakeSource,
    Role,
)
from app.services import applicant_intake as rules
from app.services import intake as screening
from app.timeutil import utcnow
from tests.test_intake import DOMAIN, JPEG, env, isolated  # noqa: F401

TOR = IntakeDocType.TRANSCRIPT_OF_RECORDS
COE = IntakeDocType.CERTIFICATE_OF_ENROLLMENT
# Values that must never reach an audit entry.
PERSONAL = ("SAMPLEREYES", "Samplereyes", "LUZVIMINDA", "Luzviminda", "OLD-2020-0042")


def reading(predicted="transcript_of_records", confidence=0.9, quality_ok=True, fields=None):
    return {"schema": 1, "quality": {"ok": quality_ok},
            "doc_type": {"predicted": predicted, "confidence": confidence},
            "fields": fields if fields is not None else {
                "student_name": {"value": "SAMPLEREYES, LUZVIMINDA C.", "confidence": 0.82},
                "student_number": {"value": "OLD-2020-0042", "confidence": 0.9},
                "school_name": {"value": "SAMPLE COLLEGE OF ARTS", "confidence": 0.88}},
            "pages": [{"page": 1, "ocr_text": "TRANSCRIPT OF RECORDS SAMPLEREYES"}]}


def pdf(pages=2) -> bytes:
    images = [Image.new("L", (300, 420), 255) for _ in range(pages)]
    out = io.BytesIO()
    images[0].save(out, "PDF", save_all=True, append_images=images[1:])
    return out.getvalue()


def png(shade=255) -> bytes:
    out = io.BytesIO()
    Image.new("L", (300, 420), shade).save(out, "PNG")
    return out.getvalue()


def applicant(env, handle="applicant"):
    return env["login"](handle)


def start(client, declared="transcript_of_records", consent=True):
    return client.post("/api/applicant/submissions", json={
        "declared_type": declared, "issuing_school": "Sample College of Arts",
        "consent": consent})


def upload(client, sub_id, content=JPEG, name="page.jpg"):
    return client.post(f"/api/applicant/submissions/{sub_id}/pages",
                       files={"file": (name, content, "application/octet-stream")})


def submitted(env, handle="applicant", content=JPEG):
    client = applicant(env, handle)
    sub_id = start(client).json()["id"]
    assert upload(client, sub_id, content).status_code == 201
    assert client.post(f"/api/applicant/submissions/{sub_id}/submit").status_code == 200
    client.post("/api/auth/logout")
    return sub_id


def worker(env, method, path, key=None, **kw):
    return env["client"].request(method, f"/api/station/applicant-jobs{path}",
                                 headers={"X-Station-Key": key or env["key"],
                                          **kw.pop("headers", {})}, **kw)


def claim(env, key=None):
    res = worker(env, "POST", "/claim", key=key)
    assert res.status_code == 200, res.text
    return res.json()["job"]


def complete(env, job, body=None, key=None):
    return worker(env, "POST", f"/{job['submission_id']}/complete", key=key,
                  json={"lease_token": job["lease_token"], **(body or {"reading": reading()})})


def second_applicant(env):
    db = env["db"]
    client = env["login"]("registrar")
    res = client.post("/api/applicants", json={"email": f"other.applicant@{DOMAIN}",
                                               "temporary_password": "Temp-Password-123"})
    assert res.status_code == 201, res.text
    user = db.get(User, res.json()["id"])
    assert user.role == Role.APPLICANT and user.must_change_password
    user.must_change_password = False
    user.password_hash = db.scalar(select(User.password_hash).where(
        User.email == f"applicant@{DOMAIN}"))
    db.commit()
    return "other.applicant"


def assert_no_values_in_audit(db):
    text = json.dumps([[a.old_value, a.new_value] for a in db.scalars(select(AuditLog))])
    for value in PERSONAL:
        assert value not in text, value


# ---- pure rules -------------------------------------------------------------------------------

def test_every_online_document_needs_manual_review_until_references_exist():
    decision, reasons, read = rules.decide(reading(), TOR)
    assert decision == IntakeDecision.MANUAL_REVIEW
    assert reasons == [screening.NO_REFERENCE_PROFILE]
    assert read.doc_type == TOR and rules.document_type(read, TOR) == TOR


@pytest.mark.parametrize(("kwargs", "declared", "expected"), [
    ({"quality_ok": False}, TOR, screening.UPLOAD_QUALITY_NOT_OK),
    ({"predicted": "unknown"}, TOR, screening.DOC_TYPE_UNKNOWN),
    ({"predicted": "psa_birth_certificate"}, TOR, screening.DOC_TYPE_UNKNOWN),
    ({"predicted": "certificate_of_enrollment"}, TOR, screening.DECLARED_TYPE_MISMATCH),
    ({"confidence": 0.5}, TOR, screening.DOC_TYPE_LOW_CONFIDENCE),
])
def test_each_rule_has_a_reason_with_plain_text(kwargs, declared, expected):
    _, reasons, read = rules.decide(reading(**kwargs), declared)
    assert expected in reasons and expected in screening.REASON_TEXT
    # An unknown reading keeps the type the applicant chose; a mismatch keeps what was read.
    assert rules.document_type(read, declared) in rules.ONLINE_TYPES


@pytest.mark.parametrize("bad", [
    None, [], {"schema": 2}, {**reading(), "quality": {}},
    {**reading(), "doc_type": {"predicted": "", "confidence": 0.9}},
    {**reading(), "doc_type": {"predicted": "transcript_of_records", "confidence": 1.5}},
    {**reading(), "doc_type": {"predicted": "transcript_of_records", "confidence": True}},
    {**reading(), "fields": {"x": {"value": 42}}},
    {**reading(), "fields": {"x": {"value": "a" * 501}}},
    {**reading(), "fields": {"x": "not an object"}},
])
def test_malformed_readings_are_refused(bad):
    with pytest.raises(rules.InvalidReading):
        rules.decide(bad, TOR)


def test_upload_kind_trusts_only_the_file_content():
    assert rules.upload_kind(pdf()) == "pdf"
    assert rules.upload_kind(png()) == "png"
    assert rules.upload_kind(JPEG) == "jpg"
    with pytest.raises(rules.InvalidUpload):
        rules.upload_kind(b"GIF89a....")


def test_lease_rules():
    now = utcnow()
    token, token_hash, expires = rules.new_lease(now, 10)
    assert expires == now + timedelta(minutes=10)
    assert rules.lease_held(token_hash, expires, token, now)
    assert not rules.lease_held(token_hash, expires, "other", now)
    assert not rules.lease_held(token_hash, expires, token, expires)  # expired
    assert not rules.lease_held(None, expires, token, now)
    queued, processing = ApplicantSubmissionStatus.QUEUED, ApplicantSubmissionStatus.PROCESSING
    assert rules.claimable(queued, None, now)
    assert not rules.claimable(processing, expires, now)
    assert rules.claimable(processing, now, now)
    assert not rules.claimable(ApplicantSubmissionStatus.DRAFT, None, now)
    assert rules.after_failure(1, 3) == queued
    assert rules.after_failure(3, 3) == ApplicantSubmissionStatus.FAILED


# ---- applicant: own submissions only ----------------------------------------------------------

def test_applicant_uploads_pages_and_submits(env):
    client = applicant(env)
    res = start(client)
    assert res.status_code == 201, res.text
    sub = res.json()
    assert sub["status"] == "draft" and sub["pages"] == 0
    assert client.post(f"/api/applicant/submissions/{sub['id']}/submit").status_code == 409

    assert upload(client, sub["id"], JPEG).json()["pages"] == 1
    out = upload(client, sub["id"], pdf(2), "scan.pdf").json()
    assert out["pages"] == 3  # the PDF was split into its pages
    assert upload(client, sub["id"], JPEG).status_code == 409  # same file twice
    page = client.get(f"/api/applicant/submissions/{sub['id']}/pages/2")
    assert page.status_code == 200 and page.headers["content-type"] == "image/jpeg"

    assert client.delete(f"/api/applicant/submissions/{sub['id']}/pages/1").json()["pages"] == 2
    assert client.get(f"/api/applicant/submissions/{sub['id']}/pages/2").status_code == 200
    assert client.get(f"/api/applicant/submissions/{sub['id']}/pages/3").status_code == 404

    out = client.post(f"/api/applicant/submissions/{sub['id']}/submit").json()
    assert out["status"] == "queued" and out["status_text"].startswith("Submitted")
    assert upload(client, sub["id"], png(200)).status_code == 409  # no more pages once sent
    assert [s["id"] for s in client.get("/api/applicant/submissions").json()] == [sub["id"]]


@pytest.mark.parametrize(("body", "code"), [
    ({"declared_type": "psa_birth_certificate"}, 422),  # only TOR / COE from other schools
    ({"consent": False}, 422),
    ({"issuing_school": " "}, 422),
])
def test_start_needs_an_online_type_consent_and_a_school(env, body, code):
    client = applicant(env)
    payload = {"declared_type": "transcript_of_records", "issuing_school": "Sample College",
               "consent": True, **body}
    assert client.post("/api/applicant/submissions", json=payload).status_code == code


@pytest.mark.parametrize(("content", "code"), [
    (b"GIF89a not a page", 422),
    (b"%PDF-1.4 broken", 422),
    (png()[:-20], 422),  # truncated image
])
def test_unreadable_uploads_are_refused(env, content, code):
    client = applicant(env)
    sub_id = start(client).json()["id"]
    assert upload(client, sub_id, content).status_code == code
    assert client.get(f"/api/applicant/submissions/{sub_id}").json()["pages"] == 0


def test_upload_size_and_page_limits(env, monkeypatch):
    client = applicant(env)
    sub_id = start(client).json()["id"]
    monkeypatch.setattr(get_settings(), "applicant_upload_max_bytes", 100_000)
    assert upload(client, sub_id, b"\xff\xd8\xff" + b"0" * 100_001).status_code == 413
    monkeypatch.setattr(get_settings(), "applicant_max_pages", 2)
    assert upload(client, sub_id, pdf(3), "big.pdf").status_code == 422
    assert upload(client, sub_id, pdf(2), "ok.pdf").json()["pages"] == 2
    assert upload(client, sub_id, png()).status_code == 409


def test_applicants_only_see_their_own_submissions(env):
    other = second_applicant(env)
    mine = submitted(env)
    client = applicant(env, other)
    assert client.get("/api/applicant/submissions").json() == []
    for method, tail in (("GET", ""), ("GET", "/pages/1"), ("POST", "/submit"),
                         ("POST", "/withdraw"), ("DELETE", "/pages/1")):
        res = client.request(method, f"/api/applicant/submissions/{mine}{tail}")
        assert res.status_code == 404, (method, tail)


def test_withdraw_deletes_the_pages_before_they_are_read(env):
    sub_id = submitted(env)
    client = applicant(env)
    assert client.post(f"/api/applicant/submissions/{sub_id}/withdraw").json()["status"] == (
        "withdrawn")
    db = env["db"]
    db.expire_all()
    assert db.get(ApplicantSubmission, sub_id).pages == []
    assert claim(env) is None


def test_only_staff_create_applicant_logins(env):
    body = {"email": f"new.applicant@{DOMAIN}", "temporary_password": "Temp-Password-123"}
    for handle in ("teacher", "maria.santos", "applicant"):
        assert env["login"](handle).post("/api/applicants", json=body).status_code == 403
    assert env["login"]("admin").post("/api/applicants", json=body).status_code == 201
    # Registrars manage applicant logins like student and teacher logins.
    listed = env["login"]("registrar").get("/api/users").json()
    assert any(u["email"] == body["email"] for u in listed)


# ---- worker: lease, reading, retries ----------------------------------------------------------

def test_worker_reads_the_submission_into_the_staff_queue(env):
    sub_id = submitted(env)
    job = claim(env)
    assert job["submission_id"] == sub_id and job["pages"] == 1 and job["attempt"] == 1
    assert claim(env) is None  # leased: nobody else gets it

    no_token = worker(env, "GET", f"/{sub_id}/pages/1")
    assert no_token.status_code == 422
    page = worker(env, "GET", f"/{sub_id}/pages/1", headers={"X-Lease-Token": job["lease_token"]})
    assert page.status_code == 200
    wrong = worker(env, "GET", f"/{sub_id}/pages/1", headers={"X-Lease-Token": "nope"})
    assert wrong.status_code == 409
    other_station = worker(env, "GET", f"/{sub_id}/pages/1", key=env["other_key"],
                           headers={"X-Lease-Token": job["lease_token"]})
    assert other_station.status_code == 409

    res = complete(env, job)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "screened" and out["decision"] == "manual_review"
    assert out["reasons"] == [screening.NO_REFERENCE_PROFILE]
    assert complete(env, job).status_code == 409  # the lease is spent

    db = env["db"]
    doc = db.get(IntakeDocument, out["intake_id"])
    assert doc.source == IntakeSource.ONLINE and doc.submission_id == sub_id
    assert doc.doc_type == TOR and doc.batch_id is None

    staff = env["login"]("registrar")
    queue = staff.get("/api/intake/documents", params={"source": "online"}).json()
    assert [d["id"] for d in queue] == [doc.id]
    assert staff.get("/api/intake/documents", params={"source": "station"}).json() == []
    detail = staff.get(f"/api/intake/documents/{doc.id}").json()
    assert detail["submission"]["pages"] == 1
    assert detail["submission"]["applicant_email"] == f"applicant@{DOMAIN}"
    assert detail["reasons_explained"][0]["text"].startswith("There is no reference layout")
    assert staff.get(f"/api/applicant-submissions/{sub_id}/pages/1").status_code == 200
    assert_no_values_in_audit(db)

    status = applicant(env).get(f"/api/applicant/submissions/{sub_id}").json()
    assert status["status_text"] == "Waiting for the registrar's review"


def test_malformed_reading_is_refused_and_the_lease_kept(env):
    submitted(env)
    job = claim(env)
    bad = complete(env, job, {"reading": {"schema": 1}})
    assert bad.status_code == 422
    assert complete(env, job).status_code == 200  # same lease, a proper reading


def test_worker_errors_are_retried_then_the_submission_fails(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "applicant_max_attempts", 2)
    sub_id = submitted(env)
    first = claim(env)
    assert complete(env, first, {"error": "Tesseract crashed"}).json()["status"] == "queued"
    second = claim(env)
    assert second["attempt"] == 2
    assert complete(env, second, {"error": "Tesseract crashed again"}).json()["status"] == (
        "failed")
    assert claim(env) is None

    staff = env["login"]("registrar")
    row = staff.get("/api/applicant-submissions", params={"state": "failed"}).json()[0]
    assert row["id"] == sub_id and row["error"] == "Tesseract crashed again"
    assert staff.post(f"/api/applicant-submissions/{sub_id}/retry", json={}).status_code == 422
    res = staff.post(f"/api/applicant-submissions/{sub_id}/retry", json={"reason": "worker fixed"})
    assert res.json()["status"] == "queued" and res.json()["attempts"] == 0
    assert claim(env)["attempt"] == 1


def test_an_expired_lease_can_be_claimed_again(env):
    sub_id = submitted(env)
    stale = claim(env)
    db = env["db"]
    db.get(ApplicantSubmission, sub_id).lease_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    fresh = claim(env, key=env["other_key"])
    assert fresh["submission_id"] == sub_id and fresh["attempt"] == 2
    assert complete(env, stale).status_code == 409  # the stopped worker can't finish it
    assert complete(env, fresh, key=env["other_key"]).status_code == 200


def test_worker_routes_need_a_station_key(env):
    submitted(env)
    assert env["client"].post("/api/station/applicant-jobs/claim").status_code == 401
    assert env["login"]("registrar").post("/api/station/applicant-jobs/claim").status_code == 401


def test_online_documents_stay_out_of_the_station_operator_console(env):
    submitted(env)
    complete(env, claim(env))
    staff = env["login"]("registrar")
    station_id = env["db"].scalar(select(IntakeDocument.station_id))
    live = staff.get(f"/api/intake-station/stations/{station_id}/live").json()
    assert live["documents"] == []


# ---- staff review -> student ------------------------------------------------------------------

def test_accepted_upload_becomes_a_student_and_the_applicant_login_a_student_login(env):
    sub_id = submitted(env)
    intake_id = complete(env, claim(env)).json()["intake_id"]
    staff = env["login"]("registrar")
    db = env["db"]

    # Needs manual review: accepting it needs a reason.
    url = f"/api/intake/documents/{intake_id}"
    assert staff.post(f"{url}/review", json={"action": "accept"}).status_code == 422
    assert staff.post(f"{url}/review", json={"action": "accept",
                                             "reason": "Checked with the issuing school"}
                      ).status_code == 200
    fields = staff.get(f"{url}/fields").json()
    assert [s["key"] for s in fields["schema"]][:4] == [
        "last_name", "first_name", "middle_name", "previous_student_number"]
    assert fields["extracted"]["previous_student_number"]["value"] == "OLD-2020-0042"
    saved = staff.put(f"{url}/fields", json={"values": {
        "last_name": "Samplereyes", "first_name": "Luzviminda", "middle_name": None,
        "previous_student_number": "OLD-2020-0042", "school_name": "Sample College of Arts"}})
    assert saved.status_code == 200, saved.text
    res = staff.post(f"{url}/create-student", json={"student_number": "2026-90001"})
    assert res.status_code == 201, res.text

    student = db.scalar(select(Student).where(Student.student_number == "2026-90001"))
    user = db.scalar(select(User).where(User.email == f"applicant@{DOMAIN}"))
    assert student.user_id == user.id and user.role == Role.STUDENT
    assert student.last_name == "Samplereyes"
    assert_no_values_in_audit(db)

    me = env["login"]("applicant").get("/api/students/me")
    assert me.status_code == 200 and me.json()["student_number"] == "2026-90001"
    # A student login now: the applicant pages are closed to it.
    assert env["client"].get("/api/applicant/submissions").status_code == 403
    assert db.get(ApplicantSubmission, sub_id).status == ApplicantSubmissionStatus.SCREENED


def test_rejected_upload_tells_the_applicant_without_staff_notes(env):
    sub_id = submitted(env)
    intake_id = complete(env, claim(env)).json()["intake_id"]
    staff = env["login"]("registrar")
    staff.post(f"/api/intake/documents/{intake_id}/review",
               json={"action": "request_resubmission", "reason": "Page 2 is cut off"})
    out = applicant(env).get(f"/api/applicant/submissions/{sub_id}").json()
    assert out["status_text"] == "Please submit this document again"
    assert "cut off" not in json.dumps(out)
