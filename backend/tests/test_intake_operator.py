"""Operator console for the intake station (app/api/intake_operator.py).

The validation operator sees station status, the running batch, each page's type, decision,
tray and plain-language reasons, never extracted field values. All documents are SAMPLEs.
"""

import io
import json
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, ScanStation, User
from app.seed import DEMO_PASSWORD, seed
from app.timeutil import utcnow
from tests.test_intake_decision import clean, indicator

DOMAIN = "school.edu.ph"


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("L", (60, 80), 230).save(out, "JPEG", quality=80)
    return out.getvalue()


JPEG = _jpeg()
FLAGGED = clean(doc_type__confidence=0.6, indicators=[indicator(0.8)])


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

    admin = login("admin")
    stations = [admin.post("/api/stations", json={"name": name}).json()
                for name in ("Outgoing Station", "Intake Station")]
    client.post("/api/auth/logout")
    return {"client": client, "login": login, "db": db, "outgoing": stations[0],
            "intake": stations[1], "key": stations[1]["api_key"]}


def as_station(env, method, path, key=None, **kw):
    return env["client"].request(method, f"/api/station{path}",
                                 headers={"X-Station-Key": key or env["key"]}, **kw)


def submit(env, result=None, *, batch_id=None, sequence=None):
    data = {"result": json.dumps(result or clean())}
    if batch_id is not None:
        data["batch_id"] = str(batch_id)
    if sequence is not None:
        data["sequence"] = str(sequence)
    res = as_station(env, "POST", "/intake/documents", data=data,
                     files={"image": ("page.jpg", JPEG, "image/jpeg")})
    assert res.status_code == 201, res.text
    return res.json()


def routed(env, intake_id, **body):
    res = as_station(env, "POST", f"/intake/documents/{intake_id}/routed", json=body)
    assert res.status_code == 200, res.text


def batch_of_two(env):
    """Status + a batch with one page per tray, routed as decided."""
    as_station(env, "POST", "/status", json={"state": "scanning"})
    batch_id = as_station(env, "POST", "/intake/batches").json()["batch_id"]
    passed = submit(env, batch_id=batch_id, sequence=1)
    flagged = submit(env, FLAGGED, batch_id=batch_id, sequence=2)
    routed(env, passed["intake_id"], tray=1)
    routed(env, flagged["intake_id"], tray=2)
    return batch_id, passed["intake_id"], flagged["intake_id"]


def live(env, station_id=None, **params):
    res = env["client"].get(f"/api/intake-station/stations/{station_id or env['intake']['id']}"
                            "/live", params=params)
    assert res.status_code == 200, res.text
    return res.json()


# ---- live view ------------------------------------------------------------------------------

def test_live_view_shows_batch_trays_counts_and_reasons(env):
    batch_id, passed_id, flagged_id = batch_of_two(env)
    env["login"]("registrar")
    body = live(env)

    assert body["station"]["state"] == "scanning" and body["station"]["intake"] is True
    assert body["batch"]["batch_id"] == batch_id and body["batch"]["open"] is True
    assert body["batch"]["counts"] == {"total": 2, "passed_screening": 1, "manual_review": 1,
                                       "tray_1": 1, "tray_2": 1, "pending": 0, "jams": 0,
                                       "wrong_tray": 0}
    assert body["today"]["total"] == 2
    latest, first = body["documents"]  # newest first: the TFT shows the latest page big
    assert (latest["id"], latest["sequence"], latest["tray"]) == (flagged_id, 2, 2)
    assert latest["decision"] == "manual_review" and latest["routing"] == "ok"
    assert latest["doc_type"] == "psa_birth_certificate"
    assert [r["code"] for r in latest["reasons"]] == ["doc_type_low_confidence",
                                                     "severe_indicator"]
    assert latest["reasons"][0]["text"].startswith("The station was not confident")
    assert "indicator" not in latest["reasons"][1]["text"]  # operators never see indicators
    assert (first["id"], first["decision"], first["tray"], first["reasons"]) == \
        (passed_id, "passed_screening", 1, [])


def test_operator_never_receives_field_values_images_or_raw_results(env):
    batch_id, passed_id, _ = batch_of_two(env)
    client = env["login"]("registrar")
    responses = [
        client.get("/api/intake-station/stations"),
        client.get(f"/api/intake-station/stations/{env['intake']['id']}/live"),
        client.get(f"/api/intake-station/stations/{env['intake']['id']}/batches"),
        client.get(f"/api/intake-station/batches/{batch_id}"),
        client.post(f"/api/intake-station/batches/{batch_id}/end"),
    ]
    for res in responses:
        assert res.status_code == 200, res.text
        text = res.text
        # The SAMPLE result carries last_name "SAMPLE" and an indicator detail "SAMPLE".
        assert "SAMPLE" not in text
        for leak in ("fields", "screening_result", "indicators", "image", "last_name"):
            assert f'"{leak}' not in text, (leak, res.request.url)
    # The registrar's own review still has them (contrast, so the check above is meaningful).
    detail = client.get(f"/api/intake/documents/{passed_id}").json()
    assert detail["fields"]["last_name"]["value"] == "SAMPLE"


def test_between_batches_the_latest_station_pages_are_shown(env):
    batch_id, *_ = batch_of_two(env)
    as_station(env, "POST", f"/intake/batches/{batch_id}/end")
    loose = submit(env)  # a page without a batch
    env["login"]("registrar")
    body = live(env, limit=5)
    assert body["batch"]["open"] is False and body["batch"]["batch_id"] == batch_id
    assert [d["id"] for d in body["documents"]][0] == loose["intake_id"]
    assert len(body["documents"]) == 3


def test_running_batch_shows_only_its_own_pages(env):
    submit(env)  # an earlier loose page
    batch_id = as_station(env, "POST", "/intake/batches").json()["batch_id"]
    env["login"]("registrar")
    body = live(env)
    assert body["batch"]["batch_id"] == batch_id and body["documents"] == []
    assert body["batch"]["counts"]["total"] == 0 and body["today"]["total"] == 1


def test_jam_and_wrong_tray_tell_the_operator_where_the_page_belongs(env):
    batch_id = as_station(env, "POST", "/intake/batches").json()["batch_id"]
    jammed = submit(env, batch_id=batch_id, sequence=1)
    misrouted = submit(env, FLAGGED, batch_id=batch_id, sequence=2)
    waiting = submit(env, batch_id=batch_id, sequence=3)
    routed(env, jammed["intake_id"], jam=True, detail="gate stuck")
    routed(env, misrouted["intake_id"], tray=1)  # belongs in Tray 2
    env["login"]("registrar")
    docs = {d["id"]: d for d in live(env)["documents"]}
    assert docs[jammed["intake_id"]]["routing"] == "jam"
    assert docs[jammed["intake_id"]]["tray"] == 2  # a jam is always manual review
    assert "routing_jam" in [r["code"] for r in docs[jammed["intake_id"]]["reasons"]]
    assert docs[misrouted["intake_id"]]["routing"] == "wrong_tray"
    assert docs[misrouted["intake_id"]]["routed_tray"] == 1
    assert docs[misrouted["intake_id"]]["tray"] == 2
    assert docs[waiting["intake_id"]]["routing"] == "pending"
    counts = live(env)["batch"]["counts"]
    assert (counts["tray_1"], counts["tray_2"], counts["jams"], counts["wrong_tray"],
            counts["pending"]) == (1, 2, 1, 1, 1)


# ---- station status -------------------------------------------------------------------------

@pytest.mark.parametrize(("reported", "state"), [
    ("ready", "idle"), ("scanning", "scanning"), ("jam", "error"), ("double_feed", "error"),
    ("offline", "offline"),
])
def test_station_state_groups_the_agent_report(env, reported, state):
    as_station(env, "POST", "/status", json={"state": reported, "detail": "Camera not found"})
    env["login"]("registrar")
    station = live(env)["station"]
    assert station["state"] == state and station["status"] == reported
    assert station["status_detail"] == "Camera not found"


def test_silent_and_never_seen_stations(env):
    as_station(env, "POST", "/status", json={"state": "ready"})
    db = env["db"]
    row = db.get(ScanStation, env["intake"]["id"])
    row.last_seen_at = utcnow() - timedelta(minutes=5)
    db.commit()
    env["login"]("registrar")
    assert live(env)["station"]["state"] == "no_signal"
    assert live(env, env["outgoing"]["id"])["station"]["state"] == "unknown"


def test_station_list_puts_intake_stations_first(env):
    submit(env)
    stations = env["login"]("registrar").get("/api/intake-station/stations").json()
    assert [(s["name"], s["intake"]) for s in stations] == [("Intake Station", True),
                                                            ("Outgoing Station", False)]
    assert "api_key" not in stations[0] and "api_key_hash" not in stations[0]


def test_unknown_station_or_batch_is_404(env):
    client = env["login"]("registrar")
    assert client.get("/api/intake-station/stations/9999/live").status_code == 404
    assert client.get("/api/intake-station/batches/9999").status_code == 404
    assert client.post("/api/intake-station/batches/9999/end").status_code == 404


# ---- batches --------------------------------------------------------------------------------

def test_batch_detail_lists_pages_in_feed_order_and_recent_batches(env):
    first, *_ = batch_of_two(env)
    second = as_station(env, "POST", "/intake/batches").json()["batch_id"]  # ends the first
    client = env["login"]("registrar")
    detail = client.get(f"/api/intake-station/batches/{first}").json()
    assert detail["station"] == "Intake Station" and detail["open"] is False
    assert [d["sequence"] for d in detail["documents"]] == [1, 2]
    batches = client.get(f"/api/intake-station/stations/{env['intake']['id']}/batches").json()
    assert [b["batch_id"] for b in batches] == [second, first]
    assert batches[1]["counts"]["total"] == 2


def test_operator_ends_the_batch_audited_and_the_station_must_start_a_new_one(env):
    batch_id, *_ = batch_of_two(env)
    client = env["login"]("registrar")
    res = client.post(f"/api/intake-station/batches/{batch_id}/end")
    assert res.status_code == 200 and res.json()["open"] is False
    ended_at = res.json()["ended_at"]
    again = client.post(f"/api/intake-station/batches/{batch_id}/end")  # harmless repeat
    assert again.status_code == 200 and again.json()["ended_at"] == ended_at

    [entry] = env["db"].scalars(select(AuditLog).where(AuditLog.action == "intake_batch_ended"))
    operator = env["db"].scalar(select(User).where(User.email == f"registrar@{DOMAIN}"))
    assert entry.user_id == operator.id and entry.entity_id == str(batch_id)
    assert entry.new_value["ended_by"] == "operator" and entry.new_value["total"] == 2

    late = as_station(env, "POST", "/intake/documents",
                      data={"result": json.dumps(clean()), "batch_id": str(batch_id)},
                      files={"image": ("page.jpg", JPEG, "image/jpeg")})
    assert late.status_code == 409


# ---- access ---------------------------------------------------------------------------------

@pytest.mark.parametrize("handle", ["admin", "teacher", "maria.santos",
                                    "registrar-no-validation"])
def test_only_validation_operators_reach_the_console(env, handle):
    batch_id, *_ = batch_of_two(env)
    if handle == "registrar-no-validation":
        user = env["db"].scalar(select(User).where(User.email == f"registrar@{DOMAIN}"))
        user.can_validate = False
        env["db"].commit()
        handle = "registrar"
    client = env["login"](handle)
    sid = env["intake"]["id"]
    for method, path in [("GET", "/stations"), ("GET", f"/stations/{sid}/live"),
                         ("GET", f"/stations/{sid}/batches"), ("GET", f"/batches/{batch_id}"),
                         ("POST", f"/batches/{batch_id}/end")]:
        assert client.request(method, f"/api/intake-station{path}").status_code == 403, path


def test_station_key_does_not_open_the_console(env):
    res = env["client"].get("/api/intake-station/stations", headers={"X-Station-Key": env["key"]})
    assert res.status_code == 401
