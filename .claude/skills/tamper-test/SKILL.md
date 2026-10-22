---
name: tamper-test
description: Build and run the tamper test set (edited PDFs, overprinted or taped grades, fake document numbers, missing pages, revoked documents) and prove that no altered document is ever marked VALID. Use for the Epic 8 tamper test, before a demo, or after any change to OCR or validation rules.
---

# Tamper test: zero false VALIDs

Acceptance criterion (Section 21): altered critical fields must never show VALID. INVALID is the goal; NEEDS_REVIEW is acceptable when the alteration is physically unclear. VALID is a failure, always.

## 1. Automated guard (always run first)

```bash
cd backend
.venv/Scripts/python -m pytest -q tests/test_validation_rules.py tests/test_e2e_scan.py
```

- `test_altered_critical_field_is_never_valid` alters every critical field at high and low OCR confidence.
- `test_bulk_stack_genuine_tampered_fake` runs real images through the agent and the API.

Both must pass. If you change rules, add the new case here.

## 2. Physical set (with the station, Section 23 step 12)

Issue genuine documents through the normal flow (request, approve, print). Then build at least these variants:

| # | Variant | Expected |
| --- | --- | --- |
| 1 | Genuine print | VALID |
| 2 | Edited PDF (one grade changed) printed | INVALID |
| 3 | Correction tape + handwritten grade | INVALID or NEEDS_REVIEW |
| 4 | Overprinted grade (sticker or reprint over the cell) | INVALID or NEEDS_REVIEW |
| 5 | Changed name or student number | INVALID |
| 6 | Fake TOR with made-up document number | INVALID ("not issued") |
| 7 | Genuine document but one page removed | INVALID ("page X of Y missing") |
| 8 | Genuine document after registrar revokes it | INVALID ("revoked") |
| 9 | Photocopy of a genuine document | VALID (expected; see Summary H) |
| 10 | Genuine, but a grade was corrected after issue | NEEDS_REVIEW ("record updated since issue") |

Scan them in **Bulk** mode as one stack, plus a few in **Single** mode.

## 3. Record and report

- Get the session's batch table: `GET /api/scan-sessions/{id}`.
- For each row, record variant, expected result, actual result, and the top reason.
- **Any VALID on variants 2 to 8 is a release blocker.** Capture the scan images (`/api/validations/{id}/pages/1`), find whether extraction (agent) or rules (backend) let it through, and fix it with a regression test before continuing.
- Report: totals per variant, false VALIDs (must be 0), and NEEDS_REVIEW cases that a registrar resolved.
