"""Intake station cycle: fake feeder + gate, the file camera source, and a fake backend that
mirrors backend/app/api/intake.py (tests/intake_server.py)."""

import copy
import logging
import types

import cv2
import pytest

from scan_agent import __main__ as cli
from scan_agent import intake as intake_module
from scan_agent.capture.camera import CameraSource
from scan_agent.client import BackendClient
from scan_agent.config import CameraSettings, IntakeSettings
from scan_agent.hardware import Compartment, FakeFeeder, FakeGate, make_hardware
from scan_agent.intake import (
    COMPLETE,
    NOTHING,
    STOP_ERROR,
    STOP_JAM,
    STOP_REJECTED,
    STOP_UNREACHABLE,
    IntakeStation,
    default_screen,
)
from tests.camera_frames import camera_frame, default_page
from tests.intake_server import STATION_STATES, FakeIntakeServer

SAMPLE_NAME = "DELA CRUZ SAMPLE"  # an extracted field value: must never reach a log

CHECKS = {"expected_headings_found": 4, "expected_headings_total": 4,
          "expected_fields_found": 9, "expected_fields_total": 10, "layout_score": 0.88,
          "seal_logo_score": None, "font_consistency": 0.93, "alignment_score": 0.90}
BLOCKS = {
    "clean": {"doc_type": {"predicted": "psa_birth_certificate", "confidence": 0.93},
              "checks": CHECKS, "indicators": [],
              "similarity": {"score": 0.84, "reference_count": 12, "method": "orb+layout"},
              "fields": {"last_name": {"value": SAMPLE_NAME, "confidence": 0.93}}},
    "flagged": {"doc_type": {"predicted": "jhs_report_card", "confidence": 0.9},
                "checks": {**CHECKS, "layout_score": 0.41},
                "indicators": [{"code": "field_out_of_position", "severity": 0.7,
                                "detail": "LRN label 9 mm off", "box": [1, 2, 3, 4]}],
                "fields": {"learner_name": {"value": SAMPLE_NAME, "confidence": 0.8}}},
    # Contract: no profile to check against -> no checks / similarity / fields.
    "unknown": {"doc_type": {"predicted": "unknown", "confidence": 0.7}, "indicators": []},
}


class ScriptedScreen:
    """Stands in for screening.screen_page: one scripted document per call."""

    def __init__(self, *kinds):
        self.kinds = list(kinds)
        self.shapes: list[tuple] = []

    def __call__(self, image, *, capture, quality, timings_ms):
        self.shapes.append(image.shape)
        result = {"schema": 1, "agent_version": "test", "capture": capture, "quality": quality,
                  **copy.deepcopy(BLOCKS[self.kinds.pop(0)])}
        result["timings_ms"] = {**timings_ms, "screening": 1200, "screening_ocr": 900}
        return result


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    """Four photographed SAMPLE pages in a folder (the file camera source)."""
    folder = tmp_path_factory.mktemp("frames")
    for i in range(4):
        frame, _ = camera_frame(default_page(), angle=i - 1.5, seed=i)
        cv2.imwrite(str(folder / f"{i + 1:02d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return folder


def build(frames, *kinds, sheets=None, gate_outcomes=(), stuck=False, script=None,
          settings=None, screen=None):
    server = FakeIntakeServer()
    clock = Clock()
    client = BackendClient(station_key=server.key, http=server.client(), sleep=clock.sleep,
                           attempts=2, backoff=1.0)
    compartment = Compartment()
    feeder = FakeFeeder(len(kinds) if sheets is None else sheets, compartment=compartment,
                        script=script)
    gate = FakeGate(compartment, gate_outcomes, stuck=stuck)
    camera = CameraSource("file", CameraSettings(source="file", device=str(frames)),
                          sleep=lambda s: None)
    settings = settings or IntakeSettings(hold_timeout=60, retry_interval=10, clear_timeout=5,
                                          resume_after=5)
    station = IntakeStation(client, feeder, gate, camera, settings=settings,
                            screen=screen or ScriptedScreen(*kinds), clock=clock,
                            sleep=clock.sleep)
    return types.SimpleNamespace(server=server, clock=clock, feeder=feeder, gate=gate,
                                 compartment=compartment, station=station, camera=camera)


def intake_calls(server):
    return [call for call in server.calls if "/intake/" in call]


def no_personal_data(caplog, server):
    assert SAMPLE_NAME not in caplog.text
    assert all(SAMPLE_NAME not in (detail or "") for _, detail in server.statuses)
    assert {state for state, _ in server.statuses} <= STATION_STATES


# ---- full cycles -------------------------------------------------------------------------


def test_backend_decides_the_tray_for_every_sheet(frames, caplog):
    caplog.set_level(logging.INFO, logger="scan_agent")
    hw = build(frames, "clean", "flagged", "unknown")

    batch = hw.station.run_batch()

    assert batch.outcome == COMPLETE and batch.exit_code == 0 and batch.batch_id == 1
    assert [(s.sequence, s.decision, s.tray, s.routed_tray) for s in batch.sheets] == [
        (1, "passed_screening", 1, 1), (2, "manual_review", 2, 2), (3, "manual_review", 2, 2)]
    assert batch.sheets[1].reasons == ["severe_indicator", "layout_score_low"]
    assert batch.sheets[2].reasons == ["doc_type_unknown", "doc_type_low_confidence"]
    assert hw.gate.trays == {1: [1], 2: [2, 3]} and hw.compartment.sheet is None
    s = hw.server
    assert intake_calls(s) == ["POST /api/station/intake/batches"] + [
        call for i in (1, 2, 3) for call in ("POST /api/station/intake/documents",
                                             f"POST /api/station/intake/documents/{i}/routed")
    ] + ["POST /api/station/intake/batches/1/end"]
    assert [(d["batch_id"], d["sequence"], d["routed_tray"]) for d in s.docs.values()] == [
        (1, 1, 1), (1, 2, 2), (1, 3, 2)]
    assert batch.summary["total"] == 3 and batch.summary["routing_issues"] == 0
    assert s.statuses[-1] == ("ready", "Batch complete: 3 sheet(s), 1 in Tray 1, 2 in Tray 2")
    assert ("scanning", "Sheet 2 -> Tray 2") in s.statuses

    # What was uploaded: the normalized page as JPEG, the merged contract JSON.
    assert all(img.shape == (3508, 2480) for img in s.images)
    result = s.results[0]
    assert result["schema"] == 1 and result["capture"]["source"] == "file"
    assert result["quality"]["ok"] is True and set(result["quality"]) >= {
        "issues", "blur_score", "brightness", "glare_fraction", "skew_deg", "coverage",
        "effective_dpi"}
    assert set(result["timings_ms"]) >= {"feed", "capture", "quality", "screening"}
    assert result["fields"]["last_name"]["value"] == SAMPLE_NAME  # sent to the backend only
    # Per-step timings in the log, without field values.
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Sheet 1:"))
    assert "psa_birth_certificate, passed_screening -> Tray 1" in line
    for step in ("feed", "capture", "quality", "screening", "upload", "route", "report",
                 "clear"):
        assert f" {step} " in line
    no_personal_data(caplog, s)


def test_empty_feeder_opens_no_batch(frames):
    hw = build(frames, sheets=0)
    batch = hw.station.run_batch()
    assert batch.outcome == NOTHING and batch.batch_id is None and batch.exit_code == 0
    assert intake_calls(hw.server) == []
    assert hw.server.statuses == [("scanning", "Feeding sheet 1"), ("ready", "Waiting for paper")]


def test_feeder_jam_stops_after_the_sheets_already_routed(frames):
    hw = build(frames, "clean", "clean", sheets=3, script={2: "jam"})
    batch = hw.station.run_batch()
    assert batch.outcome == STOP_JAM and batch.exit_code == 3
    assert [s.routed_tray for s in batch.sheets] == [1]
    assert hw.feeder.calls == 2  # nothing fed after the jam
    assert hw.server.statuses[-1] == (
        "jam", "Feeder jam at sheet 2: Feeder jam (simulated). Clear it, then restart the station")
    assert hw.server.batches[1]["ended"] and batch.summary["total"] == 1


def test_gate_jam_is_reported_and_stops(frames):
    hw = build(frames, "clean", "clean", gate_outcomes=["jam"])
    batch = hw.station.run_batch()
    assert batch.outcome == STOP_JAM and len(batch.sheets) == 1
    assert batch.sheets[0].routed_tray is None
    doc = hw.server.docs[1]
    assert doc["jam"] and doc["routed_tray"] is None and doc["detail"] == "Gate jam (simulated)"
    assert doc["decision"] == "manual_review" and doc["reasons"] == ["routing_jam"]
    assert hw.compartment.sheet == 1 and hw.feeder.calls == 1
    state, detail = hw.server.statuses[-1]
    assert state == "jam" and detail.startswith("Gate jam (simulated) at sheet 1")
    assert batch.summary["routing_issues"] == 1


def test_gate_reporting_another_tray_is_a_mismatch(frames):
    hw = build(frames, "clean", gate_outcomes=[2])
    batch = hw.station.run_batch()
    assert batch.outcome == COMPLETE and batch.sheets[0].routed_tray == 2
    assert hw.server.docs[1]["reasons"] == ["routed_tray_mismatch"]
    assert hw.server.docs[1]["decision"] == "manual_review"


def test_sheet_left_in_the_compartment_stops_before_the_next_feed(frames):
    hw = build(frames, "clean", "clean", stuck=True)
    batch = hw.station.run_batch()
    assert batch.outcome == STOP_JAM and hw.feeder.calls == 1
    assert hw.server.docs[1]["routed_tray"] == 1  # it did reach the gate's tray path
    assert "still in the compartment after routing to Tray 1" in hw.server.statuses[-1][1]


# ---- backend failures: never route without a decision ------------------------------------


def test_backend_down_holds_the_sheet_and_never_routes(frames, caplog):
    caplog.set_level(logging.INFO, logger="scan_agent")
    hw = build(frames, "clean", "clean")
    hw.server.down_paths["/documents"] = 10**6

    batch = hw.station.run_batch()

    assert batch.outcome == STOP_UNREACHABLE and batch.exit_code == 4
    assert hw.gate.routes == [] and hw.compartment.sheet == 1  # held, not routed
    assert hw.feeder.calls == 1
    held = ("offline", "Server unreachable: sheet 1 held in the compartment, retrying")
    assert held in hw.server.statuses
    state, detail = hw.server.statuses[-1]
    assert state == "offline" and detail.startswith("Stopped: server unreachable for 60 s")
    assert "take it out of the compartment" in detail
    assert hw.server.docs == {} and hw.station.stopped.held
    assert 60 <= hw.clock.now - 1000 < 75  # held for hold_timeout, then stopped
    no_personal_data(caplog, hw.server)


def test_backend_back_within_the_hold_time_continues(frames):
    hw = build(frames, "clean", "flagged")
    hw.server.down_paths["/documents"] = 3  # two tries in the first round, one in the next
    batch = hw.station.run_batch()
    assert batch.outcome == COMPLETE
    assert [s.routed_tray for s in batch.sheets] == [1, 2] and len(hw.server.docs) == 2
    assert 10 in hw.clock.sleeps  # waited retry_interval while holding


def test_backend_unreachable_at_batch_start_holds_too(frames):
    hw = build(frames, "clean")
    hw.server.down_paths["/batches"] = 10**6
    batch = hw.station.run_batch()
    assert batch.outcome == STOP_UNREACHABLE and hw.gate.routes == []
    assert batch.batch_id is None and hw.compartment.sheet == 1


@pytest.mark.parametrize("code", [401, 409, 422])
def test_refused_upload_is_not_retried_and_the_sheet_is_held(frames, code):
    hw = build(frames, "clean")
    hw.server.status_for["/documents"] = code
    batch = hw.station.run_batch()
    assert batch.outcome == STOP_REJECTED and batch.exit_code == 5
    assert hw.gate.routes == [] and hw.compartment.sheet == 1
    assert hw.server.calls.count("POST /api/station/intake/documents") == 1
    assert f"HTTP {code}" in hw.server.statuses[-1][1]
    assert "give it to staff for manual review" in hw.server.statuses[-1][1]


def test_batch_ended_from_the_console_goes_on_in_a_new_batch(frames):
    """The operator ends the batch while sheet 2 is being screened: the station starts a
    new batch and uploads sheet 2 there instead of stopping with the sheet held."""
    scripted = ScriptedScreen("clean", "flagged")
    holder = {}

    def screen(image, **blocks):
        if len(holder.setdefault("seen", [])) == 1:
            holder["hw"].server.end_batch(1)
        holder["seen"].append(1)
        return scripted(image, **blocks)

    hw = holder["hw"] = build(frames, "clean", "flagged", screen=screen)
    batch = hw.station.run_batch()
    assert batch.outcome == COMPLETE
    assert [(s.tray, s.routed_tray) for s in batch.sheets] == [(1, 1), (2, 2)]
    assert hw.server.calls.count("POST /api/station/intake/batches") == 2
    assert hw.server.batches[1]["docs"] and hw.server.batches[2]["docs"]


def test_screening_failure_holds_the_sheet(frames):
    def broken(image, **blocks):
        raise RuntimeError("tesseract crashed")

    hw = build(frames, sheets=1, screen=broken)
    batch = hw.station.run_batch()
    assert batch.outcome == STOP_ERROR and hw.gate.routes == []
    assert hw.server.statuses[-1] == (
        "offline", "Stopped: screening failed at sheet 1 (RuntimeError). Take the sheet out of "
                   "the compartment")


def test_routed_conflict_409_is_logged_and_the_batch_goes_on(frames, caplog):
    hw = build(frames, "clean", "clean")
    hw.server.status_for["/routed"] = 409
    batch = hw.station.run_batch()
    assert batch.outcome == COMPLETE and [s.routed_tray for s in batch.sheets] == [1, 1]
    assert "routing was already reported differently" in caplog.text
    assert hw.station.pending_reports == []


def test_lost_routed_report_is_sent_before_the_next_upload(frames):
    hw = build(frames, "clean", "flagged")
    hw.server.down_paths["/routed"] = 2  # both tries of the first report fail
    batch = hw.station.run_batch()
    assert batch.outcome == COMPLETE
    assert [(d["routed"], d["routed_tray"]) for d in hw.server.docs.values()] == [
        (True, 1), (True, 2)]
    calls = intake_calls(hw.server)
    assert calls.index("POST /api/station/intake/documents/1/routed") < calls.index(
        "POST /api/station/intake/documents", calls.index("POST /api/station/intake/batches") + 2)
    assert batch.summary["unrouted"] == 0


# ---- service mode ------------------------------------------------------------------------


def test_service_resumes_once_the_held_sheet_was_taken_out(frames):
    hw = build(frames, "clean", "clean")
    hw.server.down_paths["/documents"] = 10**6
    assert hw.station.tick() == STOP_UNREACHABLE
    hw.server.down_paths.clear()  # the network is back

    assert hw.station.tick() == "stopped"  # sheet 1 still in the compartment
    hw.gate.take_out()  # the operator takes it out
    assert hw.station.tick() == "stopped"  # clear, but not for long enough yet
    hw.clock.now += 5
    assert hw.station.tick() == COMPLETE  # resumed: a new batch with the next sheet
    assert hw.gate.trays == {1: [2], 2: []}
    assert [b["ended"] for b in hw.server.batches.values()] == [True, True]


def test_service_stays_stopped_after_a_jam(frames):
    hw = build(frames, "clean", "clean", gate_outcomes=["jam"])
    assert hw.station.tick() == STOP_JAM
    hw.gate.take_out()
    hw.clock.now += 60
    assert hw.station.tick() == "stopped"  # a person clears it and restarts the service
    assert hw.server.statuses[-1][0] == "jam" and hw.feeder.calls == 1


def test_service_idle_and_camera_offline(frames, tmp_path):
    hw = build(frames, sheets=0)
    hw.feeder.paper_loaded = lambda: False
    assert hw.station.tick() == "idle"
    assert hw.server.statuses[-1] == ("ready", "Waiting for paper")
    hw.station.camera = CameraSource("file", CameraSettings(source="file",
                                                            device=str(tmp_path / "gone")))
    assert hw.station.tick() == "offline"
    state, detail = hw.server.statuses[-1]
    assert state == "offline" and "not found" in detail


# ---- manual mode, real screening, CLI ----------------------------------------------------


def test_manual_mode_tells_the_operator_which_tray(frames):
    answers = iter(["", "", "done"])
    said: list[str] = []
    feeder, gate = make_hardware("manual", prompt=lambda text: next(answers), say=said.append)
    hw = build(frames, "flagged")
    hw.station.feeder, hw.station.gate = feeder, gate
    batch = hw.station.run_batch()
    assert batch.outcome == COMPLETE and batch.sheets[0].routed_tray == 2
    assert said == [">>> Sheet 1: put it in TRAY 2 (NEEDS MANUAL REVIEW) <<<"]
    assert hw.server.docs[1]["routed_tray"] == 2


def test_real_screening_result_is_accepted_by_the_contract(frames):
    """One sheet through the real screen_page (OCR): the merged JSON is contract-shaped."""
    hw = build(frames, sheets=1, screen=default_screen(False))
    batch = hw.station.run_batch()
    assert batch.outcome == COMPLETE, hw.server.statuses
    result = hw.server.results[0]
    assert {"schema", "agent_version", "capture", "quality", "doc_type", "indicators",
            "timings_ms"} <= set(result)
    assert set(result["timings_ms"]) >= {"feed", "capture", "quality", "screening",
                                         "screening_ocr"}
    assert batch.sheets[0].routed_tray == batch.sheets[0].tray


def test_cli_runs_one_batch_with_fake_hardware(frames, monkeypatch, capsys):
    server = FakeIntakeServer()
    monkeypatch.setattr(intake_module, "default_screen",
                        lambda refs=None: ScriptedScreen("clean", "unknown"))
    code = intake_module.main(["--hardware", "fake", "--source", "file", "--device",
                               str(frames), "--sheets", "2", "--once", "--server",
                               "https://adgvs.example.edu.ph", "--key", server.key],
                              http=server.client())
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Batch complete: 2 sheet(s), 1 in Tray 1, 1 in Tray 2" in out
    assert "doc_type_unknown" in out and len(server.docs) == 2


def test_cli_dispatch_check_and_service_mode(monkeypatch, capsys, frames):
    for key in ("ADGVS_INTAKE_HARDWARE", "ADGVS_FEEDER_MOTOR_PIN", "ADGVS_GATE_SERVO_PIN"):
        monkeypatch.delenv(key, raising=False)
    assert cli.main(["intake", "--check", "--hardware", "fake"]) == 0
    assert "Intake settings OK (feeder fake, gate fake" in capsys.readouterr().out

    monkeypatch.setenv("ADGVS_INTAKE_HARDWARE", "gpio")
    assert cli.main(["intake", "--check"]) == 2  # no pins configured
    assert "ADGVS_GATE_SERVO_PIN is required" in capsys.readouterr().err
    monkeypatch.setenv("ADGVS_FEEDER_SHEET_PIN", "27")
    monkeypatch.setenv("ADGVS_GATE_SERVO_PIN", "18")
    assert cli.main(["intake", "--check", "--feeder", "manual"]) == 0  # gate bring-up
    out = capsys.readouterr().out
    assert "feeder manual, gate gpio" in out and "ADGVS_GATE_SERVO_PIN=18" in out

    runs = []
    monkeypatch.setattr(IntakeStation, "run_forever", lambda self: runs.append(self))
    monkeypatch.setattr(intake_module.signal, "signal", lambda *args: None)
    monkeypatch.setattr(intake_module, "default_screen", lambda refs=None: ScriptedScreen())
    code = cli.main(["intake", "--hardware", "fake", "--source", "file", "--device",
                     str(frames), "--server", "https://adgvs.example.edu.ph", "--key", "k"])
    assert code == 0 and len(runs) == 1
    station = runs[0]
    assert station.feeder.remaining == 4 and station.settings.hardware == "fake"
