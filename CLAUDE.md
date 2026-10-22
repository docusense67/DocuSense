# CLAUDE.md

Academic Document Generation & Validation System. Scope is fixed by `docs/MVP-Specification.md`; section numbers below refer to it. Anything not in Section 19's MVP column is a change request, not a feature to add.

## Commands

```bash
cd backend && .venv/Scripts/python -m pytest -q      # tests (SQLite in-memory)
cd backend && .venv/Scripts/ruff check .              # lint
cd backend && .venv/Scripts/alembic revision --autogenerate -m "..."   # set ADGVS_DATABASE_URL first
cd frontend && npm run dev | npm run lint | npm run build
```

## Rules that must not regress

- RBAC is enforced server-side on every route via `require_roles` / `require_validator` in `backend/app/api/deps.py`. Students only ever see their own records; teachers only their sections.
- Every action that changes or checks an official record calls `services.audit.record(...)` in the same transaction. `audit_logs` is append-only (DB trigger on Postgres).
- `generated_documents.data_snapshot` is never updated after approval. A regeneration creates a new document number.
- QR codes carry only a random token, never personal data.
- VALID means "matches official records", never "authentic" (Section 15 wording).
- Validation decision rules live in the backend as pure, unit-tested functions. The scan agent only extracts fields + confidences.
- Registrar actions that change official records require a `reason`.

## Conventions

- JSON columns use `JSONType` from `app/db.py` (JSONB on Postgres, JSON on SQLite).
- Enums are `StrEnum` in `app/models/enums.py`, stored with `native_enum=False`.
- Test emails use a real-looking domain (`school.edu.ph`); `email-validator` rejects `.test`.
- Scanner capture code lives in `scan-agent/scan_agent/capture/`.

## Documents (Epic 3)

- Engine lives in `backend/app/documents/`. Each document type is one `DocumentTypeSpec` in `registry.py`. To add a type, use the `/add-document-type` skill.
- Context builders return printed strings only. The snapshot is exactly what was printed, and its SHA-256 is stored and partly printed in the footer.
- WeasyPrint can't run on the Windows dev machine (no GTK). Rendering tests skip locally; CI renders them and uploads `sample-pdfs`. Check visual changes there.
- Template authoring and OCR zone rules: `docs/template-authoring.md`.

## Scanning and validation (Epic 5)

- Scan agent (`scan-agent/`) extracts only; the backend decides (`backend/app/services/validation.py`). Never put decision logic in the agent.
- Station lookups return zones and page counts, never student data.
- Table OCR requires rule removal (`cleanup.remove_rules`). Without it Tesseract silently drops the bordered Units/Grade columns.
- Tesseract is installed locally (`C:\Program Files\Tesseract-OCR`); CI installs it too, and CI fails on any skipped test.
- Skills: `/ocr-eval` (accuracy vs. 98% target), `/tamper-test` (zero false VALIDs), `/add-document-type`.
- Writing files from Python on Windows: pass `encoding="utf-8"`. Edit regex-heavy code with the Edit tool, not shell heredocs (escapes get mangled).

## Deployment and security (Epic 8)

- Production is `docker-compose.yml` + `deploy/` (Nginx, backup scripts). Admin steps: `docs/admin-installation-guide.md`. CI job `deploy` builds and exercises the whole stack, including a ZAP baseline scan that fails on any High alert.
- The API connects with a least-privilege login (`python -m app.manage setup-db-roles`, run by the `migrate` service). Never give the app the owner's credentials.
- `tests/test_access_matrix.py` pins which routes each role can reach. A new route fails it until you add it to the right set: decide who may call it, don't just append.
- Validation identity is decided by the backend (`find_document` returns how the document was matched). Never trust agent-reported identity or decisions.
- CSP has no `'unsafe-inline'`: no inline `<style>`/`<script>`, no `style=""` strings in HTML templates served to the browser. React `style={{...}}` props are fine.
- Imports go through `services/tabular.read_table` (size, row and column caps). Don't parse uploads elsewhere.
- Dependency pins: `backend/constraints.txt`, `scan-agent/constraints.txt` (pip-audit in CI). Refresh them deliberately.
- Production station runs `python -m scan_agent ... --watch --delete-after-upload` (`scan_agent/watch.py`). Keep it the only code that deletes scanner files, and never delete before the document upload succeeded.
- Level 2 layout check: the agent measures (QR box, table header box in page mm); `validation.layout_reasons` decides against the printed template version's zones.
- CI job `ocr-accuracy` generates 100 synthetic documents (`backend/scripts/make_ocr_set.py`), simulates scans (`scan_agent.simulate`) and gates clean scans at 98%. Table-cell corrections (`parsing.fix_grade`, `fix_code`) may only restore format, never change printed digits.
- Load test: `deploy/loadtest/` (Locust) runs in the CI `deploy` job; 100 users, every screen's p95 under 2 s.

## Cloud (Epic 9) and the Pi station

- Cloud = Vercel (web, `frontend/vercel.json`, `/api` rewrite) + Render (API, `render.yaml`) + Supabase (DB + private Storage bucket). Guide: `docs/cloud-deployment.md`. The on-prem Docker stack stays supported: every cloud setting defaults to on-prem behaviour.
- Files go through `app/storage.py` (`LocalStorage` / `SupabaseStorage`). Never hand Supabase URLs to browsers; PDFs and page images are served only by our API.
- Supabase lockdown: migration `c4e8a1f2b9d0` enables RLS on every app table and revokes the Data API roles; `setup-db-roles` adds the API login's policies. A new table needs no extra step, but check it in `tests/test_db_cloud.py`.
- `GET /api/internal/cron/daily` (Bearer `ADGVS_CRON_SECRET`, called by Vercel Cron) is in the `INTERNAL` set of `test_access_matrix.py`: no user or station may reach it.
- `client_ip()` reads X-Forwarded-For only when `ADGVS_TRUSTED_PROXY_HOPS` > 0. Requests that hit onrender.com directly can spoof it; don't use audit IPs as proof.
- Pi station: `--source sane --watch` (`scan_agent/station.py`) captures from SANE and spools pages into the watched folder. It never deletes files; `watch.py` stays the only deleter. The station talks to the Render URL directly (scan uploads are too large for the Vercel proxy). Setup: `deploy/station/`, `docs/hardware/README.md`.
- E2E: `cd frontend && npm run e2e` (Playwright starts `dev_server.py` + Vite); `npm run e2e:tft` checks the operator screen at `TFT_WIDTH`x`TFT_HEIGHT`.

## Incoming-document screening (Part I, pre-approval wave)

- Contract: `docs/intake/screening-contract.md` (read its backend notes). Not merged to `main` until the client signs Annex A.
- The station measures; `services/intake.decide` (pure, unit-tested in `tests/test_intake_decision.py`) alone decides `passed_screening` (Tray 1) / `manual_review` (Tray 2). Every rule has a reason code with plain-language text in `REASON_TEXT`; add both together. Malformed results are a 422, never a pass.
- Wording: "passed screening" / "needs manual review". Never "authentic", "genuine" or "verified" in code, UI or docs.
- Station routes (`/api/station/intake/...`) use the station key; the queue and review are registrar + admin (no validation permission needed); thresholds are admin-only. Reject, request resubmission and accepting a flagged document need a reason. Audit entries never carry extracted field values.
- A routing mismatch or jam reported by the gate always turns the document into `manual_review`.
- The operator console (`app/api/intake_operator.py`, `/intake-station`) is validator-only and never returns extracted field values, indicators or images: operators see type, decision, tray and plain-language reasons only. Field review and student linking stay with registrar/admin.
- An `unknown` page may arrive without `checks` (no profile to check against); any known type without `checks` is a 422.
- Student data (S-06/S-07, contract section "Student data"): field schemas, validation and matching are pure functions in `services/intake_fields.py`; routes in `api/intake_profiles.py`. The reviewed set never overwrites `screening_result`. Candidates are suggestions only: a link is always a staff action, and a non-strong match needs a reason. Profiles created from a document go through `students.add_student` (master-list rules). Only passed or accepted documents get there (rejected / resubmission -> 409).
