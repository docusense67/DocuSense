"""Applicant intake, phase 2: reference samples through the API and the worker.

Lifecycle (upload -> read by the worker -> pending -> approved -> retired), versions, rules,
RBAC, and an upload that passes screening once its school has an approved reference.
Builds on the fixtures of test_applicant_intake.py; everything is a synthetic SAMPLE.
"""

import json

import pytest
from sqlalchemy import select

from app.models import ApplicantReference, AuditLog, IntakeDocument
from app.models.enums import ReferenceStatus
from app.services import intake as screening
from tests.test_applicant_assessment import layout, page
from tests.test_applicant_intake import (  # noqa: F401
    DOMAIN,
    JPEG,
    applicant,
    assert_no_values_in_audit,
    claim,
    complete,
    env,
    isolated,
    pdf,
    reading,
    submitted,
    worker,
)

SCHOOL = "Sample College of Arts"


def staff(env, handle="registrar"):
    return env["login"](handle)


def add_reference(client, school=SCHOOL, doc_type="transcript_of_records",
                  label="genuine_sample", use="training", rules=None, content=JPEG, **extra):
    data = {"issuing_school": school, "doc_type": doc_type, "label": label, "use": use,
            "rules": json.dumps(rules if rules is not None else {}), **extra}
    return client.post("/api/applicant-references", data=data,
                       files=[("file", ("sample.jpg", content, "image/jpeg"))])


def claim_kind(env, kinds, key=None):
    res = worker(env, "POST", "/claim", key=key, params={"kinds": kinds})
    assert res.status_code == 200, res.text
    return res.json()["job"]


def ref_station(env, method, path, key=None, **kw):
    return env["client"].request(method, f"/api/station/applicant-references{path}",
                                 headers={"X-Station-Key": key or env["key"],
                                          **kw.pop("headers", {})}, **kw)


def read_reference(env, ref_id, lay=None, **reading_kw):
    job = claim_kind(env, "reference")
    assert job["kind"] == "reference" and job["reference_id"] == ref_id
    body = {**reading(**reading_kw), "layout": lay or layout()}
    res = ref_station(env, "POST", f"/{ref_id}/complete",
                      json={"lease_token": job["lease_token"], "reading": body})
    assert res.status_code == 200, res.text
    return res.json()


def approved_reference(env, **kw):
    client = staff(env)
    res = add_reference(client, **kw)
    assert res.status_code == 201, res.text
    ref_id = res.json()["id"]
    assert read_reference(env, ref_id)["status"] == "pending"
    client = staff(env)
    res = client.post(f"/api/applicant-references/{ref_id}/approve",
                      json={"reason": "Compared with a sample from the school"})
    assert res.status_code == 200, res.text
    return ref_id


def test_reference_lifecycle_and_audit(env):
    client = staff(env)
    res = add_reference(client, rules={"field_labels": {"gwa": ["GWA"]}})
    assert res.status_code == 201, res.text
    ref = res.json()
    assert ref["status"] == "queued" and ref["version"] == 1 and ref["pages"] == 1
    assert ref["school_key"] == "sample college of arts"
    assert ref["rules"]["field_labels"] == {"gwa": ["GWA"]}
    assert "not proof of authenticity" in ref["note"]
    # Not read yet: it can't be approved.
    assert client.post(f"/api/applicant-references/{ref['id']}/approve",
                       json={"reason": "too early"}).status_code == 409

    job = claim_kind(env, "reference")
    assert job["pages"] == 1 and job["field_labels"] == {"gwa": ["GWA"]}
    assert claim_kind(env, "reference") is None  # leased
    page_res = ref_station(env, "GET", f"/{ref['id']}/pages/1",
                           headers={"X-Lease-Token": job["lease_token"]})
    assert page_res.status_code == 200
    assert ref_station(env, "GET", f"/{ref['id']}/pages/1",
                       headers={"X-Lease-Token": "wrong"}).status_code == 409
    bad = ref_station(env, "POST", f"/{ref['id']}/complete",
                      json={"lease_token": job["lease_token"], "reading": {"schema": 1}})
    assert bad.status_code == 422  # malformed: the lease is kept
    body = {**reading(), "layout": layout()}
    done = ref_station(env, "POST", f"/{ref['id']}/complete",
                       json={"lease_token": job["lease_token"], "reading": body})
    assert done.json()["status"] == "pending"

    client = staff(env)
    detail = client.get(f"/api/applicant-references/{ref['id']}").json()
    assert detail["read"] is True and detail["measured_pages"] == 1
    assert detail["reading"]["fields"]["student_number"]["value"] == "OLD-2020-0042"
    assert client.get(f"/api/applicant-references/{ref['id']}/pages/1").status_code == 200
    assert client.post(f"/api/applicant-references/{ref['id']}/approve",
                       json={}).status_code == 422  # a reason is required
    approved = client.post(f"/api/applicant-references/{ref['id']}/approve",
                           json={"reason": "Matches the school's own sample"}).json()
    assert approved["status"] == "approved" and approved["decided_by"] == f"registrar@{DOMAIN}"

    # Approved versions can't be edited: a new version replaces it, the old one is retired.
    assert client.patch(f"/api/applicant-references/{ref['id']}",
                        json={"rules": {"layout_tolerance": 0.3}}).status_code == 409
    newer = add_reference(client, replaces_id=str(ref["id"])).json()
    assert newer["version"] == 2 and newer["replaces_id"] == ref["id"]
    retired = client.post(f"/api/applicant-references/{ref['id']}/retire",
                          json={"reason": "Replaced by version 2"}).json()
    assert retired["status"] == "retired" and retired["status_reason"] == "Replaced by version 2"
    assert client.post(f"/api/applicant-references/{ref['id']}/retire",
                       json={"reason": "again"}).status_code == 409

    db = env["db"]
    actions = [a.action for a in db.scalars(select(AuditLog).where(
        AuditLog.entity_type == "applicant_reference", AuditLog.entity_id == str(ref["id"]))
        .order_by(AuditLog.id))]
    assert actions == ["applicant_reference_uploaded", "applicant_reference_claimed",
                       "applicant_reference_read", "applicant_reference_approved",
                       "applicant_reference_retired"]
    assert_no_values_in_audit(db)


def test_a_reference_that_is_not_approved_yet_can_be_edited(env):
    client = staff(env)
    ref_id = add_reference(client).json()["id"]
    res = client.patch(f"/api/applicant-references/{ref_id}",
                       json={"use": "held_out", "rules": {"required_fields": ["student_number"]},
                             "reason": "Keep it for evaluation"})
    assert res.status_code == 200, res.text
    assert res.json()["use"] == "held_out"
    assert res.json()["rules"]["required_fields"] == ["student_number"]
    assert client.patch(f"/api/applicant-references/{ref_id}",
                        json={"rules": {"layout_tolerance": 2}}).status_code == 422
    log = env["db"].scalar(select(AuditLog).where(AuditLog.action == "applicant_reference_edited"))
    assert log.new_value["changed"] == ["rules", "use"]


@pytest.mark.parametrize(("body", "code"), [
    ({"label": "other"}, 422),  # "other" samples are of type unknown
    ({"doc_type": "unknown"}, 422),  # a genuine sample must be a TOR or COE
    ({"doc_type": "psa_birth_certificate"}, 422),
    ({"rules": {"colour": "blue"}}, 422),
    ({"school": " "}, 422),
    ({"replaces_id": "999"}, 422),
    ({"label": "other", "doc_type": "unknown"}, 201),
    ({"label": "altered_sample", "use": "held_out"}, 201),
])
def test_reference_upload_is_validated(env, body, code):
    client = staff(env)
    assert add_reference(client, **body).status_code == code


def test_reference_upload_keeps_pdf_pages_and_the_size_limit(env, monkeypatch):
    from app.config import get_settings
    client = staff(env)
    res = client.post("/api/applicant-references", data={
        "issuing_school": SCHOOL, "doc_type": "transcript_of_records",
        "label": "genuine_sample"}, files=[("file", ("a.pdf", pdf(2), "application/pdf")),
                                           ("file", ("b.jpg", JPEG, "image/jpeg"))])
    assert res.status_code == 201 and res.json()["pages"] == 3
    monkeypatch.setattr(get_settings(), "applicant_upload_max_bytes", 100_000)
    big = add_reference(client, content=b"\xff\xd8\xff" + b"0" * 100_001)
    assert big.status_code == 413
    assert add_reference(client, content=b"GIF89a").status_code == 422


def test_only_staff_manage_references(env):
    for handle in ("teacher", "maria.santos", "applicant"):
        client = env["login"](handle)
        assert client.get("/api/applicant-references").status_code == 403
        assert add_reference(client).status_code == 403
    assert staff(env, "admin").get("/api/applicant-references").status_code == 200
    # Station routes need the station key and the lease.
    assert env["client"].post("/api/station/applicant-references/1/complete",
                              json={"lease_token": "x"}).status_code == 401


def test_failed_reference_reading_is_retried_then_queued_again_by_staff(env, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "applicant_max_attempts", 1)
    ref_id = add_reference(staff(env)).json()["id"]
    job = claim_kind(env, "reference")
    res = ref_station(env, "POST", f"/{ref_id}/complete",
                      json={"lease_token": job["lease_token"], "error": "Tesseract crashed"})
    assert res.json()["status"] == "failed"
    client = staff(env)
    assert client.get("/api/applicant-references", params={"state": "failed"}).json()[0][
        "error"] == "Tesseract crashed"
    res = client.post(f"/api/applicant-references/{ref_id}/retry", json={"reason": "fixed"})
    assert res.json()["status"] == "queued" and res.json()["attempts"] == 0
    assert claim_kind(env, "reference")["reference_id"] == ref_id


def test_first_readings_are_claimed_before_references(env):
    ref_id = add_reference(staff(env)).json()["id"]
    sub_id = submitted(env)
    first = claim_kind(env, "first,second,reference")
    assert first["kind"] == "first" and first["submission_id"] == sub_id
    assert claim_kind(env, "reference,first")["reference_id"] == ref_id
    assert worker(env, "POST", "/claim", params={"kinds": "bogus"}).status_code == 422
    # A phase 1 worker (no kinds) only ever gets first readings.
    assert claim(env) is None


def test_an_upload_matching_an_approved_reference_passes_screening(env):
    ref_id = approved_reference(env, rules={"field_labels": {"gwa": ["GWA"]}})
    sub_id = submitted(env)
    job = claim(env)
    assert job["field_labels"] == {"gwa": ["GWA"]}  # the school's printed labels
    assert job["classifier"][0]["id"] == ref_id and job["classifier"][0]["use"] == "training"
    res = complete(env, job, {"reading": {**reading(), "layout": layout()}})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["decision"] == "passed_screening" and out["reasons"] == []

    db = env["db"]
    doc = db.get(IntakeDocument, out["intake_id"])
    assessment = doc.screening_result["assessment"]
    assert assessment["reference"]["id"] == ref_id and assessment["findings"] == []
    log = db.scalar(select(AuditLog).where(AuditLog.action == "intake_document_screened"))
    assert log.new_value["reference"] == {"id": ref_id, "version": 1}
    assert_no_values_in_audit(db)

    client = staff(env)
    detail = client.get(f"/api/intake/documents/{doc.id}").json()
    assert detail["assessment"]["reference"]["version"] == 1
    assert "not mean the document is authentic" in detail["note"]
    # Passed screening: accepting it needs no reason; saving its fields accepts it too.
    fields = client.put(f"/api/intake/documents/{doc.id}/fields", json={"values": {
        "last_name": "Samplereyes", "first_name": "Luzviminda",
        "previous_student_number": "OLD-2020-0042", "school_name": SCHOOL}})
    assert fields.status_code == 200, fields.text
    assert applicant(env).get(f"/api/applicant/submissions/{sub_id}").json()[
        "status_text"] == "Accepted by the registrar"


def test_a_shifted_upload_needs_manual_review_with_the_reasons(env):
    approved_reference(env)
    submitted(env)
    shifted = {**reading(), "layout": layout(page(shift=0.3), page())}
    out = complete(env, claim(env), {"reading": shifted}).json()
    assert out["decision"] == "manual_review"
    assert out["reasons"] == [screening.PAGE_COUNT_DIFFERS, screening.LAYOUT_SHIFTED]
    detail = staff(env).get(f"/api/intake/documents/{out['intake_id']}").json()
    texts = [r["text"] for r in detail["reasons_explained"]]
    assert texts[0].startswith("The number of pages differs")


def test_retired_held_out_and_other_schools_references_are_not_compared_with(env):
    client = staff(env)
    retired = approved_reference(env)
    client = staff(env)
    client.post(f"/api/applicant-references/{retired}/retire", json={"reason": "outdated"})
    held = add_reference(client, use="held_out").json()["id"]
    read_reference(env, held)
    staff(env).post(f"/api/applicant-references/{held}/approve", json={"reason": "eval set"})
    approved_reference(env, school="Another Sample School")
    submitted(env)
    out = complete(env, claim(env), {"reading": {**reading(), "layout": layout()}}).json()
    assert out["reasons"] == [screening.NO_REFERENCE_PROFILE]


def test_rows_are_stored_with_enum_names(env):
    ref_id = add_reference(staff(env)).json()["id"]
    from sqlalchemy import text
    row = env["db"].execute(text(
        "SELECT label, use, status, doc_type FROM applicant_references WHERE id = :id"),
        {"id": ref_id}).one()
    assert tuple(row) == ("GENUINE_SAMPLE", "TRAINING", "QUEUED", "TRANSCRIPT_OF_RECORDS")
    assert env["db"].get(ApplicantReference, ref_id).status == ReferenceStatus.QUEUED
