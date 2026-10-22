"""Incoming-document screening (S-02 type identification, S-03 screening indicators,
S-05 reference similarity) for the intake station.

    from scan_agent.screening import screen_page
    result = screen_page(page_image)   # the doc_type/checks/indicators/similarity/fields blocks

The agent only measures and extracts (CLAUDE.md). Every decision (passed screening / needs
manual review, tray) is the backend's, from these numbers. Output format:
docs/intake/screening-contract.md.
"""

from __future__ import annotations

import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from .checks import analyze, best_variant, screen
from .classify import classify
from .page import Page
from .profile import DOC_TYPES, UNKNOWN, load_profiles
from .references import ReferenceStore, enroll, similarity

SCHEMA = 1

try:
    AGENT_VERSION = version("adgvs-scan-agent")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    AGENT_VERSION = "0.0.0"


def resolve_store(store) -> ReferenceStore | None:
    """A ReferenceStore, a path to one, False (no references), or None (the default store,
    ADGVS_SCREENING_REFS or ~/.adgvs/screening-refs, if it exists)."""
    if store is False:
        return None
    if store is None:
        return ReferenceStore.default()
    if isinstance(store, ReferenceStore):
        return store
    return ReferenceStore(Path(store))


def screen_page(image: np.ndarray | Page, *, store=None, profiles=None,
                capture: dict | None = None, quality: dict | None = None,
                timings_ms: dict | None = None) -> dict:
    """Screening result for one normalized page (contract schema 1). `capture`, `quality`
    and their timings come from the capture / quality code and are passed through."""
    start = time.perf_counter()
    store = resolve_store(store)
    profiles = profiles or load_profiles()
    page = image if isinstance(image, Page) else Page(image)
    result: dict = {"schema": SCHEMA, "agent_version": AGENT_VERSION}
    if capture is not None:
        result["capture"] = capture
    if quality is not None:
        result["quality"] = quality
    t0 = time.perf_counter()
    page.words  # noqa: B018 - the one OCR pass, timed on its own
    ocr_ms = (time.perf_counter() - t0) * 1000
    doc_type = classify(page, store, profiles)
    result["doc_type"] = doc_type
    if doc_type["predicted"] == UNKNOWN:
        # No profile to check against: checks / similarity / fields are not measured.
        result["indicators"] = []
    else:
        analysis = analyze(page, best_variant(page, doc_type["predicted"], profiles), store)
        result["checks"] = analysis.checks
        result["indicators"] = analysis.indicators
        sim = similarity(page, doc_type["predicted"], store, profiles)
        if sim is not None:
            result["similarity"] = sim
        if analysis.fields:
            result["fields"] = analysis.fields
    timings = dict(timings_ms or {})
    timings["screening"] = round((time.perf_counter() - start) * 1000)
    timings["screening_ocr"] = round(ocr_ms)
    result["timings_ms"] = timings
    return result


__all__ = ["DOC_TYPES", "UNKNOWN", "Page", "ReferenceStore", "analyze", "classify", "enroll",
           "load_profiles", "resolve_store", "screen", "screen_page", "similarity"]
