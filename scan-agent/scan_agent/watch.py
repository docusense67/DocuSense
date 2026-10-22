"""Station service mode: watch the scanner's output folder and run unattended.

    python -m scan_agent --server https://adgvs.school.edu.ph --key $KEY --folder D:\\scans \
        --watch --delete-after-upload

Most ADF scanners save each batch as files in a folder (their own software does the
feeding). This loop:

- reports the station's state every `heartbeat` seconds, so the dashboard shows Ready or
  Offline (UC4: an offline station blocks new sessions);
- treats the files as one stack once nothing has changed for `settle` seconds;
- uploads that stack to the session the operator opened (Single or Bulk), then deletes the
  files or moves them to `uploaded/`;
- keeps the files and retries if no session is open or the server cannot be reached, so a
  stack is never lost.

Decisions stay on the server; this only reads pages and sends what it read.
"""

import logging
import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from scan_agent.capture.filedrop import IMAGE_SUFFIXES, FolderSource
from scan_agent.pipeline import process_stack

log = logging.getLogger("scan_agent.watch")

SCAN_SUFFIXES = IMAGE_SUFFIXES | {".pdf"}


def _is_scan(path: Path) -> bool:
    # Scanner software writes to temporary names first; ignore those and hidden files.
    return (path.is_file() and path.suffix.lower() in SCAN_SUFFIXES
            and not path.name.startswith((".", "~")))


class Watcher:
    def __init__(self, client, folder: str | Path, *, delete_after_upload: bool = False,
                 settle: float = 5.0, heartbeat: float = 15.0,
                 clock: Callable[[], float] = time.monotonic, process=process_stack):
        self.client = client
        self.folder = Path(folder)
        self.delete_after_upload = delete_after_upload
        self.settle = settle
        self.heartbeat = heartbeat
        self.clock = clock
        self.process = process
        self._snapshot: tuple = ()
        self._changed_at = clock()
        self._last_report: tuple[float, str, str | None] | None = None

    # ---- status ------------------------------------------------------------------------

    def report(self, state: str, detail: str | None = None, force: bool = False) -> None:
        """Send the state when it changes, and at least every `heartbeat` seconds."""
        now = self.clock()
        last = self._last_report
        if (not force and last is not None and (last[1], last[2]) == (state, detail)
                and now - last[0] < self.heartbeat):
            return
        try:
            self.client.status(state, detail)
            self._last_report = (now, state, detail)
        except httpx.HTTPError as e:
            log.warning("Could not reach the server to report %s: %s", state, e)

    # ---- one pass ------------------------------------------------------------------------

    def pending(self) -> list[Path]:
        return sorted(p for p in self.folder.iterdir() if _is_scan(p))

    def tick(self) -> str:
        """One pass of the loop. Returns what happened (for logs and tests)."""
        if not self.folder.is_dir():
            self.report("offline", f"Scanner output folder not found: {self.folder}")
            return "offline"

        files = self.pending()
        snapshot = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files)
        if snapshot != self._snapshot:
            self._snapshot, self._changed_at = snapshot, self.clock()
        if not files:
            self.report("ready", "Waiting for paper")
            return "idle"
        if self.clock() - self._changed_at < self.settle:
            self.report("scanning", f"Receiving pages ({len(files)} file(s) so far)")
            return "receiving"

        try:
            session = self.client.session()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            self.report("ready", f"{len(files)} file(s) waiting: start Single or Bulk "
                                 "on the dashboard to check them")
            return "waiting"
        except httpx.HTTPError as e:
            log.warning("Server unreachable, keeping %d file(s): %s", len(files), e)
            return "unreachable"

        return self.upload(files, session)

    def upload(self, files: list[Path], session: dict) -> str:
        self.report("scanning", f"Checking {len(files)} file(s)", force=True)
        source_of: dict[int, Path] = {}  # page sequence -> the file it came from

        def pages():
            for page in FolderSource(self.folder, files=files).pages():
                source_of[page.sequence] = Path(page.source)
                yield page

        results, sent = [], set()
        try:
            for document in self.process(pages(), self.client.lookup):
                results.append(self.client.submit(session["session_id"], document))
                sent.update(p.sequence for p in document.pages)
        except httpx.HTTPError as e:
            # Finish only the files whose every page arrived; the rest are sent again on the
            # next pass, so nothing is lost and nothing is listed twice.
            pages_of: dict[Path, set[int]] = {}
            for seq, src in source_of.items():
                pages_of.setdefault(src, set()).add(seq)
            done = [f for f in files if f in pages_of and pages_of[f] <= sent]
            self.finish(done)
            log.warning("Upload failed after %d document(s); %d file(s) kept for the next "
                        "try: %s", len(results), len(files) - len(done), e)
            self.report("ready", "Upload interrupted; retrying the rest", force=True)
            self._snapshot = ()
            return "retry"

        self.finish(files)
        counts: dict[str, int] = {}
        for r in results:
            counts[r["result"]] = counts.get(r["result"], 0) + 1
        summary = ", ".join(f"{n} {result}" for result, n in sorted(counts.items()))
        log.info("Session %s: %d document(s): %s", session["session_id"], len(results), summary)
        self.report("ready", f"Last stack: {len(results)} document(s)", force=True)
        self._snapshot = ()
        return "uploaded"

    def finish(self, files: list[Path]) -> None:
        """Section 18: no scans kept on the station. Without the flag (testing), keep them
        out of the way in uploaded/ so they are not sent twice."""
        if self.delete_after_upload:
            for path in files:
                path.unlink(missing_ok=True)
            return
        done = self.folder / "uploaded" / datetime.now().strftime("%Y%m%d-%H%M%S")
        done.mkdir(parents=True, exist_ok=True)
        for path in files:
            shutil.move(str(path), done / path.name)

    def run_forever(self, interval: float = 1.0) -> None:
        log.info("Watching %s (Ctrl+C to stop)", self.folder)
        try:
            while True:
                try:
                    self.tick()
                except Exception:  # keep the station service alive; the next pass retries
                    log.exception("Unexpected error; retrying")
                time.sleep(interval)
        except KeyboardInterrupt:
            self.report("offline", "Station software stopped", force=True)
