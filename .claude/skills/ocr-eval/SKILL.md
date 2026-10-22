---
name: ocr-eval
description: Measure scan-agent OCR accuracy on a set of scanned documents against their issued snapshots, report field-level accuracy versus the spec's 98% target, and diagnose misses (zones, preprocessing, character confusions). Use for the Epic 8 OCR accuracy run, after a template or zone change, or when scans come back NEEDS_REVIEW too often.
---

# OCR accuracy evaluation

Spec target (Section 21): at least 98% field-level accuracy on 100+ clean 300 DPI scans of system-printed documents.

## 1. Collect inputs

- **Scans**: one folder of page images or scanner PDFs, named in stack order. Real scans from the station are the point. Rendered PDFs (CI `sample-pdfs`) only smoke-test the pipeline.
- **Expected values**: one `<DOCUMENT_NUMBER>.json` per issued document with `{"snapshot": {...}}`. Get snapshots from `GET /api/documents/{id}` (registrar) as `data_snapshot`, or reuse the CI sample JSONs.
- If the documents use a template other than the defaults, check that `scan_agent/evaluate.py` loads matching zones. It reads `backend/app/documents/defaults`; for a new template version, point it at that version's `field_zones`.

## 2. Run

```bash
cd scan-agent
.venv/Scripts/python -m scan_agent.evaluate --scans <scans> --expected <expected> --report ocr-accuracy.csv
```

It exits non-zero below 98% and prints every miss.

## 3. Diagnose misses, by symptom

| Symptom | Likely cause | Where to fix |
| --- | --- | --- |
| A whole field empty | Zone misaligned on real scans (paper offset, scanner scaling) | Crop the zone from a normalized page (`processing/cleanup.crop_mm`) and look at it. Adjust zones in the template version, not in code |
| Table rows missing Units/Grade | Table rules not removed (rules thicker or fainter on real scans) | `cleanup.remove_rules`: tune `min_len_px`, blur, dilation |
| `l`/`1`, `O`/`0` confusions | Expected on some fonts | Add a format-based rule in `processing/ocr.correct` or a whitelist in `FIELD_WHITELIST`. Only when the field's format makes it unambiguous |
| Stray `|`, `~` tokens | Rule fragments | `parsing.clean_line` |
| Low confidence but correct | Faint toner or 200 DPI scans | Check scanner settings (300 DPI grayscale) before touching code |
| Document not split correctly | Footer unreadable | Check the `FOOTER_ZONE` crop |

## 4. Report

Summarize for the team: documents, fields, accuracy %, misses grouped by cause, and what changed. Never loosen backend validation thresholds (`ADGVS_OCR_CONFIDENCE_THRESHOLD`, name similarity) to raise the number. Fix extraction instead. Add any new failure pattern as a test in `scan-agent/tests/`.
