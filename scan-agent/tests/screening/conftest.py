"""Shared fixtures for the screening tests: a reference store bootstrapped from synthetic
SAMPLE pages, a cache of screened pages (OCR is the slow part), and a summary of the
measured accuracy and time per page printed at the end of the run."""

import time

import numpy as np
import pytest

from scan_agent.screening import Page, enroll, screen_page
from scan_agent.screening.checks import screen
from scan_agent.screening.references import ReferenceStore
from scan_agent.screening.synthetic import BUILDERS, render

REFS_PER_TYPE = 4  # >= LEARNED_MIN_REFS, so learned positions / occupancy are exercised
STATS: dict[str, list] = {"ms": [], "classified": [], "unknown": []}


@pytest.fixture(scope="session")
def store(tmp_path_factory):
    store = ReferenceStore(tmp_path_factory.mktemp("screening-refs"))
    for doc_type in BUILDERS:
        enroll(store, doc_type, [render(doc_type, 90000 + i).image for i in range(REFS_PER_TYPE)])
    return store


_CACHE: dict = {}


@pytest.fixture(scope="session")
def screened(store):
    """screened(kind, seed, tamper=None, refs=True, as_type=None) -> screening result.
    With `as_type`, the checks of that type (screen()) instead of the full screen_page()."""

    def _screen(kind, seed, tamper=None, refs=True, as_type=None, image=None):
        key = (kind, seed, tamper, refs, as_type)
        if key not in _CACHE:
            if image is None:
                image = render(kind, seed, tamper).image
            page = Page(image)
            start = time.perf_counter()
            if as_type:
                result = screen(page, as_type, store=store if refs else None)
            else:
                result = screen_page(page, store=store if refs else False)
                STATS["ms"].append((time.perf_counter() - start) * 1000)
            _CACHE[key] = result
        return _CACHE[key]

    return _screen


def pytest_terminal_summary(terminalreporter):
    if not STATS["ms"]:
        return
    ms = np.array(STATS["ms"])
    ok = sum(STATS["classified"])
    rejected = sum(STATS["unknown"])
    terminalreporter.write_sep("-", "screening (synthetic SAMPLE pages)")
    terminalreporter.write_line(
        f"type accuracy {ok}/{len(STATS['classified'])} clean in-scope pages, unknown rejected "
        f"{rejected}/{len(STATS['unknown'])}; screen_page {len(ms)} pages: "
        f"p50 {np.percentile(ms, 50):.0f} ms, max {ms.max():.0f} ms on this machine "
        f"(Raspberry Pi 5: expect ~2-3x)")
