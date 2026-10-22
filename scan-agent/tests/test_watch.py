"""Station service mode: status reports, stack detection, upload, and never losing a stack."""

from types import SimpleNamespace

import cv2
import httpx
import numpy as np
import pytest

from scan_agent.watch import Watcher


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeServer:
    """Stands in for BackendClient. `session_open` / `fail_after` control the scenario."""

    def __init__(self):
        self.statuses: list[tuple[str, str | None]] = []
        self.submitted: list[list[int]] = []
        self.session_open = True
        self.fail_after: int | None = None

    def status(self, state, detail=None):
        self.statuses.append((state, detail))

    def session(self):
        if not self.session_open:
            request = httpx.Request("GET", "http://server/api/station/session")
            raise httpx.HTTPStatusError("no session", request=request,
                                        response=httpx.Response(404, request=request))
        return {"session_id": 7, "mode": "bulk"}

    def lookup(self, qr_token, document_number):
        return {"found": False}

    def submit(self, session_id, document):
        if self.fail_after is not None and len(self.submitted) >= self.fail_after:
            raise httpx.ConnectError("network down")
        self.submitted.append([p.sequence for p in document.pages])
        return {"result": "VALID", "reasons": ["ok"]}


def one_document_per_page(pages, lookup):
    """Stand-in for the OCR pipeline: each page is its own document."""
    return [SimpleNamespace(pages=[page], position=i, payload={})
            for i, page in enumerate(pages, start=1)]


def scan(folder, *names):
    for name in names:
        cv2.imwrite(str(folder / name), np.full((40, 30), 255, np.uint8))


@pytest.fixture
def station(tmp_path):
    clock, server = Clock(), FakeServer()
    watcher = Watcher(server, tmp_path, clock=clock, settle=5, heartbeat=15,
                      process=one_document_per_page)
    return SimpleNamespace(watcher=watcher, server=server, clock=clock, folder=tmp_path)


def test_idle_station_reports_ready_on_a_heartbeat(station):
    assert station.watcher.tick() == "idle"
    station.clock.now += 5
    station.watcher.tick()
    assert station.server.statuses == [("ready", "Waiting for paper")]  # unchanged: no resend
    station.clock.now += 15
    station.watcher.tick()
    assert len(station.server.statuses) == 2  # heartbeat keeps the dashboard current


def test_missing_output_folder_reports_offline(station):
    station.watcher.folder = station.folder / "unplugged"
    assert station.watcher.tick() == "offline"
    assert station.server.statuses[-1][0] == "offline"


def test_stack_waits_until_the_scanner_stops_writing(station):
    scan(station.folder, "p001.png", "p002.png")
    assert station.watcher.tick() == "receiving"
    station.clock.now += 3
    scan(station.folder, "p003.png")  # still feeding
    assert station.watcher.tick() == "receiving"
    station.clock.now += 6
    assert station.watcher.tick() == "uploaded"
    assert station.server.submitted == [[1], [2], [3]]
    assert sorted(p.name for p in (station.folder / "uploaded").rglob("*.png")) == [
        "p001.png", "p002.png", "p003.png"]
    assert station.watcher.pending() == []


def test_without_an_open_session_the_files_wait(station):
    station.server.session_open = False
    scan(station.folder, "p001.png")
    station.watcher.tick()
    station.clock.now += 6
    assert station.watcher.tick() == "waiting"
    assert "start Single or Bulk" in station.server.statuses[-1][1]
    assert [p.name for p in station.watcher.pending()] == ["p001.png"]

    station.server.session_open = True  # operator clicks Bulk
    assert station.watcher.tick() == "uploaded"


def test_delete_after_upload_leaves_nothing_on_the_station(station):
    station.watcher.delete_after_upload = True
    scan(station.folder, "p001.png")
    station.watcher.tick()
    station.clock.now += 6
    station.watcher.tick()
    assert list(station.folder.rglob("*.png")) == []


def test_interrupted_upload_keeps_only_what_did_not_arrive(station):
    station.watcher.delete_after_upload = True
    scan(station.folder, "p001.png", "p002.png", "p003.png")
    station.server.fail_after = 1  # network drops after the first document
    station.watcher.tick()
    station.clock.now += 6
    assert station.watcher.tick() == "retry"
    assert [p.name for p in station.watcher.pending()] == ["p002.png", "p003.png"]

    station.server.fail_after = None
    station.watcher.tick()
    station.clock.now += 6
    assert station.watcher.tick() == "uploaded"
    assert len(station.server.submitted) == 3  # each document sent exactly once


def test_temporary_and_hidden_files_are_ignored(station):
    scan(station.folder, "p001.png")
    (station.folder / "p002.png.tmp").write_bytes(b"partial")
    (station.folder / "~p003.png").write_bytes(b"partial")
    assert [p.name for p in station.watcher.pending()] == ["p001.png"]
