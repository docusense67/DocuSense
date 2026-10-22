"""Station service with the scanner driven through SANE (the Raspberry Pi station).

    python -m scan_agent --source sane --folder /var/lib/adgvs/scans --watch \\
        --delete-after-upload

Capture and upload are two steps of one process:

1. Capture: when a session is open on the dashboard and paper is in the feeder, the stack
   is fed and each page is saved as a PNG in `<folder>/.capture/`, which the watcher
   ignores (hidden, and not a scan file in the folder itself).
2. Upload: when the feeder runs empty the stack is complete. Its pages are moved (renamed,
   never copied or deleted) into the folder, and the `Watcher` (scan_agent/watch.py)
   uploads them exactly as it does for a scanner's own software: retried when the
   network drops, never listed twice, and deleted only after the upload succeeded.
   `watch.py` stays the only code that deletes scanner files.

On a jam or double-feed the pages already scanned stay in `.capture/`, the dashboard shows
"... re-feed from sheet N" (UC: the session survives a jam), and the re-fed sheets
continue the same stack from N. Pages on disk also survive a crash or power cut: when the
service starts, pages left in `.capture/` are released and checked as they are (an
unfinished document is flagged with its missing pages), so nothing is silently lost.
"""

import logging
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2
import httpx

from scan_agent.capture.base import CapturedPage, ScannerError, ScannerState
from scan_agent.pipeline import process_stack
from scan_agent.watch import Watcher

log = logging.getLogger("scan_agent.station")

CAPTURE_DIR = ".capture"


class ScannerStation:
    def __init__(self, client, source, folder: str | Path, *, delete_after_upload: bool = False,
                 heartbeat: float = 15.0, clock: Callable[[], float] = time.monotonic,
                 process=process_stack):
        self.client = client
        self.source = source  # SaneSource, or a stand-in with status/pages/paper_loaded
        self.folder = Path(folder)
        self.staging = self.folder / CAPTURE_DIR
        self.heartbeat = heartbeat
        self.clock = clock
        # A stack appears in the folder all at once (renamed when complete): no settle wait.
        self.watcher = Watcher(client, folder, delete_after_upload=delete_after_upload,
                               settle=0.0, heartbeat=heartbeat, clock=clock, process=process)
        self.held: ScannerError | None = None  # stack stopped by a jam, waiting for re-feed
        self._stamp: str | None = None  # file-name prefix of the stack being captured
        self._scanner: tuple[float, ScannerState] | None = None  # cached presence check
        released = self.release()
        if released:
            log.warning("Released %d page(s) left by an unfinished stack", released)

    # ---- helpers -------------------------------------------------------------------------

    def report(self, state: str, detail: str | None = None, force: bool = False) -> None:
        self.watcher.report(state, detail, force=force)

    def scanner_state(self) -> ScannerState:
        """Is the scanner connected? Checked at most once per heartbeat (USB probe)."""
        now = self.clock()
        if self._scanner is None or now - self._scanner[0] >= self.heartbeat:
            self._scanner = (now, self.source.status())
        return self._scanner[1]

    def session_open(self) -> bool:
        try:
            self.client.session()
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                log.warning("Session check failed: %s", e)
            return False
        except httpx.HTTPError as e:
            log.warning("Server unreachable, not feeding: %s", e)
            return False

    def _paper(self) -> bool | None:
        """The feeder's paper sensor: True/False, or None when the scanner has none (then
        feeding is simply tried; an empty feeder ends the attempt at once)."""
        sensor = getattr(self.source, "paper_loaded", None)
        return sensor() if sensor else None

    def save(self, page: CapturedPage) -> Path:
        """Write a page to the staging folder (temp name + rename: never a torn file)."""
        self.staging.mkdir(parents=True, exist_ok=True)
        ok, png = cv2.imencode(".png", page.image)
        if not ok:
            raise RuntimeError("Could not encode page image")
        path = self.staging / f"scan-{self._stamp}-{page.sequence:04d}.png"
        part = path.with_name(path.name + ".part")
        part.write_bytes(png.tobytes())
        os.replace(part, path)
        return path

    def release(self) -> int:
        """Hand a complete stack to the watcher: move its pages into the watched folder."""
        if not self.staging.is_dir():
            return 0
        pages = sorted(p for p in self.staging.iterdir()
                       if p.is_file() and p.suffix.lower() == ".png")
        for path in pages:
            os.replace(path, self.folder / path.name)
        return len(pages)

    # ---- one pass ------------------------------------------------------------------------

    def tick(self) -> str:
        """One pass of the loop. Returns what happened (for logs and tests)."""
        if self.held is not None:
            # Keep the jam on the dashboard (heartbeat) and continue once sheets are re-fed.
            self.report(self.held.state.value, str(self.held))
            if self._paper() is False:
                return "held"
            return self.capture(self.held.next_sequence)
        if not self.folder.is_dir() or self.watcher.pending():
            return self.watcher.tick()  # upload the last stack (or wait for a session)
        if self.scanner_state() is ScannerState.OFFLINE:
            self.report("offline", getattr(self.source, "detail", None)
                        or "Scanner not connected")
            return "offline"
        paper = self._paper()
        if paper is False:
            self.report("ready", "Waiting for paper")
            return "idle"
        if not self.session_open():
            # Feed only into a session: the operator's Single/Bulk choice picks the tray.
            self.report("ready", "Paper loaded: start Single or Bulk on the dashboard" if paper
                        else "Start Single or Bulk on the dashboard, then load the paper")
            return "no-session"
        return self.capture(1)

    def capture(self, start: int) -> str:
        self._stamp = self._stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
        count = 0
        try:
            for page in self.source.pages(start):
                self.save(page)
                count += 1
                # Same detail every page: sent once, then refreshed every heartbeat.
                self.report("scanning", "Scanning pages")
        except ScannerError as e:
            if e.state is ScannerState.OFFLINE and count == 0 and self.held is None:
                self._scanner = None
                self._stamp = None
                self.report("offline", str(e), force=True)
                return "offline"
            # Jam / double-feed / cover open, or the scanner vanished mid-stack: keep the
            # pages and resume from the sheet that failed.
            if self.held is None or count or e.state is not self.held.state:
                log.warning("Stack stopped after sheet %d: %s", e.next_sequence - 1, e)
            self.held = e
            self._scanner = None
            self.report(e.state.value, str(e), force=True)
            return e.state.value
        if count == 0:  # feeder empty: nothing new (still waiting for a re-feed if held)
            if self.held is None:
                self._stamp = None
                self.report("ready", "Waiting for paper")
                return "idle"
            return "held"
        released = self.release()
        log.info("Stack complete: %d page(s) captured", released)
        self.held = None
        self._stamp = None
        return "captured"

    def run_forever(self, interval: float = 2.0) -> None:
        log.info("Scanning into %s (Ctrl+C to stop)", self.folder)
        try:
            while True:
                try:
                    self.tick()
                except Exception:  # keep the station service alive; the next pass retries
                    log.exception("Unexpected error; retrying")
                time.sleep(interval)
        except KeyboardInterrupt:
            self.report("offline", "Station software stopped", force=True)
        finally:
            close = getattr(self.source, "close", None)
            if close:
                close()
