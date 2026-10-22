"""Intake demo driver: the camera station simulated with SAMPLE documents.

    python -m scan_agent intake-demo --server URL --key STATION_KEY [--count N] [--seed S]

For progress videos and client demos while the feeder / gate hardware is not finished. It
renders SAMPLE look-alike pages (scan_agent.screening.synthetic, every page watermarked
SAMPLE) about one invented learner surnamed SAMPLE: a clean page of each of the four
incoming document types, a tampered page (text added to the good moral certificate), an
out-of-scope page (a memo) and a poor capture (dim and blurred). Then it runs them through
the real station cycle (scan_agent.intake.IntakeStation) in one batch: the real quality
measurement and screen_page, the real upload with the station key, the BACKEND's decision,
and a pretend gate that puts each sheet in the tray the backend chose and reports it, like
the station does. Only the feeder, the camera and the gate are simulated; the agent still
only measures (CLAUDE.md).

`--count N` takes the first N pages of that list; beyond 7, extra clean pages about other
SAMPLE learners. `--seed S` picks the learner and the page variations (same seed, same
pages: rehearse, purge, and the recording run gives the same result). Synthetic pages say
nothing about accuracy on real documents, and a clean SAMPLE page can still be flagged:
that is what manual review is for.

Use it only with a station registered for the demo ("SAMPLE Demo Intake Station" from
`python -m app.manage seed-demo`): `purge-demo` removes that station's documents.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from dataclasses import dataclass

import cv2
import httpx
import numpy as np

from scan_agent.capture.base import ScannerState
from scan_agent.capture.camera import CameraError, CameraPage
from scan_agent.capture.page import PageDetection, level_page
from scan_agent.client import BackendClient
from scan_agent.config import IntakeSettings
from scan_agent.hardware import Compartment, FakeFeeder, FakeGate
from scan_agent.intake import IntakeStation, default_screen
from scan_agent.quality import assess_page
from scan_agent.screening import synthetic

LABELS = {"psa_birth_certificate": "PSA birth certificate",
          "jhs_report_card": "JHS report card (SF9)",
          "good_moral_certificate": "Good moral certificate",
          "jhs_completion_certificate": "JHS completion certificate"}
# (document type or unknown kind, tamper, poor capture, what the presenter should expect)
PLAN = [
    ("psa_birth_certificate", None, False, "clean"),
    ("jhs_report_card", None, False, "clean"),
    ("good_moral_certificate", None, False, "clean"),
    ("jhs_completion_certificate", None, False, "clean"),
    ("good_moral_certificate", "added_text", False, "tampered: 'WITH HIGHEST HONORS' added"),
    ("memo", None, False, "not an enrollment document"),
    ("jhs_report_card", None, True, "poor capture: dim and blurred"),
]
DEFAULT_SEED = 1
SURNAME = "SAMPLE"


@dataclass(frozen=True)
class DemoPage:
    kind: str
    tamper: str | None
    poor: bool
    expect: str
    person: tuple[str, str, str] | None
    lrn: str | None
    image: np.ndarray  # the page as the camera would deliver it, before levelling

    @property
    def label(self) -> str:
        name = LABELS.get(self.kind, self.kind.replace("_", " ").capitalize())
        return f"{name}, {self.expect}"


def learner(rng: random.Random) -> tuple[tuple[str, str, str], str]:
    """An invented learner: random first and middle names, surname SAMPLE, and an LRN that
    starts with 999999 (no school ID does)."""
    first = rng.choice(synthetic.FIRST)
    middle = rng.choice(synthetic.LAST)
    return (first, middle, SURNAME), f"999999{rng.randint(0, 999_999):06d}"


def poor_capture(image: np.ndarray) -> np.ndarray:
    """A dim, out-of-focus photo of the page (what a bad camera exposure looks like)."""
    blurred = cv2.GaussianBlur(image, (0, 0), 2.0)
    return cv2.convertScaleAbs(blurred, alpha=0.3)


def build_pages(count: int = len(PLAN), seed: int = DEFAULT_SEED) -> list[DemoPage]:
    rng = random.Random(f"intake-demo:{seed}")
    person, lrn = learner(rng)
    plan = list(PLAN[:count])
    extra = 0
    while len(plan) < count:  # more clean pages, about other SAMPLE learners
        kind = list(LABELS)[extra % len(LABELS)]
        plan.append((kind, None, False, "clean (another learner)"))
        extra += 1
    pages, others = [], {}
    for index, (kind, tamper, poor, expect) in enumerate(plan):
        about, number = (None, None)
        if kind in synthetic.BUILDERS:
            if expect.startswith("clean (another"):
                key = index // len(LABELS)
                others.setdefault(key, learner(rng))
                about, number = others[key]
            else:
                about, number = person, lrn
        sample = synthetic.render(kind, seed * 100 + index, tamper, rough=poor, person=about,
                                  lrn=number)
        image = poor_capture(sample.image) if poor else sample.image
        pages.append(DemoPage(kind, tamper, poor, expect, about, number, image))
    return pages


class SampleCamera:
    """Stands in for CameraSource: hands over the next demo page as the captured page.

    The quality block is measured by the real code (scan_agent.quality.assess_page) on the
    page pixels: exposure on the page as captured, sharpness after levelling, like a real
    capture. The page geometry (skew, coverage, DPI) is nominal: there is no camera frame."""

    name = "sample"

    def __init__(self, pages: list[DemoPage]):
        self.pages = pages
        self.detail = None
        self.captured: list[DemoPage] = []

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def status(self) -> ScannerState:
        return ScannerState.READY

    def capture(self, sequence: int = 1, **_) -> CameraPage:
        if sequence > len(self.pages):
            raise CameraError("no SAMPLE page left")
        page = self.pages[sequence - 1]
        self.captured.append(page)
        raw = page.image
        levelled = level_page(raw)
        h, w = raw.shape
        corners = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        detection = PageDetection(corners, coverage=0.85, cut_off=False, skew_deg=0.0,
                                  effective_dpi=round(w / (210 / 25.4)), contrast=120.0)
        return CameraPage(sequence=sequence, image=levelled, dpi=300, source="SAMPLE page",
                          capture={"source": "sample", "width": int(w), "height": int(h),
                                   "attempts": 1},
                          quality=assess_page(raw, levelled, detection),
                          timings_ms={"capture": 0, "quality": 0})


class RecordingScreen:
    """screen_page, keeping the predicted type of each page for the report."""

    def __init__(self, screen):
        self.screen = screen
        self.predicted: list[tuple[str, float]] = []

    def __call__(self, image, **blocks) -> dict:
        result = self.screen(image, **blocks)
        doc_type = result.get("doc_type") or {}
        self.predicted.append((str(doc_type.get("predicted")),
                               float(doc_type.get("confidence") or 0.0)))
        return result


def run(client: BackendClient, pages: list[DemoPage], screen, *,
        settings: IntakeSettings | None = None, out=print) -> int:
    compartment = Compartment()
    camera = SampleCamera(pages)
    recorder = RecordingScreen(screen)
    station = IntakeStation(client, FakeFeeder(len(pages), compartment=compartment),
                            FakeGate(compartment), camera,
                            settings=settings or IntakeSettings(hardware="fake",
                                                                clear_timeout=2.0),
                            screen=recorder)
    try:
        batch = station.run_batch()
    finally:
        station.close()
    out(batch.detail or batch.outcome)
    rows = [("#", "Page fed (SAMPLE)", "Read as", "Decision", "Tray", "Reasons")]
    for sheet in batch.sheets:
        page = pages[sheet.sequence - 1]
        predicted, confidence = recorder.predicted[sheet.sequence - 1]
        decision = {"passed_screening": "passed screening",
                    "manual_review": "needs manual review"}.get(sheet.decision, sheet.decision)
        tray = f"Tray {sheet.routed_tray}" if sheet.routed_tray else "JAM"
        rows.append((str(sheet.sequence), page.label, f"{predicted} {confidence:.2f}", decision,
                     tray, ", ".join(sheet.reasons) or "-"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        out("  " + "  ".join(c.ljust(widths[n]) for n, c in enumerate(row)).rstrip())
        if i == 0:
            out("  " + "  ".join("-" * w for w in widths))
    if batch.summary:
        s = batch.summary
        out(f"Backend batch summary: {s.get('total')} document(s), {s.get('passed_screening')} "
            f"passed screening, {s.get('manual_review')} need manual review")
    return batch.exit_code


def main(argv: list[str] | None = None, *, http: httpx.Client | None = None, screen=None,
         out=print) -> int:
    parser = argparse.ArgumentParser(prog="python -m scan_agent intake-demo",
                                     description="Simulate the intake station with SAMPLE "
                                                 "documents (real screening and backend).")
    parser.add_argument("--server", default=os.environ.get("ADGVS_SERVER"))
    parser.add_argument("--key", default=os.environ.get("ADGVS_STATION_KEY"),
                        help="key of a station registered for the demo")
    parser.add_argument("--ca-cert", default=os.environ.get("ADGVS_CA_CERT") or None)
    parser.add_argument("--count", type=int, default=len(PLAN),
                        help=f"pages in the batch (default {len(PLAN)}: one of each case)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="learner and page variations (same seed, same pages)")
    parser.add_argument("--refs", default=None,
                        help="screening reference store (default: ADGVS_SCREENING_REFS)")
    parser.add_argument("-v", "--verbose", action="store_true", help="station log lines too")
    args = parser.parse_args(argv)
    if (not args.server and http is None) or not args.key:
        parser.error("--server and --key (or ADGVS_SERVER / ADGVS_STATION_KEY) are required")
    if not 1 <= args.count <= 50:
        parser.error("--count must be between 1 and 50")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(message)s")

    pages = build_pages(args.count, args.seed)
    person = next((p for p in pages if p.person and p.expect == "clean"), None)
    who = (f"learner {' '.join(person.person)} (LRN {person.lrn})"
           if person and person.person else "SAMPLE pages")
    out(f"SAMPLE intake demo: {len(pages)} page(s), seed {args.seed}, {who}")
    out("Screening each page (real OCR, a few seconds per page)...")
    client = BackendClient(args.server, args.key, http=http, ca_cert=args.ca_cert)
    return run(client, pages, screen or default_screen(args.refs), out=out)


if __name__ == "__main__":
    sys.exit(main())
