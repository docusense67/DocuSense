"""SANE station service: capture into the spool folder, upload via the watcher, jams."""

import numpy as np
import pytest

from scan_agent import __main__ as cli
from scan_agent.capture.base import CapturedPage, ScannerError, ScannerState
from scan_agent.station import CAPTURE_DIR, ScannerStation
from tests.test_watch import Clock, FakeServer, one_document_per_page


class FakeScanner:
    """Stands in for SaneSource. Each `pages()` call feeds the next entry of `stacks`:
    a list of sheet values, optionally ending in a ScannerState (the stop)."""

    def __init__(self, *stacks, paper=None):
        self.stacks = list(stacks)
        self.state = ScannerState.READY
        self.detail = None
        self.paper = paper
        self.starts: list[int] = []

    def status(self):
        return self.state

    def paper_loaded(self):
        return self.paper

    def pages(self, start_sequence=1):
        self.starts.append(start_sequence)
        if self.state is ScannerState.OFFLINE:
            raise ScannerError(ScannerState.OFFLINE, start_sequence, "No scanner found")
        sheets = self.stacks.pop(0) if self.stacks else []
        sequence = start_sequence
        for sheet in sheets:
            if isinstance(sheet, ScannerState):
                raise ScannerError(sheet, sequence)
            yield CapturedPage(sequence=sequence, image=np.full((40, 30), sheet, np.uint8))
            sequence += 1


@pytest.fixture
def station_parts(tmp_path):
    server, clock = FakeServer(), Clock()

    def make(scanner, **kwargs):
        return ScannerStation(server, scanner, tmp_path, delete_after_upload=True,
                              clock=clock, process=one_document_per_page, **kwargs)

    return server, clock, make, tmp_path


def test_stack_is_captured_then_uploaded_and_deleted(station_parts):
    server, _clock, make, folder = station_parts
    station = make(FakeScanner([11, 12, 13]))

    assert station.tick() == "captured"
    files = sorted(p.name for p in folder.iterdir() if p.is_file())
    assert len(files) == 3 and files[0].endswith("-0001.png")
    assert not list((folder / CAPTURE_DIR).iterdir())

    assert station.tick() == "uploaded"  # the watcher uploads it at once (no settle wait)
    assert server.submitted == [[1], [2], [3]]
    assert not [p for p in folder.iterdir() if p.is_file()]  # deleted only after upload
    assert ("scanning", "Scanning pages") in server.statuses


def test_no_session_means_no_feeding(station_parts):
    server, _clock, make, _folder = station_parts
    server.session_open = False
    scanner = FakeScanner([1, 2], paper=True)
    station = make(scanner)

    assert station.tick() == "no-session"
    assert scanner.starts == []
    assert server.statuses[-1] == ("ready",
                                   "Paper loaded: start Single or Bulk on the dashboard")


def test_empty_feeder_sensor_skips_session_poll(station_parts):
    server, _clock, make, _folder = station_parts
    server.session = None  # would fail if called
    station = make(FakeScanner(paper=False))
    assert station.tick() == "idle"
    assert server.statuses[-1] == ("ready", "Waiting for paper")


def test_scanner_offline_is_reported(station_parts):
    server, _clock, make, _folder = station_parts
    scanner = FakeScanner()
    scanner.state, scanner.detail = ScannerState.OFFLINE, "No scanner found: check USB"
    station = make(scanner)
    assert station.tick() == "offline"
    assert server.statuses[-1] == ("offline", "No scanner found: check USB")


def test_jam_holds_pages_and_resumes_same_stack(station_parts):
    server, clock, make, folder = station_parts
    # Sheets 1-2 scan, sheet 3 jams; the operator re-feeds 3-4, which complete the stack.
    scanner = FakeScanner([1, 2, ScannerState.JAM], [], [3, 4])
    station = make(scanner)

    assert station.tick() == "jam"
    assert server.statuses[-1] == ("jam", "Scanner jam; re-feed from sheet 3")
    assert len(list((folder / CAPTURE_DIR).glob("*.png"))) == 2
    assert not [p for p in folder.iterdir() if p.is_file()]  # nothing for the watcher yet

    clock.now += 2
    assert station.tick() == "held"  # feeder still empty: keep waiting, jam stays shown
    assert server.submitted == []
    assert server.statuses[-1][0] == "jam"

    assert station.tick() == "captured"
    assert scanner.starts == [1, 3, 3]
    assert station.tick() == "uploaded"
    assert server.submitted == [[1], [2], [3], [4]]  # one stack, in feed order
    assert station.held is None


def test_jam_on_first_sheet_is_held(station_parts):
    server, _clock, make, _folder = station_parts
    scanner = FakeScanner([ScannerState.DOUBLE_FEED], [1, 2])
    station = make(scanner)
    assert station.tick() == "double_feed"
    assert server.statuses[-1][0] == "double_feed"
    assert station.tick() == "captured"
    assert scanner.starts == [1, 1]


def test_files_waiting_for_upload_block_feeding(station_parts):
    server, _clock, make, _folder = station_parts
    scanner = FakeScanner([1], [2])
    station = make(scanner)
    station.tick()
    server.session_open = False  # the operator closed the session before the upload
    assert station.tick() == "waiting"
    assert scanner.starts == [1]


def test_pages_left_by_a_crash_are_released_at_start(station_parts):
    server, _clock, make, folder = station_parts
    staging = folder / CAPTURE_DIR
    staging.mkdir()
    for seq in (1, 2):
        (staging / f"scan-20261009-101500-{seq:04d}.png").write_bytes(b"png")
    (staging / "scan-20261009-101500-0003.png.part").write_bytes(b"torn")

    make(FakeScanner())

    released = sorted(p.name for p in folder.iterdir() if p.is_file())
    assert released == ["scan-20261009-101500-0001.png", "scan-20261009-101500-0002.png"]


def test_cli_runs_the_sane_station(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(cli.ScannerStation, "run_forever",
                        lambda self, interval: runs.append((self, interval)))
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    monkeypatch.setenv("ADGVS_SANE_OPTIONS", "df-action=Stop")

    code = cli.main(["--server", "https://adgvs.example.edu.ph", "--key", "k", "--source",
                     "sane", "--device", "fujitsu:fi-7160:1", "--sane-option", "swdeskew=yes",
                     "--folder", str(tmp_path), "--watch", "--delete-after-upload"])

    station, interval = runs[0]
    assert code == 0 and interval == 2.0
    assert station.source.device_name == "fujitsu:fi-7160:1"
    assert station.source.options == {"df-action": "Stop", "swdeskew": "yes"}
    assert station.watcher.delete_after_upload and station.watcher.settle == 0.0


def test_cli_rejects_malformed_sane_option():
    with pytest.raises(SystemExit, match="NAME=VALUE"):
        cli.sane_options(["df-action"])
