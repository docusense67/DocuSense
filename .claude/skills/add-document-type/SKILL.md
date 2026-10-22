---
name: add-document-type
description: Add a new issuable academic document type (e.g. COG, COR, Diploma) to the generation engine - enum, context builder, registry entry, default template, OCR zones, tests. Use when asked to add or support a new document type, or when a client change request introduces one.
---

# Add a document type

New types are additions to a registry, not rewrites. Read `docs/template-authoring.md` first. Use `backend/app/documents/registry.py` (TOR, COE) as the reference.

## 0. Scope check (do not skip)

Only TOR and COE are in MVP scope (spec Section 19). Any other type is a change request. Before writing code, confirm that the user has an approved change request for it, or that they explicitly want to proceed anyway. Note the decision in your reply.

## 1. Gather the facts

Ask for, or find, each of these. Don't guess.
- The official layout (the school's Word/PDF sample) and every printed field.
- Where each value comes from in the data model (`backend/app/models/`). If a field has no source (e.g. fees for COR), stop and report the schema gap.
- Eligibility rules for requesting it. These belong in the request flow, not here.

## 2. Implement

1. `backend/app/models/enums.py`: add the value to `DocumentType` (stored as a string, so no migration is needed).
2. `backend/app/documents/context.py`: add `build_<type>(db, student, opts) -> dict`.
   - Return **printed strings only**. Reuse `format_name`, `format_date`, `format_units`, `ordinal_year`, `academic_history`.
   - Collect every problem into a list and raise `MissingDataError(missing)` once.
3. `backend/app/documents/defaults/<type>.html`: an OCR-friendly layout.
   - Fixed-mm header block with a value row per scanned field.
   - `<img class="qr" src="{{ qr_code }}">` top-right.
   - Sans-serif, at least 10 pt.
   - Don't set `@page`; the system stylesheet owns size, margins and the footer.
4. `backend/app/documents/defaults/__init__.py`: add `<TYPE>_ZONES`. Zone names equal the snapshot keys; use `_rows([...])` for header rows.
5. `backend/app/documents/registry.py`: add placeholder names to `PLACEHOLDER_CATALOG` if new, add a sample-context function, and add a `DocumentTypeSpec` to `REGISTRY`.
6. `backend/app/seed.py` picks the new type up automatically through `REGISTRY`.

## 3. Verify

- Add builder tests in `backend/tests/test_documents.py`: happy path with exact strings, and a missing-data case that lists every problem.
- Run `cd backend && .venv/Scripts/python -m pytest -q` and `ruff check .`.
- `test_default_templates_are_valid_and_use_only_catalog_names` must pass for the new type.
- Push, then download the CI `sample-pdfs` artifact. Add the new type to `app/documents/samples.py` and **look at the rendered PDF**: fields inside their zones, QR top-right, footer present.
- Update `docs/template-authoring.md` if you added placeholders.
