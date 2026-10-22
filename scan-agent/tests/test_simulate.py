"""Simulated scans of the issued sample documents read back completely (OCR pre-check)."""

import shutil
from pathlib import Path

import pytest

from scan_agent.evaluate import evaluate
from scan_agent.simulate import LEVELS, simulate

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_set(tmp_path):
    for sub in ("pdfs", "expected"):
        (tmp_path / sub).mkdir()
    for number in ("COE-2026-000001", "TOR-2026-000001"):
        shutil.copy(FIXTURES / f"{number}.pdf", tmp_path / "pdfs")
        shutil.copy(FIXTURES / f"{number}.json", tmp_path / "expected")
    return tmp_path


def scan_and_read(sample_set, level: str) -> list[dict]:
    out = sample_set / level
    assert simulate(sample_set / "pdfs", out, LEVELS[level], batch=1, seed=1) == 2
    assert sorted(p.parent.name for p in out.rglob("*.png")) == ["batch-01", "batch-02"]
    rows = evaluate(out, sample_set / "expected")
    assert len(rows) == 19  # 9 COE fields + 6 TOR fields + 4 grades
    return rows


def test_clean_simulated_scans_read_every_field(sample_set):
    rows = scan_and_read(sample_set, "clean")
    assert [(r["field"], r["read"]) for r in rows if not r["correct"]] == []


def test_rough_scans_lose_readings_but_never_invent_values(sample_set):
    """Worse than a scanner should be: a field may go unread (-> review), but a different
    value read off the paper could wrongly fail a genuine document."""
    rows = scan_and_read(sample_set, "rough")
    assert [(r["field"], r["read"]) for r in rows if not r["correct"] and r["read"]] == []
