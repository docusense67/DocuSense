"""Time the scan agent's OCR on the bundled TOR/COE fixtures (spec: result < 15 s/document).

    /opt/adgvs/venv/bin/python /opt/adgvs/station/ocr_timing.py          # on the Pi
    scan-agent/.venv/Scripts/python deploy/station/ocr_timing.py         # in a checkout

Pages are rendered at 300 DPI first (the scanner delivers them already rendered), then each
stack runs through the real pipeline: clean-up, QR, footer OCR, splitting, field and
grade-table OCR. Zones come from the backend's default templates, so no server is needed.
The 15 s budget also covers scanning and the upload, so OCR should leave headroom.
Exit code 1 if a document takes 15 s or more, or if its number is misread.
"""

import argparse
import importlib.util
import sys
import time
from pathlib import Path

LIMIT_S = 15.0
FIXTURES = ("COE-2026-000001.pdf", "TOR-2026-000001.pdf", "TOR-SAMPLE-LONG.pdf")
ZONES_FILE = Path("backend") / "app" / "documents" / "defaults" / "__init__.py"


def find_root(given: str | None) -> Path:
    candidates = [Path(given)] if given else [Path(__file__).resolve().parents[n] for n in (1, 2)]
    for root in candidates:
        if (root / "scan-agent" / "tests" / "fixtures").is_dir() and (root / ZONES_FILE).is_file():
            return root
    raise SystemExit(f"No scan-agent/tests/fixtures and {ZONES_FILE} under {candidates}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="folder holding scan-agent/ and backend/ "
                                       "(default: next to this script, or the repo root)")
    args = parser.parse_args(argv)
    root = find_root(args.root)

    import numpy as np
    import pypdfium2

    from scan_agent.capture.base import CapturedPage
    from scan_agent.pipeline import process_stack

    spec = importlib.util.spec_from_file_location("template_defaults", root / ZONES_FILE)
    defaults = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(defaults)
    zones = {"TOR": defaults.TOR_ZONES, "COE": defaults.COE_ZONES}

    def lookup(token, number):  # stands in for the backend: zones only, never student data
        doc_type = "COE" if (number or "").startswith("COE") else "TOR"
        return {"found": True, "document_number": number, "document_type": doc_type,
                "field_zones": zones[doc_type]}

    failures = 0
    for name in FIXTURES:
        pdf = root / "scan-agent" / "tests" / "fixtures" / name
        images = [np.array(page.render(scale=300 / 72).to_pil().convert("L"))
                  for page in pypdfium2.PdfDocument(str(pdf))]
        pages = [CapturedPage(sequence=i, image=img) for i, img in enumerate(images, start=1)]
        started = time.perf_counter()
        documents = process_stack(pages, lookup)
        seconds = time.perf_counter() - started
        read = documents[0].payload.get("document_number_read") if documents else None
        expected = name.removesuffix(".pdf")
        number_ok = expected.endswith("SAMPLE-LONG") or read == expected
        ok = seconds < LIMIT_S and len(documents) == 1 and number_ok
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name:<22} {len(images)} page(s) "
              f"{seconds:5.1f} s  read {read or '?'}")
    print(f"Budget: under {LIMIT_S:.0f} s per document, scan to result (spec Section 21)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
