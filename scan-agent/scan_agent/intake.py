"""Intake station: feeder -> camera -> screening -> backend decision -> gate -> tray.

    python -m scan_agent intake --hardware gpio --source picamera2           # service
    python -m scan_agent intake --hardware manual --source picamera2 --once  # bring-up
    python -m scan_agent intake --feeder manual --gate gpio --once           # gate bring-up
    python -m scan_agent intake --check                                      # settings only

One cycle per sheet (docs/intake/screening-contract.md):

    FEED      feeder.feed_one(): fed / empty (batch complete) / jam (stop)
    BATCH     first sheet only: POST /api/station/intake/batches
    CAPTURE   CameraSource.capture(): wait for a still page, focus, still, find + flatten
              the page, quality check, recapture policy (all inside capture/camera.py)
    SCREEN    screening.screen_page(): doc_type / checks / indicators / similarity / fields,
              merged with the capture + quality blocks and timings_ms (schema 1)
    UPLOAD    POST /api/station/intake/documents (JPEG + result + batch_id + sequence)
              -> the backend's decision and tray. Backend unreachable: HOLD (below)
    ROUTE     gate.route(tray): ok (the tray it reached) / jam (stop)
    REPORT    POST .../documents/{id}/routed {tray} or {jam, detail}
    CLEAR     wait until the compartment is empty, then the next FEED
    END       feeder empty: POST .../batches/{id}/end (summary in the log)

The station measures; the backend decides (CLAUDE.md). The gate moves only on the
backend's answer: with no decision (server unreachable, result refused, camera or
screening failure) the sheet stays in the compartment and is never routed. Unreachable is
retried every `retry_interval` for `hold_timeout` seconds (the dashboard shows Offline
with "sheet N held"); then the station stops and asks the operator to take the sheet out.
A jam (feeder or gate) is reported and stops the station until it is cleared and the
service restarted. Logs carry sheet numbers, decisions, reason codes and timings, never
extracted field values.

Dashboard states (backend STATION_STATES): ready (idle), scanning, jam, offline. The
backend has no "error" state: a stop for any other reason is shown as offline with a
"Stopped: ..." detail.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import httpx

from scan_agent.capture.base import ScannerState
from scan_agent.capture.camera import SOURCES, CameraError, CameraSource
from scan_agent.client import (
    BackendClient,
    BackendUnreachable,
    BadResponse,
    IntakeDecision,
    IntakeError,
    IntakeRejected,
    result_json,
)
from scan_agent.config import (
    CameraSettings,
    ConfigError,
    FeederSettings,
    GateSettings,
    IntakeSettings,
    validate_intake,
)
from scan_agent.hardware import (
    FEED_EMPTY,
    FEED_JAM,
    ROUTE_OK,
    TRAY_NAMES,
    Feeder,
    Gate,
    HardwareError,
    make_hardware,
)

log = logging.getLogger("scan_agent.intake")

READY, SCANNING, JAM, OFFLINE = "ready", "scanning", "jam", "offline"

# BatchResult.outcome
COMPLETE = "complete"  # the feeder ran empty after at least one sheet
NOTHING = "nothing"  # the feeder was empty from the start: no batch was opened
STOP_JAM = "jam"
STOP_UNREACHABLE = "unreachable"
STOP_REJECTED = "rejected"  # the backend refused the page (4xx) or answered off-contract
STOP_ERROR = "error"  # camera / screening / encoding failed
EXIT_CODES = {COMPLETE: 0, NOTHING: 0, STOP_JAM: 3, STOP_UNREACHABLE: 4, STOP_REJECTED: 5,
              STOP_ERROR: 5}


class Stop(Exception):
    """Stops the batch. `state`/`detail` go to the dashboard; `held`: a sheet that was not
    routed is still in the compartment."""

    def __init__(self, outcome: str, state: str, detail: str, *, held: bool = False):
        super().__init__(detail)
        self.outcome = outcome
        self.state = state
        self.detail = detail
        self.held = held


@dataclass
class SheetResult:
    sequence: int
    intake_id: int
    decision: str
    tray: int  # assigned by the backend
    routed_tray: int | None  # where the gate put it (None: jam)
    reasons: list[str]
    timings_ms: dict = field(default_factory=dict)


@dataclass
class BatchResult:
    outcome: str
    batch_id: int | None = None
    sheets: list[SheetResult] = field(default_factory=list)
    summary: dict | None = None
    detail: str = ""

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.outcome]


class StatusReporter:
    """Dashboard status: sent when it changes and at least every `heartbeat` seconds.
    An unreachable server is logged, never raised (the cycle handles that itself)."""

    def __init__(self, client, heartbeat: float, clock: Callable[[], float]):
        self.client = client
        self.heartbeat = heartbeat
        self.clock = clock
        self.last: tuple[float, str, str | None] | None = None

    def __call__(self, state: str, detail: str | None = None, force: bool = False) -> None:
        now = self.clock()
        if (not force and self.last is not None and self.last[1:] == (state, detail)
                and now - self.last[0] < self.heartbeat):
            return
        try:
            self.client.status(state, detail[:500] if detail else detail)  # backend max 500
            self.last = (now, state, detail)
        except httpx.HTTPError as e:
            log.warning("Could not report %s to the server: %s", state, e)


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def encode_jpeg(image, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("Could not encode the page as JPEG")
    return buf.tobytes()


def default_screen(store=None) -> Callable[..., dict]:
    """screening.screen_page with the reference store resolved once (imported lazily: it
    loads the OCR stack)."""
    from scan_agent.screening import resolve_store, screen_page

    resolved = resolve_store(store)

    def screen(image, **blocks) -> dict:
        return screen_page(image, store=resolved, **blocks)

    return screen


class IntakeStation:
    def __init__(self, client, feeder: Feeder, gate: Gate, camera: CameraSource, *,
                 settings: IntakeSettings | None = None,
                 screen: Callable[..., dict] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.feeder = feeder
        self.gate = gate
        self.camera = camera
        self.settings = settings or IntakeSettings()
        self._screen = screen
        self.clock = clock
        self.sleep = sleep
        self.report = StatusReporter(client, self.settings.heartbeat, clock)
        self.pending_reports: list[tuple[int, dict]] = []  # routed reports not yet sent
        self.stopped: Stop | None = None  # service: latched stop
        self._clear_since: float | None = None

    @property
    def screen(self) -> Callable[..., dict]:
        if self._screen is None:
            self._screen = default_screen()
        return self._screen

    # ---- backend calls ---------------------------------------------------------------

    def _hold(self, call: Callable[[], object], what: str, sequence: int):
        """Run a backend call while sheet `sequence` waits in the compartment: retried
        while the server is unreachable, for at most hold_timeout seconds."""
        s = self.settings
        deadline = self.clock() + s.hold_timeout
        while True:
            try:
                return call()
            except BackendUnreachable as e:
                if self.clock() >= deadline:
                    raise Stop(STOP_UNREACHABLE, OFFLINE,
                               f"Stopped: server unreachable for {s.hold_timeout:g} s. Sheet "
                               f"{sequence} was not screened: take it out of the compartment "
                               "and feed it again later", held=True) from e
                log.warning("%s: server unreachable, sheet %d held in the compartment: %s",
                            what, sequence, e)
                self.report(OFFLINE, f"Server unreachable: sheet {sequence} held in the "
                                     "compartment, retrying")
                self.sleep(s.retry_interval)
            except IntakeRejected as e:
                raise Stop(STOP_REJECTED, OFFLINE,
                           f"Stopped: the server refused sheet {sequence} ({what}: {e}). Take "
                           "it out of the compartment and give it to staff for manual review",
                           held=True) from e
            except BadResponse as e:
                raise Stop(STOP_REJECTED, OFFLINE,
                           f"Stopped: unexpected server answer for sheet {sequence} ({e}). "
                           "Take it out of the compartment and give it to staff for manual "
                           "review", held=True) from e

    def _send_routed(self, intake_id: int, body: dict) -> bool:
        """True once the backend has the report (or refused it for good)."""
        try:
            res = self.client.report_routed(intake_id, **body)
        except BackendUnreachable as e:
            log.warning("Routing of intake %d not reported yet (kept for later): %s",
                        intake_id, e)
            return False
        except IntakeError as e:
            log.error("Routing report for intake %d refused: %s", intake_id, e)
            return True
        if res.get("conflict"):
            log.warning("Intake %d: routing was already reported differently; the server "
                        "keeps the first report (%s)", intake_id, res.get("detail"))
        elif res.get("decision") == "manual_review" and res.get("reasons"):
            added = [r for r in res["reasons"] if r in ("routing_jam", "routed_tray_mismatch")]
            if added:
                log.info("Intake %d now needs manual review: %s", intake_id, ", ".join(added))
        return True

    def report_routed(self, intake_id: int, **body) -> None:
        if not self._send_routed(intake_id, body):
            self.pending_reports.append((intake_id, body))

    def flush_reports(self) -> None:
        self.pending_reports = [(i, b) for i, b in self.pending_reports
                                if not self._send_routed(i, b)]

    # ---- one sheet -------------------------------------------------------------------

    def process_sheet(self, sequence: int, batch: BatchResult) -> SheetResult | None:
        """One cycle. None when the feeder is empty. Raises Stop."""
        s = self.settings
        timings: dict[str, int] = {}
        t0 = t = self.clock()
        self.report(SCANNING, f"Feeding sheet {sequence}")
        fed = self.feeder.feed_one()
        timings["feed"] = _ms(self.clock() - t)
        if fed.result == FEED_EMPTY:
            return None
        if fed.result == FEED_JAM:
            raise Stop(STOP_JAM, JAM, f"Feeder jam at sheet {sequence}: {fed.detail}. Clear "
                                      "it, then restart the station")

        if batch.batch_id is None:
            batch.batch_id = self._hold(self.client.start_batch, "start batch", sequence)
            log.info("Batch %d started", batch.batch_id)
        self.flush_reports()

        self.report(SCANNING, f"Screening sheet {sequence}")
        try:
            page = self.camera.capture(sequence, shoot_on_timeout=True)
        except CameraError as e:
            raise Stop(STOP_ERROR, OFFLINE, f"Stopped: camera failed at sheet {sequence} ({e}). "
                                            "Take the sheet out of the compartment",
                       held=True) from e
        if page is None:
            raise Stop(STOP_ERROR, OFFLINE, f"Stopped: the camera returned no image for sheet "
                                            f"{sequence}. Take the sheet out of the compartment",
                       held=True)
        timings.update(page.timings_ms)

        t = self.clock()
        try:
            result = self.screen(page.image, capture=page.capture, quality=page.quality,
                                 timings_ms={**page.timings_ms, "feed": timings["feed"]})
            text = result_json(result)
            jpeg = encode_jpeg(page.image, s.jpeg_quality)
        except Exception as e:
            log.exception("Screening failed for sheet %d", sequence)
            raise Stop(STOP_ERROR, OFFLINE, f"Stopped: screening failed at sheet {sequence} "
                                            f"({type(e).__name__}). Take the sheet out of the "
                                            "compartment", held=True) from e
        timings["screening"] = _ms(self.clock() - t)

        t = self.clock()

        def upload() -> IntakeDecision:
            try:
                return self.client.upload_intake(jpeg, text, batch_id=batch.batch_id,
                                                 sequence=sequence)
            except IntakeRejected as e:
                # The operator ended the batch from the console while this sheet was being
                # screened: carry on in a new batch (once), the sheet is still held.
                if e.status_code != 409 or "batch has ended" not in e.detail.lower():
                    raise
                log.info("Batch %d was ended from the console; starting a new batch for "
                         "sheet %d", batch.batch_id, sequence)
                batch.batch_id = self.client.start_batch()
                return self.client.upload_intake(jpeg, text, batch_id=batch.batch_id,
                                                 sequence=sequence)

        decision: IntakeDecision = self._hold(upload, "upload", sequence)
        timings["upload"] = _ms(self.clock() - t)

        t = self.clock()
        self.report(SCANNING, f"Sheet {sequence} -> Tray {decision.tray}")
        routed = self.gate.route(decision.tray)
        timings["route"] = _ms(self.clock() - t)
        sheet = SheetResult(sequence, decision.intake_id, decision.decision, decision.tray,
                            routed.tray if routed.result == ROUTE_OK else None,
                            decision.reasons, timings)
        batch.sheets.append(sheet)

        t = self.clock()
        if routed.result != ROUTE_OK:
            self.report_routed(decision.intake_id, jam=True, detail=routed.detail)
            self._log_sheet(sheet, doc_type=result.get("doc_type"), total=self.clock() - t0)
            raise Stop(STOP_JAM, JAM, f"{routed.detail or 'Gate jam'} at sheet {sequence}. "
                                      "Clear it (the sheet goes to Tray 2 for manual review), "
                                      "then restart the station")
        if routed.tray != decision.tray:
            log.warning("Sheet %d: assigned Tray %d but the gate reports Tray %s", sequence,
                        decision.tray, routed.tray)
        self.report_routed(decision.intake_id, tray=routed.tray)
        timings["report"] = _ms(self.clock() - t)

        t = self.clock()
        cleared = self._wait(self.gate.sheet_cleared, s.clear_timeout)
        timings["clear"] = _ms(self.clock() - t)
        self._log_sheet(sheet, doc_type=result.get("doc_type"), total=self.clock() - t0)
        if not cleared:
            raise Stop(STOP_JAM, JAM, f"Sheet {sequence} is still in the compartment after "
                                      f"routing to Tray {routed.tray}. Clear it, then restart "
                                      "the station")
        return sheet

    def _wait(self, condition: Callable[[], bool], timeout: float) -> bool:
        deadline = self.clock() + timeout
        while True:
            if condition():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(0.05)

    @staticmethod
    def _log_sheet(sheet: SheetResult, doc_type, total: float) -> None:
        # Decision, reason codes, type and timings only: never extracted field values.
        predicted = doc_type.get("predicted") if isinstance(doc_type, dict) else None
        where = (f"Tray {sheet.routed_tray}" if sheet.routed_tray is not None else "JAM")
        steps = " ".join(f"{k} {v}" for k, v in sheet.timings_ms.items())
        log.info("Sheet %d: %s, %s -> %s (assigned Tray %d)%s | total %d ms: %s",
                 sheet.sequence, predicted or "?", sheet.decision, where, sheet.tray,
                 f" [{', '.join(sheet.reasons)}]" if sheet.reasons else "", _ms(total), steps)

    # ---- one batch -------------------------------------------------------------------

    def _end_batch(self, batch: BatchResult) -> None:
        if batch.batch_id is None:
            return
        self.flush_reports()
        try:
            batch.summary = self.client.end_batch(batch.batch_id)
        except IntakeError as e:  # the server ends it when the next batch starts
            log.warning("Batch %d not ended on the server: %s", batch.batch_id, e)
            return
        sm = batch.summary
        log.info("Batch %d ended: %s document(s), %s passed screening, %s manual review, "
                 "%s routing issue(s), %s unrouted", batch.batch_id, sm.get("total"),
                 sm.get("passed_screening"), sm.get("manual_review"),
                 sm.get("routing_issues"), sm.get("unrouted"))

    def run_batch(self) -> BatchResult:
        """Feed and screen sheets until the feeder is empty, or until a stop."""
        batch = BatchResult(NOTHING)
        try:
            while True:
                if self.process_sheet(len(batch.sheets) + 1, batch) is None:
                    break
        except Stop as stop:
            batch.outcome, batch.detail = stop.outcome, stop.detail
            log.error("%s", stop.detail)
            self.report(stop.state, stop.detail, force=True)
            self._end_batch(batch)
            self.stopped = stop
            return batch
        if batch.batch_id is None:
            self.report(READY, "Waiting for paper")
            return batch
        batch.outcome = COMPLETE
        self._end_batch(batch)
        trays = {1: 0, 2: 0}
        for sheet in batch.sheets:
            if sheet.routed_tray in trays:
                trays[sheet.routed_tray] += 1
        batch.detail = (f"Batch complete: {len(batch.sheets)} sheet(s), {trays[1]} in Tray 1, "
                        f"{trays[2]} in Tray 2")
        self.report(READY, batch.detail, force=True)
        return batch

    # ---- service ---------------------------------------------------------------------

    def compartment_clear(self) -> bool:
        # The gate first: in manual mode it asks the operator to take the sheet out.
        return self.gate.sheet_cleared() and not self.feeder.sheet_present()

    def tick(self) -> str:
        """One pass of the service loop. Returns what happened (for logs and tests)."""
        if self.stopped is not None:
            stop = self.stopped
            self.report(stop.state, stop.detail)
            if stop.state == JAM:
                return "stopped"  # cleared by a person, then a restart of the service
            # A held sheet: resume once it has been taken out (compartment clear for a while).
            if not self.compartment_clear():
                self._clear_since = None
                return "stopped"
            now = self.clock()
            self._clear_since = self._clear_since if self._clear_since is not None else now
            if now - self._clear_since < self.settings.resume_after:
                return "stopped"
            log.info("Compartment clear: resuming")
            self.stopped = self._clear_since = None
        if self.camera.status() is ScannerState.OFFLINE:
            self.report(OFFLINE, self.camera.detail or "Camera not available")
            return "offline"
        if self.feeder.paper_loaded() is False:
            self.report(READY, "Waiting for paper")
            return "idle"
        return self.run_batch().outcome

    def run_forever(self) -> None:
        log.info("Intake station: %s, %s, camera %s (Ctrl+C to stop)", self.feeder.describe(),
                 self.gate.describe(), self.camera.backend.describe())
        try:
            while True:
                try:
                    self.tick()
                except Exception:  # keep the service alive; the next pass retries
                    log.exception("Unexpected error; retrying")
                self.sleep(self.settings.idle_poll)
        except KeyboardInterrupt:
            self.report(OFFLINE, "Station software stopped", force=True)

    def close(self) -> None:
        for part in (self.feeder, self.gate, self.camera):
            try:
                part.close()
            except Exception:  # pragma: no cover - best effort on shutdown
                log.exception("Closing %s failed", type(part).__name__)


# ---- CLI ---------------------------------------------------------------------------------


def _stop(signum, frame):
    raise KeyboardInterrupt  # systemd stops with SIGTERM: report Offline before exiting


def _settings_lines(intake: IntakeSettings, feeder: FeederSettings, gate: GateSettings,
                    gpio: bool) -> list[str]:
    lines = [f"ADGVS_INTAKE_{k.upper()}={v}" for k, v in dataclasses.asdict(intake).items()]
    if gpio:
        lines += [f"ADGVS_FEEDER_{k.upper()}={v}" for k, v in dataclasses.asdict(feeder).items()]
        lines += [f"ADGVS_GATE_{k.upper()}={v}" for k, v in dataclasses.asdict(gate).items()]
    return lines


def _image_count(folder: str) -> int:
    from scan_agent.capture.camera import IMAGE_SUFFIXES

    path = Path(folder or ".")
    return sum(1 for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES) \
        if path.is_dir() else 0


def main(argv: list[str] | None = None, *, http: httpx.Client | None = None) -> int:
    try:
        intake = IntakeSettings.from_env()
        feeder_settings = FeederSettings.from_env()
        gate_settings = GateSettings.from_env()
        camera_defaults = CameraSettings.from_env()
    except ValueError as e:
        print(f"Invalid station settings: {e}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="python -m scan_agent intake",
                                     description="Intake station: feed, capture, screen, "
                                                 "upload, route to Tray 1 / Tray 2.")
    parser.add_argument("--server", default=os.environ.get("ADGVS_SERVER"))
    parser.add_argument("--key", default=os.environ.get("ADGVS_STATION_KEY"))
    parser.add_argument("--ca-cert", default=os.environ.get("ADGVS_CA_CERT") or None)
    parser.add_argument("--hardware", choices=("fake", "manual", "gpio"),
                        default=intake.hardware,
                        help="feeder + gate: gpio (station), manual (operator, bring-up), "
                             "fake (dry run) (default: ADGVS_INTAKE_HARDWARE or manual)")
    parser.add_argument("--feeder", choices=("fake", "manual", "gpio"), default=None,
                        help="bring-up: override --hardware for the feeder only")
    parser.add_argument("--gate", choices=("fake", "manual", "gpio"), default=None,
                        help="bring-up: override --hardware for the gate only (e.g. "
                             "--feeder manual --gate gpio)")
    parser.add_argument("--source", choices=SOURCES, default=camera_defaults.source,
                        help="camera backend (default: ADGVS_CAMERA_SOURCE or picamera2)")
    parser.add_argument("--device", default=camera_defaults.device,
                        help="picamera2: camera number; opencv: index or URL; file: folder")
    parser.add_argument("--rotate", type=int, choices=(0, 90, 180, 270),
                        default=camera_defaults.rotate)
    parser.add_argument("--refs", default=None,
                        help="screening reference store (default: ADGVS_SCREENING_REFS)")
    parser.add_argument("--sheets", type=int, default=None,
                        help="fake hardware: sheets on the feeder (default: the images in "
                             "--device with --source file, else 1)")
    parser.add_argument("--once", action="store_true",
                        help="one batch (until the feeder is empty), then exit; default: run "
                             "as a service (systemd)")
    parser.add_argument("--check", action="store_true",
                        help="validate the settings, print them (no secrets) and exit; "
                             "moves nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")

    intake = dataclasses.replace(intake, hardware=args.hardware)
    kinds = {"feeder_kind": args.feeder or args.hardware, "gate_kind": args.gate or args.hardware}
    try:
        validate_intake(intake, feeder_settings, gate_settings, **kinds)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    if args.check:
        print(f"Intake settings OK (feeder {kinds['feeder_kind']}, gate {kinds['gate_kind']}, "
              f"camera {args.source}):")
        for line in _settings_lines(intake, feeder_settings, gate_settings,
                                    "gpio" in kinds.values()):
            print("  " + line)
        if kinds["feeder_kind"] == "gpio" and not feeder_settings.stack_pin.strip():
            print("  note: no ADGVS_FEEDER_STACK_PIN: an empty feeder and a misfeed look the "
                  "same, and the service tries a feed every idle poll")
        return 0
    if not args.server or not args.key:
        parser.error("--server and --key (or ADGVS_SERVER / ADGVS_STATION_KEY) are required")

    camera_settings = dataclasses.replace(camera_defaults, source=args.source,
                                          device=args.device, rotate=args.rotate)
    sheets = args.sheets
    if sheets is None:
        sheets = _image_count(args.device) if args.source == "file" else 1
    try:
        feeder, gate = make_hardware(intake.hardware, feeder=feeder_settings, gate=gate_settings,
                                     sheets=sheets, **kinds)
    except HardwareError as e:
        print(f"Hardware unavailable: {e}", file=sys.stderr)
        return 2
    camera = CameraSource(args.source, camera_settings)
    client = BackendClient(args.server, args.key, http=http, ca_cert=args.ca_cert,
                           attempts=intake.http_attempts, backoff=intake.http_backoff)
    station = IntakeStation(client, feeder, gate, camera, settings=intake,
                            screen=default_screen(args.refs))
    if kinds["feeder_kind"] == "gpio" and feeder.paper_loaded() is None:
        log.warning("No stack sensor (ADGVS_FEEDER_STACK_PIN): the service tries a feed every "
                    "%g s while idle", intake.idle_poll)
    try:
        if not args.once:
            signal.signal(signal.SIGTERM, _stop)
            station.run_forever()
            return 0
        try:
            camera.open()
        except CameraError as e:
            print(f"Camera unavailable: {e}", file=sys.stderr)
            return 2
        result = station.run_batch()
        print(result.detail or ("Feeder empty: nothing to do" if result.outcome == NOTHING
                                else result.outcome))
        for sheet in result.sheets:
            where = f"Tray {sheet.routed_tray}" if sheet.routed_tray else "JAM"
            print(f"  sheet {sheet.sequence:>3}  {sheet.decision:<16} -> {where}"
                  f"  {', '.join(sheet.reasons)}")
        return result.exit_code
    finally:
        station.close()


__all__ = ["TRAY_NAMES", "BatchResult", "IntakeStation", "SheetResult", "Stop", "main"]
