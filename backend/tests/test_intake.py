"""Incoming-document screening API (Part I): station intake, routing, review queue, settings.

Contract: docs/intake/screening-contract.md. All documents here are synthetic SAMPLEs.
"""

import io
import json

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, IntakeBatch, IntakeDocument, User
from app.seed import DEMO_PASSWORD, seed
from app.storage import get_storage
from tests.test_intake_decision import clean, indicator

DOMAIN = "school.edu.ph"


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("L", (60, 80), 230).save(out, "JPEG", quality=80)
    return out.getvalue()


JPEG = _jpeg()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))


@pytest.fixture
def env(client, db):
    seed(db)
    for user in db.scalars(select(User)):
        user.must_change_password = False
    db.commit()

    def login(handle):
        client.post("/api/auth/logout")
        res = client.post("/api/auth/login", json={"email": f"{handle}@{DOMAIN}",
                                                    "password": DEMO_PASSWORD})
        assert res.status_code == 200
        return client

    keys = [login("admin").post("/api/stations", json={"name": name}).json()["api_key"]
            for name in ("Intake Station", "Other Station")]
    client.post("/api/auth/logout")
    return {"client": client, "login": login, "key": keys[0], "other_key": keys[1], "db": db}


def station(env, method, path, key=None, **kw):
    return env["client"].request(method, f"/api/station/intake{path}",
                                 headers={"X-Station-Key": key or env["key"]}, **kw)


def submit(env, result=None, *, image=JPEG, batch_id=None, sequence=None, key=None,
           raw_result=None):
    data = {"result": raw_result if raw_result is not None else json.dumps(result or clean())}
    if batch_id is not None:
        data["batch_id"] = str(batch_id)
    if sequence is not None:
        data["sequence"] = str(sequence)
    return station(env, "POST", "/documents", key=key, data=data,
                   files={"image": ("page.jpg", image, "image/jpeg")})


def audits(db, action):
    return list(db.scalars(select(AuditLog).where(AuditLog.action == action)
                           .order_by(AuditLog.id)))


# ---- station: screening ---------------------------------------------------------------------

def test_clean_document_passes_screening_to_tray_1(env):
    res = submit(env)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body == {"intake_id": body["intake_id"], "decision": "passed_screening", "tray": 1,
                    "reasons": []}
    doc = env["db"].get(IntakeDocument, body["intake_id"])
    assert doc.status == "screened" and doc.doc_type == "psa_birth_certificate"
    assert doc.screening_result == clean()  # stored as received
    assert get_storage().get(doc.image_key)[:3] == b"\xff\xd8\xff"
    [entry] = audits(env["db"], "intake_document_screened")
    assert entry.new_value["decision"] == "passed_screening" and entry.new_value["tray"] == 1
    assert "SAMPLE" not in json.dumps(entry.new_value)  # no extracted field values in the log


def test_flagged_document_needs_manual_review_in_tray_2(env):
    result = clean(doc_type__confidence=0.6, indicators=[indicator(0.8)])
    body = submit(env, result).json()
    assert body["decision"] == "manual_review" and body["tray"] == 2
    assert body["reasons"] == ["doc_type_low_confidence", "severe_indicator"]


def test_unknown_keys_are_kept_but_ignored(env):
    result = clean()
    result["new_block"] = {"x": 1}
    res = submit(env, result)
    assert res.json()["decision"] == "passed_screening"
    assert env["db"].get(IntakeDocument, res.json()["intake_id"]).screening_result["new_block"]


@pytest.mark.parametrize("raw", ["not json", "[]", json.dumps(clean(quality=None)),
                                 json.dumps({"schema": 1})])
def test_malformed_result_is_refused_and_nothing_is_stored(env, raw):
    res = submit(env, raw_result=raw)
    assert res.status_code == 422
    assert env["db"].scalar(select(IntakeDocument)) is None


@pytest.mark.parametrize("image", [b"not an image", JPEG[:-40], b"GIF89a" + b"0" * 50])
def test_unreadable_image_is_refused(env, image):
    assert submit(env, image=image).status_code == 422
    assert env["db"].scalar(select(IntakeDocument)) is None


def test_station_routes_need_a_valid_key(env):
    assert submit(env, key="nope").status_code == 401
    assert env["client"].post("/api/station/intake/batches").status_code == 401


# ---- station: routing -----------------------------------------------------------------------

def test_routed_to_the_assigned_tray_keeps_the_decision(env):
    intake_id = submit(env).json()["intake_id"]
    res = station(env, "POST", f"/documents/{intake_id}/routed", json={"tray": 1})
    assert res.status_code == 200
    assert res.json()["decision"] == "passed_screening" and res.json()["routed_tray"] == 1
    # a retried report is harmless; a different one is refused
    url = f"/documents/{intake_id}/routed"
    assert station(env, "POST", url, json={"tray": 1}).status_code == 200
    assert station(env, "POST", url, json={"tray": 2}).status_code == 409
    assert len(audits(env["db"], "intake_document_routed")) == 1


def test_routing_mismatch_sends_the_document_to_manual_review(env):
    batch_id = station(env, "POST", "/batches").json()["batch_id"]
    intake_id = submit(env, batch_id=batch_id, sequence=1).json()["intake_id"]
    res = station(env, "POST", f"/documents/{intake_id}/routed", json={"tray": 2})
    assert res.status_code == 200
    assert res.json()["decision"] == "manual_review"
    assert res.json()["reasons"] == ["routed_tray_mismatch"]
    batch = env["db"].get(IntakeBatch, batch_id)
    assert (batch.passed_count, batch.manual_review_count) == (0, 1)
    [entry] = audits(env["db"], "intake_routing_issue")
    assert entry.old_value["decision"] == "passed_screening"
    assert entry.new_value["routed_tray"] == 2 and entry.new_value["assigned_tray"] == 1


@pytest.mark.parametrize("body", [{"jam": True}, {"tray": 1, "jam": True, "detail": "SAMPLE jam"}])
def test_a_jam_sends_the_document_to_manual_review(env, body):
    intake_id = submit(env).json()["intake_id"]
    res = station(env, "POST", f"/documents/{intake_id}/routed", json=body)
    assert res.json()["decision"] == "manual_review" and res.json()["jam"] is True
    assert res.json()["reasons"] == ["routing_jam"]


def test_manual_review_routed_to_tray_1_keeps_its_reasons_and_adds_the_mismatch(env):
    intake_id = submit(env, clean(quality__ok=False)).json()["intake_id"]
    res = station(env, "POST", f"/documents/{intake_id}/routed", json={"tray": 1})
    assert res.json()["reasons"] == ["quality_not_ok", "routed_tray_mismatch"]


@pytest.mark.parametrize("body", [{}, {"tray": 3}, {"tray": 0}, {"tray": "left"}])
def test_routed_report_must_name_a_tray_or_a_jam(env, body):
    intake_id = submit(env).json()["intake_id"]
    assert station(env, "POST", f"/documents/{intake_id}/routed", json=body).status_code == 422


def test_a_station_cannot_touch_another_stations_documents(env):
    intake_id = submit(env).json()["intake_id"]
    batch_id = station(env, "POST", "/batches").json()["batch_id"]
    other = env["other_key"]
    assert station(env, "POST", f"/documents/{intake_id}/routed", key=other,
                   json={"tray": 1}).status_code == 404
    assert station(env, "POST", f"/batches/{batch_id}/end", key=other).status_code == 404
    assert submit(env, batch_id=batch_id, key=other).status_code == 404


# ---- station: mixed batches (S-10) ----------------------------------------------------------

def test_mixed_batch_flow(env):
    res = station(env, "POST", "/batches")
    assert res.status_code == 201
    batch_id = res.json()["batch_id"]
    results = [clean(), clean(doc_type__predicted="jhs_report_card"),
               clean(doc_type__predicted="unknown"),
               clean(doc_type__predicted="good_moral_certificate")]
    ids = []
    for seq, result in enumerate(results, start=1):
        body = submit(env, result, batch_id=batch_id, sequence=seq).json()
        ids.append(body["intake_id"])
        station(env, "POST", f"/documents/{body['intake_id']}/routed",
                json={"tray": body["tray"]})
    summary = station(env, "POST", f"/batches/{batch_id}/end").json()
    assert summary["total"] == 4 and summary["passed_screening"] == 3
    assert summary["manual_review"] == 1 and summary["routed"] == 4
    assert summary["unrouted"] == 0 and summary["routing_issues"] == 0
    assert summary["ended_at"] is not None
    # ending twice is harmless; no more documents into an ended batch
    assert station(env, "POST", f"/batches/{batch_id}/end").json()["total"] == 4
    assert len(audits(env["db"], "intake_batch_ended")) == 1
    assert submit(env, batch_id=batch_id).status_code == 409
    docs = env["db"].scalars(select(IntakeDocument).order_by(IntakeDocument.id)).all()
    assert [d.sequence for d in docs] == [1, 2, 3, 4]
    assert [d.doc_type for d in docs] == ["psa_birth_certificate", "jhs_report_card", "unknown",
                                          "good_moral_certificate"]

    env["login"]("registrar")
    queue = env["client"].get(f"/api/intake/documents?batch_id={batch_id}").json()
    assert [r["id"] for r in queue] == ids[::-1]  # newest first


def test_a_new_batch_ends_the_one_left_open(env):
    first = station(env, "POST", "/batches").json()["batch_id"]
    second = station(env, "POST", "/batches").json()["batch_id"]
    assert env["db"].get(IntakeBatch, first).ended_at is not None
    assert env["db"].get(IntakeBatch, second).ended_at is None
    assert audits(env["db"], "intake_batch_started")[-1].new_value["ended_open_batches"] == [first]


def test_unknown_batch_is_not_found(env):
    assert submit(env, batch_id=999).status_code == 404
    assert station(env, "POST", "/batches/999/end").status_code == 404


# ---- staff: queue, detail, image ------------------------------------------------------------

def test_queue_filters(env):
    passed = submit(env).json()["intake_id"]
    flagged = submit(env, clean(doc_type__predicted="jhs_report_card",
                                checks__layout_score=0.2)).json()["intake_id"]
    client = env["login"]("registrar")

    def ids(query=""):
        res = client.get(f"/api/intake/documents{query}")
        assert res.status_code == 200, res.text
        return [r["id"] for r in res.json()]

    assert ids() == [flagged, passed]
    assert ids("?decision=manual_review") == [flagged]
    assert ids("?doc_type=psa_birth_certificate") == [passed]
    assert ids("?status=screened") == [flagged, passed]
    assert ids("?status=accepted") == []
    assert client.get("/api/intake/documents?status=authentic").status_code == 422
    client.post(f"/api/intake/documents/{passed}/review", json={"action": "accept"})
    assert ids("?status=accepted") == [passed]


def test_detail_explains_the_decision_in_plain_language(env):
    result = clean(checks__expected_headings_found=2, indicators=[indicator(0.7)])
    intake_id = submit(env, result).json()["intake_id"]
    client = env["login"]("registrar")
    detail = client.get(f"/api/intake/documents/{intake_id}").json()
    assert [r["code"] for r in detail["reasons_explained"]] == ["severe_indicator",
                                                               "expected_headings_missing"]
    assert all(r["text"] and r["text"] != r["code"] for r in detail["reasons_explained"])
    assert detail["indicators"][0]["code"] == "altered_region"
    assert detail["checks"]["layout_score"] == 0.88 and detail["quality"]["ok"] is True
    assert detail["similarity"]["score"] == 0.84 and detail["has_image"] is True
    assert detail["station"] == "Intake Station"
    assert [h["action"] for h in detail["history"]] == ["intake_document_screened"]
    text = json.dumps(detail["reasons_explained"]) + detail["note"]
    assert "authentic" not in text.replace("does not mean the document is authentic", "")


def test_image_is_streamed_by_the_api(env):
    intake_id = submit(env).json()["intake_id"]
    client = env["login"]("registrar")
    res = client.get(f"/api/intake/documents/{intake_id}/image")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert res.headers["cache-control"] == "no-store"
    assert res.content == get_storage().get(env["db"].get(IntakeDocument, intake_id).image_key)
    assert client.get("/api/intake/documents/999/image").status_code == 404


# ---- staff: review --------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["reject", "request_resubmission"])
def test_reject_and_resubmission_need_a_reason(env, action):
    intake_id = submit(env).json()["intake_id"]
    client = env["login"]("registrar")
    url = f"/api/intake/documents/{intake_id}/review"
    for body in ({"action": action}, {"action": action, "reason": "  "},
                 {"action": action, "reason": "no"}):
        assert client.post(url, json=body).status_code == 422
    res = client.post(url, json={"action": action, "reason": "Photo copy, not the original"})
    assert res.status_code == 200
    expected = "rejected" if action == "reject" else "resubmission_requested"
    assert res.json()["status"] == expected
    [entry] = audits(env["db"], "intake_document_reviewed")
    assert entry.user_id is not None and entry.old_value == {"status": "screened"}
    assert entry.new_value["status"] == expected
    assert entry.new_value["reason"] == "Photo copy, not the original"
    assert client.post(url, json={"action": "accept"}).status_code == 409  # already reviewed


def test_accepting_a_passed_document_needs_no_reason(env):
    intake_id = submit(env).json()["intake_id"]
    client = env["login"]("registrar")
    res = client.post(f"/api/intake/documents/{intake_id}/review", json={"action": "accept"})
    assert res.status_code == 200 and res.json()["status"] == "accepted"
    detail = client.get(f"/api/intake/documents/{intake_id}").json()
    assert detail["review"]["reviewed_by"] == f"registrar@{DOMAIN}"
    assert [h["action"] for h in detail["history"]] == ["intake_document_screened",
                                                       "intake_document_reviewed"]


def test_accepting_a_document_that_needs_manual_review_needs_a_reason(env):
    intake_id = submit(env, clean(quality__ok=False)).json()["intake_id"]
    client = env["login"]("registrar")
    url = f"/api/intake/documents/{intake_id}/review"
    assert client.post(url, json={"action": "accept"}).status_code == 422
    res = client.post(url, json={"action": "accept", "reason": "Checked the original by hand"})
    assert res.status_code == 200 and res.json()["status"] == "accepted"
    assert res.json()["decision"] == "manual_review"  # the screening outcome is kept


def test_unknown_review_action_is_refused(env):
    intake_id = submit(env).json()["intake_id"]
    client = env["login"]("registrar")
    res = client.post(f"/api/intake/documents/{intake_id}/review",
                      json={"action": "verify", "reason": "looks fine"})
    assert res.status_code == 422


# ---- RBAC -----------------------------------------------------------------------------------

@pytest.mark.parametrize(("handle", "queue", "settings"), [
    ("registrar", 200, 403), ("admin", 200, 200), ("teacher", 403, 403),
    ("maria.santos", 403, 403)])
def test_who_may_reach_the_queue_and_settings(env, handle, queue, settings):
    intake_id = submit(env).json()["intake_id"]
    client = env["login"](handle)
    assert client.get("/api/intake/documents").status_code == queue
    assert client.get(f"/api/intake/documents/{intake_id}").status_code == queue
    assert client.get(f"/api/intake/documents/{intake_id}/image").status_code == queue
    assert client.get("/api/intake/settings").status_code == settings


def test_registrar_without_the_validation_permission_still_reviews(env):
    registrar = env["db"].scalar(select(User).where(User.email == f"registrar@{DOMAIN}"))
    registrar.can_validate = False
    env["db"].commit()
    intake_id = submit(env).json()["intake_id"]
    client = env["login"]("registrar")
    assert client.post(f"/api/intake/documents/{intake_id}/review",
                       json={"action": "accept"}).status_code == 200


def test_a_user_session_cannot_call_station_routes(env):
    client = env["login"]("admin")
    assert client.post("/api/station/intake/batches").status_code == 401


# ---- settings (admin) -----------------------------------------------------------------------

def test_admin_changes_thresholds_and_the_next_decision_uses_them(env):
    client = env["login"]("admin")
    current = client.get("/api/intake/settings").json()
    assert current["min_layout_score"] == 0.70 and current["defaults"]["min_layout_score"] == 0.70
    new = {"min_doc_type_confidence": 0.8, "max_indicator_severity": 0.4,
           "min_layout_score": 0.9, "min_similarity_score": 0.6}
    assert client.put("/api/intake/settings", json=new).status_code == 422  # reason required
    res = client.put("/api/intake/settings", json={**new, "reason": "Pilot week calibration"})
    assert res.status_code == 200 and res.json()["min_layout_score"] == 0.9
    [entry] = audits(env["db"], "intake_settings_changed")
    assert entry.old_value["min_layout_score"] == 0.70
    assert entry.new_value["reason"] == "Pilot week calibration"
    body = submit(env).json()  # layout 0.88 < 0.9 now
    assert body["decision"] == "manual_review" and body["reasons"] == ["layout_score_low"]
    screened = audits(env["db"], "intake_document_screened")[-1]
    assert screened.new_value["thresholds"]["min_layout_score"] == 0.9


@pytest.mark.parametrize("change", [{"min_layout_score": 1.5}, {"max_indicator_severity": 0},
                                    {"min_similarity_score": -0.1}])
def test_thresholds_must_be_between_0_and_1(env, change):
    client = env["login"]("admin")
    body = {"min_doc_type_confidence": 0.75, "max_indicator_severity": 0.5,
            "min_layout_score": 0.7, "min_similarity_score": 0.6, "reason": "tuning", **change}
    assert client.put("/api/intake/settings", json=body).status_code == 422
