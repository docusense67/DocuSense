"""Write the API reference (OpenAPI 3) to docs/api/openapi.json (Section 26 deliverable).

    cd backend && .venv/Scripts/python scripts/export_openapi.py

Production turns the interactive /docs page off, so this file is the reference. View it with
any OpenAPI viewer (e.g. paste into editor.swagger.io, or run the dev server and open /docs).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "docs" / "api" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(app.openapi()['paths'])} paths)")
