"""OCR accuracy run (Section 21: >= 98% field-level accuracy on 100+ clean 300 DPI scans).

    python -m scan_agent.evaluate --scans ./scans --expected ./expected --report report.csv

--scans     folder of scanned pages (images or PDFs), in stack order; or a folder of batch
            subfolders, each one stack (as scan_agent.simulate writes them)
--expected  one JSON per issued document, named <DOCUMENT_NUMBER>.json, containing
            {"snapshot": {...}} as exported for the issued document (see CI sample-pdfs)
Zones come from the repo's backend default templates, so this runs offline.
"""

import argparse
import csv
import importlib.util
import json
import re
import sys
import unicodedata
from pathlib import Path

from scan_agent.capture.filedrop import FolderSource
from scan_agent.pipeline import process_stack

FIELDS = ["document_number", "student_name", "student_number", "program", "date_issued",
          "gwa", "year_level", "semester", "academic_year", "purpose"]
DEFAULTS = Path(__file__).parents[2] / "backend" / "app" / "documents" / "defaults" / "__init__.py"


ID_FIELDS = {"document_number", "student_number"}


def same_as_validation(field: str, read: str, expected: str) -> bool:
    """Count a read as correct when validation would accept it as matching: IDs ignoring
    spacing and dashes, text ignoring case, accents (Ñ = N), punctuation and spacing.
    Mirrors backend app/services/validation.py normalize_id / normalize_text."""
    if field in ID_FIELDS:
        def norm(v: str) -> str:
            return re.sub(r"[\s\-_.]", "", v).upper()
    else:
        def norm(v: str) -> str:
            decomposed = unicodedata.normalize("NFKD", v)
            plain = "".join(c for c in decomposed if not unicodedata.combining(c))
            return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", plain.casefold())).strip()
    return norm(read) == norm(expected)


def _zones() -> dict:
    spec = importlib.util.spec_from_file_location("defaults", DEFAULTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {"TOR": module.TOR_ZONES, "COE": module.COE_ZONES}


def evaluate(scans: Path, expected: Path) -> list[dict]:
    zones = _zones()
    truth = {p.stem: json.loads(p.read_text(encoding="utf-8"))["snapshot"]
             for p in expected.glob("*.json")}

    def lookup(token, number):
        snap = truth.get(number or "")
        if snap is None:
            return {"found": False}
        return {"found": True, "document_number": number,
                "field_zones": zones[snap["document_type"]]}

    stacks = sorted(p for p in scans.iterdir() if p.is_dir()) or [scans]
    rows = []
    for doc in (d for stack in stacks for d in process_stack(FolderSource(stack).pages(), lookup)):
        number = doc.payload.get("document_number_read") or "?"
        snap = truth.get(number)
        if snap is None:
            rows.append({"document": number, "field": "(document)", "expected": "",
                         "read": "", "confidence": "", "correct": False})
            continue
        for name in FIELDS:
            if name not in snap:
                continue
            read = doc.payload["fields"].get(name, {})
            rows.append({"document": number, "field": name, "expected": snap[name],
                         "read": read.get("value", ""), "confidence": read.get("confidence"),
                         "correct": same_as_validation(name, read.get("value", ""),
                                                       str(snap[name]))})
        table_rows = (doc.payload.get("table") or {}).get("rows", [])
        # Matched like backend validation._compare_table: same subject code and grade,
        # under the same term (or no term read). A row under the wrong term is a miss there.
        printed = [(r["subject_code"], r["grade"], r.get("term")) for r in table_rows]
        for term in snap.get("terms", []):
            label = f"{term['semester']}, AY {term['academic_year']}"
            for subject in term["subjects"]:
                code, grade = subject["subject_code"], subject["grade"]
                hit = next((p for p in printed if p[:2] == (code, grade)
                            and p[2] in (None, label)), None)
                if hit:
                    printed.remove(hit)
                    read = grade
                else:  # report a different grade or term read for the same subject, if any
                    other = next((p for p in printed if p[0] == code), None)
                    read = "" if other is None else (
                        other[1] if other[1] != grade else f"{grade} under {other[2]}")
                rows.append({"document": number, "field": f"grade {code}",
                             "expected": grade, "read": read,
                             "confidence": "", "correct": hit is not None})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scan_agent.evaluate")
    parser.add_argument("--scans", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("ocr-accuracy.csv"))
    parser.add_argument("--min-accuracy", type=float, default=98.0,
                        help="exit non-zero below this field accuracy (Section 21: 98)")
    args = parser.parse_args(argv)

    rows = evaluate(args.scans, args.expected)
    with args.report.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["document"])
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    correct = sum(r["correct"] for r in rows)
    documents = len({r["document"] for r in rows})
    accuracy = 100 * correct / total if total else 0.0
    print(f"{documents} documents, {total} fields, {correct} correct: {accuracy:.2f}% "
          f"(target >= {args.min_accuracy:g}%). Details: {args.report}")
    by_field: dict[str, list[bool]] = {}
    for r in rows:
        by_field.setdefault(r["field"].split(" ")[0], []).append(r["correct"])
    for field, results in sorted(by_field.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        print(f"  {field:<16} {100 * sum(results) / len(results):6.2f}%  ({len(results)})")
    for r in rows:
        if not r["correct"]:
            print(f"  MISS {r['document']} {r['field']}: expected {r['expected']!r}, "
                  f"read {r['read']!r}")
    return 0 if accuracy >= args.min_accuracy else 1


if __name__ == "__main__":
    sys.exit(main())
