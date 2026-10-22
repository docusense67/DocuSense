"""Epic 4: request lifecycle (approve-first order, S-11), eligibility, verification, release,
downloads (UC1-UC3)."""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.documents.pdf import RenderedPdf
from app.models import (
    AuditLog,
    DocumentTemplate,
    Enrollment,
    GeneratedDocument,
    Grade,
    Student,
    User,
)
from app.models.enums import GradeRemark, StudentStatus, TemplateStatus
from app.security import sign_payload
from app.seed import DEMO_PASSWORD, seed
from app.timeutil import utcnow

DOMAIN = "school.edu.ph"


def fake_render(html: str, left: str, right: str) -> RenderedPdf:
    return RenderedPdf(content=b"%PDF-1.7 " + left.encode(), page_count=1)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))
    monkeypatch.setattr("app.documents.service.render_pdf", fake_render)


@pytest.fixture
def seeded(db):
    seed(db)
    for user in db.scalars(select(User)):
        user.must_change_password = False
    db.commit()
    return db


@pytest.fixture
def as_user(client, seeded):
    def _as(handle: str):
        client.post("/api/auth/logout")
        res = client.post("/api/auth/login",
                          json={"email": f"{handle}@{DOMAIN}", "password": DEMO_PASSWORD})
        assert res.status_code == 200, res.text
        return client
    return _as


def student(db, number: str) -> Student:
    return db.scalar(select(Student).where(Student.student_number == number))


def request_doc(client, doc_type="TOR", **extra):
    return client.post("/api/requests",
                       json={"document_type": doc_type, "purpose": "Employment", **extra})


def approve(client, req_id: int):
    """Registrar approves the request: retrieval + generation run now (S-11)."""
    return client.post(f"/api/requests/{req_id}/approve")


def submit_and_approve(as_user, doc_type="TOR", handle="maria.santos"):
    req = request_doc(as_user(handle), doc_type).json()
    body = approve(as_user("registrar"), req["id"]).json()
    assert body["status"] == "for_verification", body
    return body


def submit_and_release(as_user, doc_type="TOR"):
    req = submit_and_approve(as_user, doc_type)
    as_user("registrar").post(f"/api/requests/{req['id']}/verify")
    return req


# ---- UC1: request + eligibility -------------------------------------------------------------

def test_uc1_eligible_request_waits_for_registrar_approval(as_user, seeded):
    res = request_doc(as_user("maria.santos"))
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "requested" and body["client_status"] == "pending"
    assert body["reference_no"].startswith("REQ-") and body["reference_no"].endswith("000001")
    # S-11: nothing is retrieved or generated, and no document number is used, before approval.
    assert body["document"] is None
    assert seeded.scalar(select(GeneratedDocument)) is None
    actions = set(seeded.scalars(select(AuditLog.action)))
    assert "eligibility_checked" in actions and "document_generated" not in actions


def test_registrar_approval_runs_retrieval_and_generation(as_user, seeded):
    req = request_doc(as_user("maria.santos")).json()
    registrar = as_user("registrar")
    queue = registrar.get("/api/requests?status=requested").json()
    assert [r["id"] for r in queue] == [req["id"]]

    body = approve(registrar, req["id"]).json()
    assert body["status"] == "for_verification" and body["client_status"] == "approved"
    assert body["document"]["document_number"].startswith("TOR-")
    assert body["document"]["status"] == "generated"
    assert body["document"]["can_download"] is False  # not verified/released yet
    approved = seeded.scalar(select(AuditLog).where(AuditLog.action == "request_approved"))
    assert approved.user_id == seeded.scalar(
        select(User.id).where(User.email == f"registrar@{DOMAIN}"))
    assert approve(registrar, req["id"]).status_code == 409  # approve the request only once
    # The student sees "Approved" while the document is being prepared and checked.
    mine = as_user("maria.santos").get("/api/requests").json()[0]
    assert mine["client_status"] == "approved"


def test_uc1_inc_grade_rejects_with_reason(as_user):
    body = request_doc(as_user("ana.garcia")).json()
    assert body["status"] == "rejected" and body["client_status"] == "rejected"
    assert "Incomplete grade in GE101" in body["reject_reason"]
    assert body["document"] is None


def test_approval_rechecks_eligibility(as_user, seeded):
    """Records can change between submission and approval: approval checks them again."""
    req = request_doc(as_user("maria.santos")).json()
    grade = seeded.scalars(select(Grade).join(Enrollment).where(
        Enrollment.student_id == student(seeded, "2025-00001").id,
        Grade.is_locked.is_(True))).first()
    grade.grade_value, grade.remarks = None, GradeRemark.INC
    seeded.commit()
    body = approve(as_user("registrar"), req["id"]).json()
    assert body["status"] == "rejected" and "Incomplete grade" in body["reject_reason"]
    assert body["document"] is None


def test_duplicate_pending_request_blocked(as_user):
    client = as_user("maria.santos")
    assert request_doc(client).status_code == 201
    res = request_doc(client)
    assert res.status_code == 409 and "already pending" in res.json()["detail"]
    assert request_doc(client, "COE").status_code == 201  # different type is fine


def test_coe_requires_active_enrolled_student(as_user, seeded):
    juan = student(seeded, "2025-00002")
    juan.status = StudentStatus.INACTIVE
    seeded.commit()
    assert request_doc(as_user("maria.santos"), "COE").json()["status"] == "requested"
    body = request_doc(as_user("juan.delacruz"), "COE").json()
    assert body["status"] == "rejected"
    assert "not currently active" in body["reject_reason"]


def test_students_only_request_for_themselves(as_user, seeded):
    other = student(seeded, "2025-00002")
    res = request_doc(as_user("maria.santos"), student_id=other.id)
    assert res.status_code == 403
    assert request_doc(as_user("teacher")).status_code == 403


def test_students_cannot_approve_their_own_request(as_user):
    req = request_doc(as_user("maria.santos")).json()
    client = as_user("maria.santos")
    for action in ("approve", "verify", "release", "confirm-payment"):
        assert client.post(f"/api/requests/{req['id']}/{action}").status_code == 403


def test_registrar_files_walk_in_request(as_user, seeded):
    client = as_user("registrar")
    assert request_doc(client).status_code == 422  # needs student_id
    res = request_doc(client, "COE", student_id=student(seeded, "2025-00001").id)
    assert res.status_code == 201 and res.json()["status"] == "requested"
    assert approve(client, res.json()["id"]).json()["status"] == "for_verification"


def test_generation_blocked_then_regenerated(as_user, seeded):
    seeded.execute(update(DocumentTemplate).values(status=TemplateStatus.RETIRED))
    seeded.commit()
    req = request_doc(as_user("maria.santos")).json()
    registrar = as_user("registrar")
    body = approve(registrar, req["id"]).json()
    assert body["status"] == "approved" and body["client_status"] == "approved"
    assert "No active TOR template" in body["processing_note"]

    seeded.execute(update(DocumentTemplate).where(DocumentTemplate.version == 1)
                   .values(status=TemplateStatus.ACTIVE))
    seeded.commit()
    res = registrar.post(f"/api/requests/{body['id']}/regenerate",
                         json={"reason": "Template re-activated"})
    assert res.json()["status"] == "for_verification" and res.json()["processing_note"] is None


def test_payment_required_mode(as_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "payment_required", True)
    body = request_doc(as_user("maria.santos")).json()
    assert body["status"] == "requested" and body["document"] is None
    registrar = as_user("registrar")
    res = approve(registrar, body["id"])
    assert res.status_code == 409 and "payment" in res.json()["detail"]
    res = registrar.post(f"/api/requests/{body['id']}/confirm-payment")
    assert res.json()["status"] == "requested" and res.json()["payment_confirmed"]
    assert registrar.post(f"/api/requests/{body['id']}/confirm-payment").status_code == 409
    assert approve(registrar, body["id"]).json()["status"] == "for_verification"


# ---- visibility -----------------------------------------------------------------------------

def test_students_see_only_their_requests(as_user):
    mine = request_doc(as_user("maria.santos")).json()
    client = as_user("juan.delacruz")
    assert client.get("/api/requests").json() == []
    assert client.get(f"/api/requests/{mine['id']}").status_code == 404
    assert client.post(f"/api/requests/{mine['id']}/cancel").status_code == 404
    assert as_user("teacher").get(f"/api/requests/{mine['id']}").status_code == 403


# ---- registrar decision on the request -------------------------------------------------------

def test_registrar_rejects_pending_request_with_reason(as_user, seeded):
    req = request_doc(as_user("maria.santos")).json()
    registrar = as_user("registrar")
    assert registrar.post(f"/api/requests/{req['id']}/reject", json={}).status_code == 422
    body = registrar.post(f"/api/requests/{req['id']}/reject",
                          json={"reason": "Wrong purpose stated"}).json()
    assert body["status"] == "rejected" and body["client_status"] == "rejected"
    assert body["reject_reason"] == "Wrong purpose stated" and body["document"] is None
    log = seeded.scalar(select(AuditLog).where(AuditLog.action == "request_rejected"))
    assert log.new_value["reason"] == "Wrong purpose stated" and log.user_id is not None
    assert approve(registrar, req["id"]).status_code == 409
    # The student sees the reason on their own list.
    mine = as_user("maria.santos").get("/api/requests").json()[0]
    assert mine["client_status"] == "rejected" and mine["reject_reason"] == "Wrong purpose stated"


def test_client_status_filter(as_user):
    request_doc(as_user("maria.santos"))
    submit_and_approve(as_user, "COE")
    registrar = as_user("registrar")
    pending = registrar.get("/api/requests?client_status=pending").json()
    approved = registrar.get("/api/requests?client_status=approved").json()
    assert [r["document_type"] for r in pending] == ["TOR"]
    assert [r["document_type"] for r in approved] == ["COE"]


# ---- UC2: verification of the generated document, then release -------------------------------

def test_uc2_verify_releases_with_30_day_window(as_user, seeded):
    req = submit_and_approve(as_user)
    registrar = as_user("registrar")
    queue = registrar.get("/api/requests?status=for_verification").json()
    assert [r["id"] for r in queue] == [req["id"]]

    body = registrar.post(f"/api/requests/{req['id']}/verify").json()
    assert body["status"] == "released" and body["client_status"] == "completed"
    doc = seeded.get(GeneratedDocument, body["document"]["id"])
    seeded.refresh(doc)
    assert doc.status == "released"
    window = doc.download_expires_at.replace(tzinfo=None) - doc.released_at.replace(tzinfo=None)
    assert window == timedelta(days=30)
    assert registrar.post(f"/api/requests/{req['id']}/verify").status_code == 409
    assert approve(registrar, req["id"]).status_code == 409


def test_verify_needs_an_approved_request(as_user):
    req = request_doc(as_user("maria.santos")).json()
    res = as_user("registrar").post(f"/api/requests/{req['id']}/verify")
    assert res.status_code == 409  # nothing generated before approval


def test_manual_release_mode(as_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "auto_release_on_approval", False)
    req = submit_and_approve(as_user)
    registrar = as_user("registrar")
    body = registrar.post(f"/api/requests/{req['id']}/verify").json()
    assert body["status"] == "verified" and body["client_status"] == "approved"
    body = registrar.post(f"/api/requests/{req['id']}/release").json()
    assert body["status"] == "released" and body["client_status"] == "completed"


def test_send_back_for_regeneration_issues_new_number(as_user, seeded):
    req = submit_and_approve(as_user)
    first_number = req["document"]["document_number"]
    registrar = as_user("registrar")
    assert registrar.post(f"/api/requests/{req['id']}/regenerate",
                          json={}).status_code == 422  # reason required
    body = registrar.post(f"/api/requests/{req['id']}/regenerate",
                          json={"reason": "Program name corrected"}).json()
    assert body["status"] == "for_verification"
    assert body["document"]["document_number"] != first_number
    voided = seeded.scalar(select(GeneratedDocument)
                           .where(GeneratedDocument.document_number == first_number))
    seeded.refresh(voided)
    assert voided.status == "revoked"  # can never become official
    assert voided.data_snapshot["document_number"] == first_number  # snapshot untouched


def test_reject_at_verification_then_regenerate(as_user, seeded):
    req = submit_and_approve(as_user)
    first_number = req["document"]["document_number"]
    registrar = as_user("registrar")
    body = registrar.post(f"/api/requests/{req['id']}/reject",
                          json={"reason": "Program name misspelled"}).json()
    assert body["status"] == "rejected" and body["reject_reason"] == "Program name misspelled"
    voided = seeded.scalar(select(GeneratedDocument)
                           .where(GeneratedDocument.document_number == first_number))
    seeded.refresh(voided)
    assert voided.status == "revoked"

    body = registrar.post(f"/api/requests/{req['id']}/regenerate",
                          json={"reason": "Program name corrected"}).json()
    assert body["status"] == "for_verification"
    assert body["document"]["document_number"] != first_number


def test_side_by_side_flags_records_changed_since_generation(as_user, seeded):
    req = submit_and_approve(as_user)
    registrar = as_user("registrar")
    detail = registrar.get(f"/api/requests/{req['id']}").json()
    assert detail["snapshot"]["student_name"] == "MARIA R. SANTOS"
    assert detail["current_data"]["gwa"] == detail["snapshot"]["gwa"]
    assert detail["differs_from_records"] is False

    # A post-issue correction to one of Maria's locked grades.
    grade = seeded.scalars(select(Grade).join(Enrollment).where(
        Enrollment.student_id == student(seeded, "2025-00001").id,
        Grade.is_locked.is_(True))).first()
    seeded.execute(update(Grade).where(Grade.id == grade.id).values(grade_value=5))
    seeded.commit()
    assert registrar.get(f"/api/requests/{req['id']}").json()["differs_from_records"] is True


def test_cancel_rules(as_user):
    client = as_user("maria.santos")
    req = request_doc(client).json()
    body = client.post(f"/api/requests/{req['id']}/cancel").json()
    assert body["status"] == "cancelled" and body["document"] is None

    approved = submit_and_approve(as_user)
    body = as_user("maria.santos").post(f"/api/requests/{approved['id']}/cancel").json()
    assert body["status"] == "cancelled" and body["document"]["status"] == "revoked"

    released = submit_and_release(as_user, "COE")
    client = as_user("maria.santos")
    assert client.post(f"/api/requests/{released['id']}/cancel").status_code == 409


def test_registrar_cancel_needs_a_reason(as_user, seeded):
    req = request_doc(as_user("maria.santos")).json()
    registrar = as_user("registrar")
    assert registrar.post(f"/api/requests/{req['id']}/cancel").status_code == 422
    res = registrar.post(f"/api/requests/{req['id']}/cancel",
                         json={"reason": "Student withdrew at the counter"})
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    log = seeded.scalar(select(AuditLog).where(AuditLog.action == "request_cancelled"))
    assert log.new_value["reason"] == "Student withdrew at the counter"


# ---- UC3: downloads -------------------------------------------------------------------------

def test_uc3_student_downloads_released_document(as_user, seeded):
    req = submit_and_release(as_user)
    client = as_user("maria.santos")
    doc_id = client.get("/api/requests").json()[0]["document"]["id"]
    link = client.post(f"/api/documents/{doc_id}/download-link")
    assert link.status_code == 200
    pdf = client.get(link.json()["url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["cache-control"] == "no-store"
    # inline so the registrar's review frame can display it (attachment would download)
    assert pdf.headers["content-disposition"].startswith("inline")
    assert seeded.scalar(select(AuditLog).where(AuditLog.action == "document_downloaded"))
    assert req["id"]
    # Section 16 student history: the download shows on the student's own request list,
    # and the registrar's own review downloads do not.
    registrar = as_user("registrar")
    registrar.get(registrar.post(f"/api/documents/{doc_id}/download-link").json()["url"])
    mine = as_user("maria.santos").get("/api/requests").json()[0]["document"]["downloads"]
    assert len(mine) == 1


def test_download_denied_before_release_but_registrar_can_review(as_user):
    req = submit_and_approve(as_user)
    doc_id = req["document"]["id"]
    assert as_user("maria.santos").post(
        f"/api/documents/{doc_id}/download-link").status_code == 404
    registrar = as_user("registrar")
    link = registrar.post(f"/api/documents/{doc_id}/download-link").json()["url"]
    assert registrar.get(link).status_code == 200


def test_links_are_user_bound_expiring_and_tamper_proof(as_user, seeded):
    submit_and_release(as_user)
    doc = seeded.scalar(select(GeneratedDocument))
    registrar = as_user("registrar")
    registrar_link = registrar.post(f"/api/documents/{doc.id}/download-link").json()["url"]

    client = as_user("maria.santos")
    assert client.get(registrar_link).status_code == 404  # bound to the registrar

    maria = seeded.scalar(select(User).where(User.email == f"maria.santos@{DOMAIN}"))
    secret = get_settings().secret_key
    expired = sign_payload({"d": doc.id, "u": maria.id, "exp": int(utcnow().timestamp()) - 1},
                           secret)
    assert client.get(f"/api/files/{expired}").status_code == 404
    forged = sign_payload({"d": doc.id, "u": maria.id, "exp": 9999999999}, "wrong-secret")
    assert client.get(f"/api/files/{forged}").status_code == 404
    other_student = as_user("juan.delacruz")
    assert other_student.post(f"/api/documents/{doc.id}/download-link").status_code == 404


def test_expiry_ends_downloads_but_not_validity(as_user, seeded):
    submit_and_release(as_user)
    doc = seeded.scalar(select(GeneratedDocument))
    doc.download_expires_at = utcnow() - timedelta(minutes=1)
    seeded.commit()

    client = as_user("maria.santos")
    assert client.get("/api/requests").json()[0]["status"] == "expired"
    assert client.get("/api/requests").json()[0]["client_status"] == "expired"
    assert client.post(f"/api/documents/{doc.id}/download-link").status_code == 404
    seeded.refresh(doc)
    assert doc.status == "released"  # still verifiable by scan (Section 14)

    res = as_user("registrar").post(f"/api/documents/{doc.id}/reopen-download",
                                    json={"reason": "Student asked at the window"})
    assert res.status_code == 200
    client = as_user("maria.santos")
    assert client.post(f"/api/documents/{doc.id}/download-link").status_code == 200


def test_revoke_released_document(as_user, seeded):
    submit_and_release(as_user)
    doc = seeded.scalar(select(GeneratedDocument))
    registrar = as_user("registrar")
    assert registrar.post(f"/api/documents/{doc.id}/revoke", json={}).status_code == 422
    res = registrar.post(f"/api/documents/{doc.id}/revoke",
                         json={"reason": "Issued with wrong grade"})
    assert res.json()["status"] == "revoked"
    client = as_user("maria.santos")
    assert client.get("/api/requests").json()[0]["status"] == "revoked"
    assert client.post(f"/api/documents/{doc.id}/download-link").status_code == 404


def test_document_timeline(as_user, seeded):
    submit_and_release(as_user)
    doc = seeded.scalar(select(GeneratedDocument))
    timeline = as_user("registrar").get(f"/api/documents/{doc.id}").json()["timeline"]
    actions = [t["action"] for t in timeline]
    for expected in ("request_submitted", "eligibility_checked", "request_approved",
                     "document_generated", "document_verified", "document_released"):
        assert expected in actions
    # S-11 order: the request is approved before the document is generated.
    assert actions.index("request_approved") < actions.index("document_generated")
    assert actions.index("document_generated") < actions.index("document_verified")


def test_numbering_skips_non_numeric_legacy_numbers(db):
    """Imported/legacy numbers like REQ-2026-OLD01 must not crash the sequence."""
    from app.models import DocumentRequest
    from app.services.numbering import next_number
    seed(db)
    student_id = db.scalar(select(Student.id))
    for ref in ("REQ-2026-000007", "REQ-2026-OLD01"):
        db.add(DocumentRequest(reference_no=ref, student_id=student_id, document_type="TOR",
                               purpose="x"))
    db.commit()
    assert next_number(db, DocumentRequest.reference_no, "REQ-2026-") == "REQ-2026-000008"
    assert next_number(db, DocumentRequest.reference_no, "REQ-2031-") == "REQ-2031-000001"
