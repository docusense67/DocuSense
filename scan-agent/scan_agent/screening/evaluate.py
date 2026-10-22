"""Measure type accuracy, indicator detection and time per page on labelled pages.

    python -m scan_agent screening-eval --synthetic 10                 # synthetic SAMPLE set
    python -m scan_agent screening-eval --dir samples/ --refs <store>  # client samples
    python -m scan_agent screening-calibrate --dir samples/            # refit confidence

A labelled folder holds one sub-folder per document type (`psa_birth_certificate/`, ...,
`unknown/`) with page images (PNG/JPEG) or PDFs; optional `tampered/<indicator_code>/
<doc_type>/` sub-folders hold known-altered pages and the indicator each must raise.
Reports carry file names, types and scores only, never field values.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import screen_page
from .checks import screen
from .classify import fit_calibration, write_calibration
from .page import Page
from .profile import DOC_TYPES, UNKNOWN
from .references import ReferenceStore
from .synthetic import BUILDERS, TAMPERS, UNKNOWN_KINDS, render

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SEVERITY_LINE = 0.5  # the backend's default indicator threshold, for reporting only


def load_images(path: Path) -> list[np.ndarray]:
    if path.suffix.lower() == ".pdf":
        import pypdfium2  # noqa: PLC0415 - optional (pdf extra)

        return [np.array(page.render(scale=300 / 72).to_pil().convert("L"))
                for page in pypdfium2.PdfDocument(str(path))]
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot read image {path}")
    return [image]


def synthetic_specs(per_type: int, seed: int = 5000, tampers_per_type: int = 2,
                    unknown_per_kind: int | None = None, rough_per_type: int = 0) -> list[dict]:
    """Clean captures per type, tampered variants, out-of-scope documents and (optionally)
    poor captures of each type and unknown kind."""
    specs = []
    for kind in (*BUILDERS, *UNKNOWN_KINDS):
        for i in range(rough_per_type):
            specs.append({"source": "synthetic", "kind": kind, "seed": seed + 900 + i,
                          "rough": True})
    for doc_type in BUILDERS:
        for i in range(per_type):
            specs.append({"source": "synthetic", "kind": doc_type, "seed": seed + i})
        for tamper in TAMPERS:
            for i in range(tampers_per_type):
                specs.append({"source": "synthetic", "kind": doc_type, "seed": seed + 500 + i,
                              "tamper": tamper})
    for kind in UNKNOWN_KINDS:
        for i in range(per_type if unknown_per_kind is None else unknown_per_kind):
            specs.append({"source": "synthetic", "kind": kind, "seed": seed + i})
    return specs


def folder_specs(directory: Path) -> list[dict]:
    specs = []
    for truth in (*DOC_TYPES, UNKNOWN):
        for path in sorted((directory / truth).glob("*")):
            if path.suffix.lower() in IMAGE_SUFFIXES | {".pdf"}:
                specs.append({"source": "file", "path": str(path), "truth": truth})
    for code_dir in sorted((directory / "tampered").glob("*")):
        for truth in DOC_TYPES:
            for path in sorted((code_dir / truth).glob("*")):
                if path.suffix.lower() in IMAGE_SUFFIXES | {".pdf"}:
                    specs.append({"source": "file", "path": str(path), "truth": truth,
                                  "expected": code_dir.name})
    return specs


def _pages(spec: dict) -> list[tuple[str, np.ndarray, str, str | None, str | None, dict]]:
    """[(name, image, truth, tamper, expected indicator, printed values if known)]"""
    if spec["source"] == "synthetic":
        sample = render(spec["kind"], spec["seed"], spec.get("tamper"),
                        rough=spec.get("rough", False))
        name = f"{spec['kind']}-{spec['seed']}" + (f"-{spec['tamper']}" if spec.get("tamper")
                                                   else "") + ("-rough" if spec.get("rough")
                                                               else "")
        return [(name, sample.image, sample.doc_type, sample.tamper, sample.expected_indicator,
                 sample.values)]
    path = Path(spec["path"])
    images = load_images(path)
    return [(f"{path.name}#{i + 1}" if len(images) > 1 else path.name, image, spec["truth"],
             spec.get("expected"), spec.get("expected"), {}) for i, image in enumerate(images)]


def run_spec(spec: dict, store_root: str | None) -> list[dict]:
    store = ReferenceStore(store_root) if store_root else False
    out = []
    for name, image, truth, tamper, expected, printed in _pages(spec):
        page = Page(image)
        start = time.perf_counter()
        result = screen_page(page, store=store)
        elapsed = (time.perf_counter() - start) * 1000
        raw, mode = page.cache.get("classify_raw", ({}, "ocr"))
        row = {"name": name, "truth": truth, "tamper": tamper, "expected": expected,
               "rough": name.endswith("-rough"),
               "predicted": result["doc_type"]["predicted"],
               "confidence": result["doc_type"]["confidence"], "raw": raw, "mode": mode,
               "indicators": [(i["code"], i["severity"]) for i in result["indicators"]],
               "checks": result.get("checks"), "similarity": result.get("similarity"),
               "ms": round(elapsed), "ocr_ms": result["timings_ms"]["screening_ocr"]}
        if printed and not tamper and "checks" in result:
            # synthetic only: how many extracted values equal what was printed (the values
            # themselves are not kept in the report)
            read = result.get("fields", {})
            row["fields_exact"] = [sum(read.get(k, {}).get("value") == v
                                       for k, v in printed.items()), len(printed)]
        if expected and truth in DOC_TYPES:
            # indicator detection on its own: screened as the true type even if the
            # classifier was fooled by the alteration
            as_true = screen(page, truth, store=store or None)
            row["expected_severity"] = max((i["severity"] for i in as_true["indicators"]
                                            if i["code"] == expected), default=0.0)
            if expected == "seal_logo_not_found" and as_true["checks"]["seal_logo_score"] is None:
                row["expected_severity"] = None  # no seal templates: not measured
        out.append(row)
    return out


def run(specs: list[dict], store_root: str | None = None, jobs: int = 1) -> list[dict]:
    if jobs <= 1:
        return [row for spec in specs for row in run_spec(spec, store_root)]
    # Tesseract is multi-threaded (OpenMP); one thread per worker process avoids
    # oversubscription. Per-page times from parallel runs are not station timings.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_spec, spec, store_root) for spec in specs]
        return [row for f in futures for row in f.result()]


def summarize(rows: list[dict]) -> dict:
    rough = [r for r in rows if r.get("rough")]
    rows = [r for r in rows if not r.get("rough")]
    clean = [r for r in rows if not r["expected"]]
    in_scope = [r for r in clean if r["truth"] != UNKNOWN]
    unknown = [r for r in clean if r["truth"] == UNKNOWN]
    confusion: dict[str, dict[str, int]] = {}
    for r in clean:
        row = confusion.setdefault(r["truth"], {})
        row[r["predicted"]] = row.get(r["predicted"], 0) + 1
    false_flags = [{"name": r["name"], "codes": sorted({c for c, s in r["indicators"]
                                                        if s >= SEVERITY_LINE})}
                   for r in in_scope if any(s >= SEVERITY_LINE for _, s in r["indicators"])]
    tampered = [r for r in rows if r["expected"]]
    detection: dict[str, list[int]] = {}
    for r in tampered:
        key = r["tamper"] or r["expected"]
        detection.setdefault(key, [0, 0])
        if r.get("expected_severity") is None:
            continue
        detection[key][0] += int(r["expected_severity"] >= SEVERITY_LINE)
        detection[key][1] += 1
    tampered_types = [r for r in tampered if r["truth"] in DOC_TYPES]
    ms = np.array([r["ms"] for r in rows]) if rows else np.zeros(1)
    ocr = np.array([r["ocr_ms"] for r in rows]) if rows else np.zeros(1)

    def share(hits: int, total: int) -> str:
        return f"{hits}/{total} ({100 * hits / total:.1f}%)" if total else "n/a"

    exact = [r["fields_exact"] for r in rows if r.get("fields_exact")]
    rough_in = [r for r in rough if r["truth"] != UNKNOWN]
    rough_out = [r for r in rough if r["truth"] == UNKNOWN]
    return {
        "pages": len(rows) + len(rough),
        "type_accuracy_clean": share(sum(r["predicted"] == r["truth"] for r in in_scope),
                                     len(in_scope)),
        "unknown_rejected": share(sum(r["predicted"] == UNKNOWN for r in unknown), len(unknown)),
        "type_accuracy_tampered": share(sum(r["predicted"] == r["truth"] for r in tampered_types),
                                        len(tampered_types)),
        "rough_type_accuracy": share(sum(r["predicted"] == r["truth"] for r in rough_in),
                                     len(rough_in)),
        "rough_unknown_rejected": share(sum(r["predicted"] == UNKNOWN for r in rough_out),
                                        len(rough_out)),
        "rough_pages_with_indicator_ge_0.5": share(
            sum(any(s >= SEVERITY_LINE for _, s in r["indicators"]) for r in rough_in),
            len(rough_in)),
        "clean_pages_with_indicator_ge_0.5": share(len(false_flags), len(in_scope)),
        "field_values_exact_clean": share(sum(e[0] for e in exact), sum(e[1] for e in exact)),
        "false_flags": false_flags,
        "tamper_detected_ge_0.5": {k: share(*v) if v[1] else "not measured (no templates)"
                                   for k, v in sorted(detection.items())},
        "confusion": confusion,
        "lowest_correct_confidence": min((r["confidence"] for r in in_scope
                                          if r["predicted"] == r["truth"]), default=None),
        "highest_unknown_type_score": max((max(r["raw"].values()) for r in unknown if r["raw"]),
                                          default=None),
        "ms_per_page": {"p50": int(np.percentile(ms, 50)), "p95": int(np.percentile(ms, 95)),
                        "max": int(ms.max())},
        "ocr_ms_per_page": {"p50": int(np.percentile(ocr, 50)), "max": int(ocr.max())},
    }


def calibrate(rows: list[dict]) -> dict[str, dict]:
    fitted = {}
    for mode in ("ocr", "ocr+refs"):
        samples = [(r["raw"], r["truth"]) for r in rows if r["mode"] == mode and r["raw"]]
        if len(samples) >= 10:
            fitted[mode] = fit_calibration(samples)
    return fitted


def _specs(args) -> list[dict]:
    specs = []
    if args.synthetic:
        specs += synthetic_specs(args.synthetic, args.seed,
                                 tampers_per_type=args.tampers_per_type,
                                 rough_per_type=args.rough)
    if args.dir:
        specs += folder_specs(Path(args.dir))
    for extra in args.extra_unknown or []:
        specs.append({"source": "file", "path": extra, "truth": UNKNOWN})
    if not specs:
        raise SystemExit("nothing to evaluate: pass --synthetic N and/or --dir")
    return specs


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--synthetic", type=int, default=0, help="synthetic pages per type")
    parser.add_argument("--tampers-per-type", type=int, default=2)
    parser.add_argument("--rough", type=int, default=0,
                        help="poor-capture synthetic pages per type and unknown kind")
    parser.add_argument("--seed", type=int, default=5000)
    parser.add_argument("--dir", help="labelled folder (see module docstring)")
    parser.add_argument("--extra-unknown", nargs="*", help="more out-of-scope images/PDFs")
    parser.add_argument("--refs", help="reference store (default: none)")
    parser.add_argument("--jobs", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 1)))
    return parser


def main_eval(argv: list[str] | None = None) -> int:
    parser = _parser("scan_agent screening-eval")
    parser.add_argument("--report", type=Path, help="also write the per-page rows as JSON")
    args = parser.parse_args(argv)
    rows = run(_specs(args), args.refs, args.jobs)
    print(json.dumps(summarize(rows), indent=1))
    if args.report:
        args.report.write_text(json.dumps(rows, indent=1), encoding="utf-8", newline="")
    return 0


def main_calibrate(argv: list[str] | None = None) -> int:
    parser = _parser("scan_agent screening-calibrate")
    parser.add_argument("--out", type=Path,
                        help="calibration file (default: <refs>/calibration.json)")
    args = parser.parse_args(argv)
    out = args.out or (Path(args.refs) / "calibration.json" if args.refs else None)
    if out is None:
        parser.error("--out or --refs is required")
    rows = run(_specs(args), args.refs, args.jobs)
    fitted = calibrate(rows)
    if not fitted:
        raise SystemExit("not enough labelled pages to fit (need 10+)")
    write_calibration(out, fitted, f"fitted on {len(rows)} page(s) {time.strftime('%Y-%m-%d')}")
    print(json.dumps({"written": str(out), **fitted}, indent=1))
    return 0
