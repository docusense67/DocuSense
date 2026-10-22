"""Screening commands, dispatched from `python -m scan_agent <command>`:

    screen <image|pdf>...                  print the screening JSON (contract schema 1)
    screening-enroll --type <t> <images|folders>...   add reference samples
    screening-eval / screening-calibrate   see scan_agent/screening/evaluate.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import resolve_store, screen_page
from .evaluate import IMAGE_SUFFIXES, load_images, main_calibrate, main_eval
from .profile import DOC_TYPES
from .references import ReferenceStore, default_root, enroll
from .synthetic import render

COMMANDS = ("screen", "screening-enroll", "screening-eval", "screening-calibrate")


def _inputs(paths: list[str]) -> list[Path]:
    files = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files += sorted(p for p in path.iterdir()
                            if p.suffix.lower() in IMAGE_SUFFIXES | {".pdf"})
        elif path.exists():
            files.append(path)
        else:
            raise SystemExit(f"not found: {path}")
    return files


def cmd_screen(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="scan_agent screen")
    parser.add_argument("images", nargs="+")
    parser.add_argument("--refs", help=f"reference store (default: {default_root()} if present)")
    parser.add_argument("--no-refs", action="store_true", help="ignore reference samples")
    args = parser.parse_args(argv)
    store = False if args.no_refs else (args.refs or None)
    results = []
    for path in _inputs(args.images):
        for image in load_images(path):
            results.append(screen_page(image, store=store))
    print(json.dumps(results[0] if len(results) == 1 else results, indent=1))
    return 0


def cmd_enroll(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_agent screening-enroll",
        description="Add reference samples of one document type to the reference store.")
    parser.add_argument("--type", required=True, choices=DOC_TYPES, dest="doc_type")
    parser.add_argument("images", nargs="*", help="page images / PDFs, or folders of them")
    parser.add_argument("--refs", help=f"reference store (default: {default_root()})")
    parser.add_argument("--no-images", action="store_true",
                        help="keep only features, not the (value-masked) sample images")
    parser.add_argument("--no-seal-templates", action="store_true",
                        help="do not cut missing seal/logo templates from the samples")
    parser.add_argument("--clear", action="store_true",
                        help="remove this type's references (and templates) first")
    parser.add_argument("--synthetic", type=int, default=0, metavar="N",
                        help="bootstrap: enroll N synthetic SAMPLE pages (until real samples "
                             "arrive; replace them with --clear)")
    args = parser.parse_args(argv)
    store = resolve_store(args.refs) if args.refs else ReferenceStore(default_root())
    assert store is not None
    if args.clear:
        shutil.rmtree(store.type_dir(args.doc_type), ignore_errors=True)
    images = [image for path in _inputs(args.images) for image in load_images(path)]
    images += [render(args.doc_type, 90000 + i).image for i in range(args.synthetic)]
    if not images:
        parser.error("no images given")
    reports = enroll(store, args.doc_type, images, keep_images=not args.no_images,
                     extract_seals=not args.no_seal_templates)
    for report in reports:
        print(json.dumps(report))
    print(f"{store.count(args.doc_type)} reference(s) for {args.doc_type} in {store.root}")
    return 0


def main(argv: list[str]) -> int:
    command, rest = argv[0], argv[1:]
    if command == "screen":
        return cmd_screen(rest)
    if command == "screening-enroll":
        return cmd_enroll(rest)
    if command == "screening-eval":
        return main_eval(rest)
    if command == "screening-calibrate":
        return main_calibrate(rest)
    raise SystemExit(f"unknown command {command!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
