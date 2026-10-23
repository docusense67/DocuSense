"""Cloud motor control (docs/cloud-motor-control.md): the backend side of the station relay.

Pure safety rules (TTL, stop priority, one controller, heartbeat freshness, caps) and the
routes: operator commands are queued and answered from what the relay reports; the station
routes return only the calling camera station's commands.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models import AuditLog, MechanismCommand, MechanismStationState, ScanSession, ScanStation
from app.models.enums import MechanismAction, Role, ScanMode
from app.security import hash_token
from app.services import mechanism_relay as relay
from app.timeutil import as_utc, utcnow

A = MechanismAction
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"


# ---- pure rules ------------------------------------------------------------------------------

def cmd(id, action, expires_at=None):
    return SimpleNamespace(id=id, action=action, expires_at=expires_at)


def test_mode_defaults_to_loopback(monkeypatch):
    for name in ("ADGVS_MECHANISM_MODE", "ADGVS_MECHANISM_STATION_ID", "ADGVS_MECHANISM_URL"):
        monkeypatch.delenv(name, raising=False)
    assert Settings(_env_file=None).mechanism_mode == "loopback"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mechanism_mode="relay")  # needs the station id
    assert Settings(_env_file=None, mechanism_mode="relay",
                    mechanism_station_id=3).mechanism_station_id == 3
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mechanism_mode="anywhere")


def test_commands_expire_but_stop_and_disconnect_never_do():
    now = utcnow()
    assert relay.command_expiry(A.TEST_MOTOR, now) == now + relay.COMMAND_TTL
    assert relay.COMMAND_TTL <= timedelta(seconds=5)
    for action in (A.STOP, A.DISCONNECT):
        assert relay.command_expiry(action, now) is None
        assert relay.expires_in(None, now) is None
    assert not relay.is_expired(now + timedelta(seconds=1), now)
    assert relay.is_expired(now, now)
    assert relay.expires_in(now + timedelta(seconds=2), now) == 2.0
    assert relay.expires_in(now - timedelta(seconds=2), now) == 0.0


def test_stop_is_delivered_first_and_supersedes_older_movement():
    now = utcnow()
    live = now + timedelta(seconds=3)
    pending = [cmd(1, A.TEST_MOTOR, live), cmd(2, A.STOP), cmd(3, A.TEST_SERVO1, live),
               cmd(4, A.CONNECT, now - timedelta(seconds=1)), cmd(5, A.DISCONNECT)]
    deliver, dropped = relay.plan_delivery(pending, now)
    assert [c.id for c in deliver] == [2, 5]
    assert sorted((c.id, reason) for c, reason in dropped) == [
        (1, "superseded"), (3, "superseded"), (4, "superseded")]
    # Movement requested after the stop still runs, after it.
    deliver, dropped = relay.plan_delivery(
        [cmd(7, A.TEST_MOTOR, live), cmd(6, A.STOP), cmd(8, A.UP, now)], now)
    assert [c.id for c in deliver] == [6, 7]
    assert [(c.id, r) for c, r in dropped] == [(8, "expired")]
    # A stop is delivered even long after it was requested.
    deliver, _ = relay.plan_delivery([cmd(9, A.STOP)], now + timedelta(days=1))
    assert [c.id for c in deliver] == [9]


def test_heartbeat_freshness_and_viewer_window():
    now = utcnow()
    assert relay.fresh_for(None, now, relay.OPERATOR_FRESH) == 0
    assert relay.fresh_for(now, now, relay.OPERATOR_FRESH) == relay.OPERATOR_FRESH.total_seconds()
    assert relay.fresh_for(now - timedelta(seconds=1), now, relay.OPERATOR_FRESH) == 2.0
    assert relay.fresh_for(now - timedelta(seconds=10), now, relay.OPERATOR_FRESH) == 0
    # A timestamp from the future (clock trouble) never grants more than the window.
    assert relay.fresh_for(now + timedelta(hours=1), now, relay.OPERATOR_FRESH) == 3.0
    row = SimpleNamespace(heartbeat_at=now - timedelta(seconds=1), viewer_seen_at=None)
    assert relay.control_payload(row, now) == {"operator_fresh_for": 2.0, "viewer_active": False}
    assert relay.control_payload(None, now) == {"operator_fresh_for": 0.0,
                                                "viewer_active": False}


def test_one_controller_per_station():
    now = utcnow()
    # The open scan session's operator controls the station; nobody else.
    assert relay.control_refusal(1, 1, 2, now, now) is None
    assert relay.control_refusal(2, 1, 2, now, now)
    # Without a session, the last controller keeps it for CONTROL_LEASE.
    assert relay.control_refusal(1, None, None, None, now) is None
    assert relay.control_refusal(2, None, 1, now, now)
    assert relay.control_refusal(1, None, 1, now, now) is None
    later = now + relay.CONTROL_LEASE + timedelta(seconds=1)
    assert relay.control_refusal(2, None, 1, now, later) is None


def test_frame_and_state_caps():
    assert relay.frame_problem(JPEG) is None
    assert relay.frame_problem(b"")
    assert relay.frame_problem(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
    assert relay.frame_problem(b"\xff\xd8\xff" + b"0" * relay.MAX_FRAME_BYTES)
    assert relay.clean_state({"state": "idle", "enabled": False, "station_id": 99,
                              "health": {}, "remote_control": True}) == {"state": "idle"}
    with pytest.raises(ValueError):
        relay.clean_state({"x": "y" * relay.MAX_STATE_BYTES})
    assert relay.clean_error("  e " * 500).startswith("e")
    assert len(relay.clean_error("e" * 1000)) == relay.MAX_ERROR_CHARS


def test_signal_wakes_waiters_without_losing_a_notify():
    signal = relay.Signal()
    seen = signal.generation(1)
    signal.notify(1)
    assert signal.wait(1, seen, timeout=0.01)  # notified after reading: returns at once
    assert not signal.wait(1, signal.generation(1), timeout=0.01)
    assert not signal.wait(2, signal.generation(2), timeout=0.01)


class FakeSession:
    """Counts commits: the waits below must commit (release the connection) before waiting."""

    def __init__(self, rows=()):
        self.rows, self.commits = list(rows), 0

    def scalars(self, _query):
        return list(self.rows)

    def commit(self):
        self.commits += 1


def test_result_wait_wakes_on_the_relays_report_without_holding_the_session():
    import threading
    import time
    row = SimpleNamespace(id=1, completed_at=None)
    db = FakeSession([row])

    def relay_reports():
        time.sleep(0.2)
        row.completed_at = utcnow()
        relay.results_signal.notify(77)

    threading.Thread(target=relay_reports).start()
    start = time.monotonic()
    assert relay.wait_for_results(db, [1], 77, timeout=5) == [row]
    assert time.monotonic() - start < 2  # woken by the signal, not the timeout
    assert db.commits >= 2
    # Nobody answers: bounded by the timeout.
    row.completed_at = None
    start = time.monotonic()
    assert relay.wait_for_results(db, [1], 77, timeout=0.3)[0].completed_at is None
    assert 0.25 <= time.monotonic() - start < 2


def test_long_poll_wakes_when_a_command_is_queued(monkeypatch):
    import threading
    import time
    queued = []
    monkeypatch.setattr(relay, "take_commands", lambda db, sid, now: list(queued))
    monkeypatch.setattr(relay, "state_row", lambda db, sid: None)

    def operator_sends():
        time.sleep(0.2)
        queued.append({"id": 1, "action": "stop", "params": {}, "expires_in": None})
        relay.commands_signal.notify(78)

    threading.Thread(target=operator_sends).start()
    start = time.monotonic()
    result = relay.poll_commands(FakeSession(), 78, 20)
    assert result["commands"][0]["action"] == "stop"
    assert time.monotonic() - start < 2


# ---- routes ----------------------------------------------------------------------------------

@pytest.fixture
def setup(db, client, make_user, login, monkeypatch):
    station = ScanStation(name="Camera station", api_key_hash=hash_token("camera-key"))
    other = ScanStation(name="Other station", api_key_hash=hash_token("other-key"))
    db.add_all([station, other])
    db.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "mechanism_mode", "relay")
    monkeypatch.setattr(settings, "mechanism_station_id", station.id)
    monkeypatch.setattr(settings, "mechanism_url", "")
    monkeypatch.setattr(relay, "RESULT_WAIT_SECONDS", 0)
    relay._frames.clear()
    ops = {name: make_user(Role.REGISTRAR, f"{name}@school.edu.ph", can_validate=True)
           for name in ("op1", "op2")}
    make_user(Role.REGISTRAR, "plain@school.edu.ph")
    make_user(Role.STUDENT, "student@school.edu.ph")

    def as_user(name):
        client.cookies.clear()
        assert login(f"{name}@school.edu.ph").status_code == 200

    def station_call(method, path, key="camera-key", **kw):
        return client.request(method, "/api/station/mechanism" + path,
                              headers={"X-Station-Key": key, **kw.pop("headers", {})}, **kw)

    def report(remote=True, **state):
        res = station_call("POST", "/state", json={
            "state": {"connected": True, "state": "idle", "busy": False, **state},
            "remote_allowed": remote})
        assert res.status_code == 200, res.text
        return res.json()

    def fake_station(ok=True, error=None):
        """wait_for_results stand-in: the relay picks the commands up and reports at once."""
        def wait(db_, ids, station_id, timeout=None):
            relay.take_commands(db_, station_id, utcnow())
            for command in db_.scalars(select(MechanismCommand).where(
                    MechanismCommand.id.in_(ids))):
                if command.completed_at is None:
                    command.completed_at = utcnow()
                    command.result = {"ok": ok, "error": error}
            db_.commit()
            return list(db_.scalars(select(MechanismCommand).where(MechanismCommand.id.in_(ids))))
        monkeypatch.setattr(relay, "wait_for_results", wait)

    return SimpleNamespace(db=db, client=client, station=station, other=other, ops=ops,
                           as_user=as_user, station_call=station_call, report=report,
                           fake_station=fake_station)


def commands(db):
    return list(db.scalars(select(MechanismCommand).order_by(MechanismCommand.id)))


def test_operator_routes_need_the_validation_permission(setup):
    s = setup
    s.report()
    for name in ("student", "plain"):
        s.as_user(name)
        assert s.client.get("/api/mechanism/status").status_code == 403
        assert s.client.get("/api/mechanism/camera/frame").status_code == 403
        assert s.client.post("/api/mechanism/command",
                             json={"action": "stop"}).status_code == 403
    assert commands(s.db) == []
    # A user session is not a station key.
    s.as_user("op1")
    assert s.client.get("/api/station/mechanism/commands").status_code == 401


def test_status_comes_from_the_relay_report_and_goes_stale(setup):
    s = setup
    s.as_user("op1")
    assert s.client.get("/api/mechanism/status").status_code == 503  # never reported
    s.report(station_id=999, enabled=False, health={"x": 1})
    body = s.client.get("/api/mechanism/status").json()
    assert body["enabled"] is True and body["station_id"] == s.station.id
    assert body["relay"] is True and body["remote_control"] is True and "health" not in body
    row = s.db.get(MechanismStationState, s.station.id)
    row.updated_at = utcnow() - relay.STATE_STALE - timedelta(seconds=1)
    s.db.commit()
    assert s.client.get("/api/mechanism/status").status_code == 503


def test_command_round_trip_is_audited_and_returns_the_new_state(setup):
    s = setup
    s.report()
    s.as_user("op1")
    s.fake_station()
    res = s.client.post("/api/mechanism/command", json={"action": "test-motor"})
    assert res.status_code == 200, res.text
    assert res.json()["enabled"] is True
    [command] = commands(s.db)
    assert command.action == A.TEST_MOTOR and command.params == {}
    assert command.requested_by == s.ops["op1"].id and command.result == {"ok": True,
                                                                          "error": None}
    audit = s.db.scalar(select(AuditLog).where(AuditLog.action == "mechanism.requested"))
    assert audit.user_id == s.ops["op1"].id and audit.new_value == {"action": "test-motor"}
    # Tray: connect first, then the sorting servo with the requested angle.
    assert s.client.post("/api/mechanism/command",
                         json={"action": "tray", "angle": 120}).status_code == 200
    assert [(c.action, c.params) for c in commands(s.db)[1:]] == [
        (A.CONNECT, {}), (A.UP, {"angle": 120.0})]


def test_station_refusal_is_shown_to_the_operator(setup):
    s = setup
    s.report()
    s.as_user("op1")
    s.fake_station(ok=False, error="Connect the hardware first.")
    res = s.client.post("/api/mechanism/command", json={"action": "test-servo1"})
    assert res.status_code == 409 and "Connect the hardware" in res.json()["detail"]


def test_scan_keeps_session_checks_and_fixed_angles(setup):
    s = setup
    s.report()
    s.as_user("op1")
    s.fake_station()
    session = ScanSession(mode=ScanMode.SINGLE, operator_id=s.ops["op1"].id,
                          station_id=s.station.id)
    s.db.add(session)
    s.db.commit()
    res = s.client.post("/api/mechanism/command", json={
        "action": "scan", "session_id": session.id, "valid_angle": 5, "calibrated": False})
    assert res.status_code == 200, res.text
    [command] = commands(s.db)
    assert command.params == {"session_id": session.id, "valid_angle": 180,
                              "invalid_angle": 90, "calibrated": True}
    # Another operator may not use it, nor move the station while that session is open.
    s.as_user("op2")
    assert s.client.post("/api/mechanism/command", json={
        "action": "scan", "session_id": session.id}).status_code == 409
    res = s.client.post("/api/mechanism/command", json={"action": "test-motor"})
    assert res.status_code == 409 and "Another operator" in res.json()["detail"]
    assert s.client.post("/api/mechanism/command",
                         json={"action": "heartbeat"}).status_code == 409
    # ... but anyone may stop it.
    assert s.client.post("/api/mechanism/command", json={"action": "stop"}).status_code == 200
    assert [c.action for c in commands(s.db)] == [A.SCAN, A.STOP]


def test_one_controller_without_a_session(setup):
    s = setup
    s.report()
    s.fake_station()
    s.as_user("op1")
    assert s.client.post("/api/mechanism/command", json={"action": "connect"}).status_code == 200
    s.as_user("op2")
    assert s.client.post("/api/mechanism/command",
                         json={"action": "test-motor"}).status_code == 409
    row = s.db.get(MechanismStationState, s.station.id)
    row.controller_seen_at = utcnow() - relay.CONTROL_LEASE - timedelta(seconds=1)
    s.db.commit()
    assert s.client.post("/api/mechanism/command",
                         json={"action": "test-motor"}).status_code == 200
    assert s.db.get(MechanismStationState, s.station.id).controller_id == s.ops["op2"].id


def test_heartbeat_freshness_is_delivered_with_commands(setup):
    s = setup
    s.report()
    poll = s.station_call("GET", "/commands").json()
    assert poll == {"commands": [], "operator_fresh_for": 0.0, "viewer_active": False}
    s.as_user("op1")
    res = s.client.post("/api/mechanism/command", json={"action": "heartbeat"})
    assert res.status_code == 200 and res.json()["enabled"] is True
    assert commands(s.db) == []  # heartbeats are not queued and not audited
    assert s.db.scalar(select(AuditLog).where(AuditLog.action == "mechanism.requested")) is None
    fresh = s.station_call("GET", "/commands").json()["operator_fresh_for"]
    assert 0 < fresh <= relay.OPERATOR_FRESH.total_seconds()
    assert 0 < s.report()["operator_fresh_for"] <= 3
    row = s.db.get(MechanismStationState, s.station.id)
    row.heartbeat_at = utcnow() - timedelta(seconds=10)
    s.db.commit()
    assert s.station_call("GET", "/commands").json()["operator_fresh_for"] == 0


def test_remote_control_needs_the_stations_opt_in(setup):
    s = setup
    s.report(remote=False)
    s.as_user("op1")
    res = s.client.post("/api/mechanism/command", json={"action": "test-motor"})
    assert res.status_code == 409 and "Remote control is switched off" in res.json()["detail"]
    assert s.client.post("/api/mechanism/command", json={"action": "heartbeat"}).status_code == 409
    assert s.db.get(MechanismStationState, s.station.id).heartbeat_at is None
    # stop is still queued; with nobody answering it is reported unconfirmed.
    assert s.client.post("/api/mechanism/command", json={"action": "stop"}).status_code == 503
    assert [c.action for c in commands(s.db)] == [A.STOP]


def test_offline_relay_refuses_movement_but_queues_stop(setup):
    s = setup
    s.as_user("op1")
    res = s.client.post("/api/mechanism/command", json={"action": "connect"})
    assert res.status_code == 503 and "not connected" in res.json()["detail"]
    assert s.client.post("/api/mechanism/command", json={"action": "stop"}).status_code == 503
    assert [c.action for c in commands(s.db)] == [A.STOP]
    s.report()
    [stop] = s.station_call("GET", "/commands").json()["commands"]
    assert stop["action"] == "stop" and stop["expires_in"] is None


def test_station_polls_only_live_commands_stop_first_and_posts_results(setup):
    s = setup
    s.report()
    s.as_user("op1")
    # Not confirmed in time (no relay answering): 503, the command still waits for its TTL.
    assert s.client.post("/api/mechanism/command",
                         json={"action": "test-motor"}).status_code == 503
    [pending] = commands(s.db)
    assert as_utc(pending.expires_at) - as_utc(pending.created_at) == relay.COMMAND_TTL
    polled = s.station_call("GET", "/commands").json()["commands"]
    assert polled == [{"id": pending.id, "action": "test-motor", "params": {},
                       "expires_in": polled[0]["expires_in"]}]
    assert 0 < polled[0]["expires_in"] <= 5
    assert s.station_call("GET", "/commands").json()["commands"] == []  # delivered once
    # Results: only for this station's delivered commands, once.
    assert s.station_call("POST", f"/commands/{pending.id}/result", key="other-key",
                          json={"ok": True}).status_code == 403
    res = s.station_call("POST", f"/commands/{pending.id}/result", json={
        "ok": False, "error": "Stop the active movement first.", "status": {"state": "cycle"}})
    assert res.status_code == 200 and set(res.json()) == {"operator_fresh_for", "viewer_active"}
    assert s.db.get(MechanismCommand, pending.id).result == {
        "ok": False, "error": "Stop the active movement first."}
    assert s.db.get(MechanismStationState, s.station.id).state["state"] == "cycle"
    assert s.station_call("POST", f"/commands/{pending.id}/result",
                          json={"ok": True}).status_code == 409
    assert s.station_call("POST", "/commands/999999/result", json={"ok": True}).status_code == 404

    # Expired and superseded commands are never delivered.
    now = utcnow()
    old = relay.enqueue(s.db, s.station.id, [(A.TEST_SERVO2, {})], s.ops["op1"].id,
                        now - timedelta(seconds=6))
    s.db.commit()
    assert s.station_call("GET", "/commands").json()["commands"] == []
    assert s.db.get(MechanismCommand, old[0].id).result == {"ok": False, "error": "expired"}
    later = relay.enqueue(s.db, s.station.id, [(A.TEST_MOTOR, {}), (A.STOP, {}),
                                              (A.CONNECT, {})], s.ops["op1"].id, now)
    s.db.commit()
    polled = s.station_call("GET", "/commands").json()["commands"]
    assert [c["action"] for c in polled] == ["stop", "connect"]
    assert s.db.get(MechanismCommand, later[0].id).result == {"ok": False,
                                                              "error": "superseded"}
    # A dropped command cannot be reported as run.
    assert s.station_call("POST", f"/commands/{old[0].id}/result",
                          json={"ok": True}).status_code == 409


def test_other_stations_get_nothing(setup):
    s = setup
    s.report()
    relay.enqueue(s.db, s.station.id, [(A.STOP, {})], s.ops["op1"].id, utcnow())
    s.db.commit()
    for method, path, kw in (("GET", "/commands", {}),
                             ("POST", "/state", {"json": {"state": {}}}),
                             ("POST", "/frame", {"content": JPEG,
                                                 "headers": {"Content-Type": "image/jpeg"}})):
        assert s.station_call(method, path, key="other-key", **kw).status_code == 403
        assert s.station_call(method, path, key="wrong", **kw).status_code == 401
    assert commands(s.db)[0].delivered_at is None
    s.other.is_active = s.station.is_active = False
    s.db.commit()
    assert s.station_call("GET", "/commands").status_code == 401  # deactivated key


def test_station_routes_are_off_in_loopback_mode(setup, monkeypatch):
    monkeypatch.setattr(get_settings(), "mechanism_mode", "loopback")
    assert setup.station_call("GET", "/commands").status_code == 409
    assert setup.station_call("POST", "/state", json={"state": {}}).status_code == 409


def test_long_poll_waits_and_returns_empty(setup):
    import time
    s = setup
    start = time.monotonic()
    res = s.station_call("GET", "/commands", params={"wait": 0.3})
    assert res.status_code == 200 and res.json()["commands"] == []
    assert time.monotonic() - start >= 0.25
    assert s.station_call("GET", "/commands", params={"wait": 60}).status_code == 422


def test_state_and_frame_caps(setup):
    s = setup
    res = s.station_call("POST", "/state", json={"state": {"x": "y" * relay.MAX_STATE_BYTES}})
    assert res.status_code == 422
    big = b"\xff\xd8\xff" + b"0" * relay.MAX_FRAME_BYTES
    jpeg = {"Content-Type": "image/jpeg"}
    assert s.station_call("POST", "/frame", content=big, headers=jpeg).status_code == 413
    assert s.station_call("POST", "/frame", content=b"\x89PNG....",
                          headers=jpeg).status_code == 422
    s.report()
    s.as_user("op1")
    assert s.client.get("/api/mechanism/camera/frame").status_code == 503  # no frame yet
    assert s.station_call("GET", "/commands").json()["viewer_active"] is True
    assert s.station_call("POST", "/frame", content=JPEG, headers=jpeg).status_code == 200
    res = s.client.get("/api/mechanism/camera/frame")
    assert res.status_code == 200 and res.content == JPEG
    assert res.headers["content-type"] == "image/jpeg" and res.headers["X-Frame-Time"]
    assert res.headers["Cache-Control"] == "no-store"


def test_old_commands_are_pruned(setup, monkeypatch):
    s = setup
    relay.enqueue(s.db, s.station.id, [(A.STOP, {})], None, utcnow() - timedelta(days=2))
    relay.enqueue(s.db, s.station.id, [(A.STOP, {})], None, utcnow())
    s.db.commit()
    monkeypatch.setattr(relay, "_last_prune", [float("-inf")])
    relay.maybe_prune(s.db, utcnow())
    assert len(commands(s.db)) == 1
