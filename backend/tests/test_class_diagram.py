"""The committed Mermaid class diagrams (docs/diagrams/classes-*.mmd) match the models.

Fails when a model changes without re-running backend/scripts/gen_class_diagram.py.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DIAGRAMS = BACKEND.parent / "docs" / "diagrams"
FIX = "re-run `python scripts/gen_class_diagram.py` from backend/ and commit the output"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_class_diagram", BACKEND / "scripts" / "gen_class_diagram.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load_generator()


def test_generated_files_match_committed(gen, tmp_path):
    written = gen.write(tmp_path, None)
    names = sorted(p.name for p in written)
    committed = sorted(p.name for p in DIAGRAMS.glob("classes-*.mmd"))
    assert names == committed, f"diagram set changed: {FIX}"
    for name in names:
        fresh = (tmp_path / name).read_text(encoding="utf-8")
        old = (DIAGRAMS / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert fresh == old, f"{name} is stale: {FIX}"


def test_output_is_deterministic(gen):
    assert gen.render_all() == gen.render_all()


def test_readme_embeds_current_diagrams(gen):
    readme = (DIAGRAMS / "README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = gen.readme_block(gen.render_all())
    assert block in readme, f"docs/diagrams/README.md is stale: {FIX}"


def test_every_table_is_drawn(gen):
    from app.db import Base

    overview = gen.render_all()["classes-overview.mmd"]
    for table in Base.metadata.tables:
        assert f"({table})" in overview, table
