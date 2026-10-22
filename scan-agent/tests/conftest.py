import importlib.util
import json
from pathlib import Path

import numpy as np
import pypdfium2
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND_DEFAULTS = (Path(__file__).parents[2] / "backend" / "app" / "documents" / "defaults"
                    / "__init__.py")


def _load_backend_zones():
    """Use the backend's real default zone map so agent and templates never drift apart."""
    spec = importlib.util.spec_from_file_location("backend_defaults", BACKEND_DEFAULTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {"TOR": module.TOR_ZONES, "COE": module.COE_ZONES}


ZONES = _load_backend_zones()


def render(pdf_name: str) -> list[np.ndarray]:
    return [np.array(page.render(scale=300 / 72).to_pil().convert("L"))
            for page in pypdfium2.PdfDocument(str(FIXTURES / pdf_name))]


def snapshot(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def tor_pages():
    return render("TOR-2026-000001.pdf")


@pytest.fixture(scope="session")
def coe_pages():
    return render("COE-2026-000001.pdf")


@pytest.fixture(scope="session")
def long_tor_pages():
    return render("TOR-SAMPLE-LONG.pdf")


@pytest.fixture
def lookup():
    """Stand-in for the backend lookup: zones by document type, never student data."""
    calls = []

    def _lookup(token, document_number):
        calls.append((token, document_number))
        number = document_number or ""
        doc_type = "COE" if number.startswith("COE") else "TOR"
        return {"found": True, "document_number": number, "document_type": doc_type,
                "field_zones": ZONES[doc_type]}

    _lookup.calls = calls
    return _lookup
