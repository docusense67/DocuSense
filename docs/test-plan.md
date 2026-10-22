# Test plan and results

Academic Document Generation & Validation System. Version of 2026-10-05 (main at commit `4469041`).

This is the test deliverable from MVP specification Section 26 ("Test plan and results for the 7 use cases"). It covers:

- the seven end-to-end use cases (Section 22), which are the acceptance script;
- the non-functional targets (Section 21);
- the OCR accuracy run and the tamper test set;
- the security scan and the backup restore test.

The registrar signs off on the results after the manual acceptance run.

## 1. Automated test results

| Suite | Command | Result on 2026-10-05 (Windows dev machine) |
| --- | --- | --- |
| Backend API, rules, generation | `cd backend && .venv/Scripts/python -m pytest -q` | 190 passed, 1 skipped (see below) |
| Scan agent (image clean-up, QR, OCR, splitting) | `cd scan-agent && .venv/Scripts/python -m pytest -q` | 17 passed, 0 skipped |

- **Skipped locally, run in CI.** `test_documents.py::test_real_render_tor_and_single_page_coe` needs WeasyPrint's GTK/Pango libraries, which the Windows dev machine does not have. CI on Ubuntu installs them, renders real TOR/COE PDFs, uploads them as the `sample-pdfs` artifact, and **fails the build if any test is skipped** (`.github/workflows/ci.yml`).
- **OCR in the automated tests.** The scan-agent tests and `test_e2e_scan.py` run real Tesseract OCR on rendered document images. That proves the pipeline works. It does not stand in for real scans from the station. That is the job of the OCR accuracy run in Section 5.
- **CI also checks:** ruff lint on backend and scan agent; `alembic upgrade head && alembic downgrade base` on a real PostgreSQL 16; `pip-audit` on the pinned backend and scan-agent dependencies and `npm audit` (high) on the frontend; frontend lint and build.
- **CI `deploy` job (full production stack in Docker):** builds and starts every service; checks HTTPS, the HTTP→HTTPS redirect and security headers (HSTS, CSP, nosniff), and that `/api/docs` is off; runs the go-live commands and a first admin login; checks that the API's database login is refused UPDATE/DELETE/TRUNCATE on `audit_logs` and DROP/CREATE TABLE; runs UC1–UC3 inside the stack (request, approval, real WeasyPrint render, verification, protected download); takes a backup and runs the restore check; runs an OWASP ZAP baseline scan and fails on any High alert (`.github/workflows/ci.yml`).

Test files: `backend/tests/*.py`, `scan-agent/tests/*.py`. Tests below are cited as `file::test_name`.

### 1.1 End-to-end browser tests (Playwright)

Real browser (Chromium) against the real API: Playwright starts `backend/scripts/dev_server.py` (API on :8000, a fresh throwaway SQLite DB and storage dir per run, demo accounts, Maria's sample TOR pre-issued, CI sample PDFs instead of WeasyPrint) and `npm run dev` (Vite on :5173, proxying `/api`). Locally an API or Vite server already running on those ports is reused; CI always starts fresh ones. Config: `frontend/playwright.config.ts`; tests: `frontend/e2e/*.spec.ts`. Every test sets up its own data through the UI or the API (no ordering between tests); the two request tests each own one student (Maria: TOR, Ana: COE) because the backend allows one open request per student and type.

| Command (in `frontend/`) | What it runs |
| --- | --- |
| `npm run e2e` | Everything: desktop Chromium project + TFT project (CI job `e2e`) |
| `npm run e2e:tft` | Only tests tagged `@tft`, at the scan station TFT size |
| `npm run e2e:ui` | Playwright UI mode, for writing and debugging tests |

First time on a PC: backend venv as in the README, then `npm ci && npx playwright install chromium`. The backend interpreter is `backend/.venv` (Windows or POSIX layout) or `E2E_PYTHON`.

| Spec | Covers |
| --- | --- |
| `auth.spec.ts` | Sign in through the form per role (admin, registrar, teacher, student) lands on the right dashboard; sign out ends the session server-side; generic error for wrong credentials; a student opening registrar pages is sent home and gets 403 from registrar APIs; only the validation operator sees the scan station. Smoke only: the full matrix is `test_access_matrix.py`. |
| `request-flow.spec.ts` | UC1–UC3 in the browser, approve-first order (S-11): Maria requests a TOR (**Pending**, no document yet) → registrar approves it from the dashboard approval queue (document generated, **Ready for verification**; Maria sees **Approved**) → registrar verifies it from the verification queue (auto-release) → Maria sees **Completed**, clicks Download and the protected link returns a PDF (`%PDF-`). Ana: TOR shown as not available (INC grade), COE approved and verified from the request list, then downloaded. Juan: COE rejected while pending (reason required, nothing generated), Maria-style row shows **Rejected** with the reason. |
| `operator.spec.ts` | UC4–UC7 dashboard side: the registrar (validation operator) picks a station, starts a **Single** session and sees the tray instruction; the station is simulated through its API (`X-Station-Key`, payloads as in `test_scanning.py`) and the dashboard shows **VALID / INVALID / NEEDS REVIEW** rows, totals, the review prompt and the Section 15 wording. Each test registers its own station. Real scanning/OCR is not in the browser tests (covered by `test_e2e_scan.py` and the OCR run). |
| `a11y.spec.ts` | axe-core on the login page, student dashboard and registrar dashboard; fails only on **critical** violations (others are attached to the report as annotations). |

**TFT viewport.** The `tft` project runs the `@tft` tests (operator dashboard: no horizontal page overflow, tray instruction visible, the large result badge fully inside the viewport) at `TFT_WIDTH` × `TFT_HEIGHT` CSS pixels, default 800×480. When the real panel is known, either run `TFT_WIDTH=480 TFT_HEIGHT=320 npm run e2e:tft` or change the defaults at the top of `frontend/playwright.config.ts` (CI uses the defaults). Use the panel's CSS-pixel size: physical resolution divided by the browser's device scale factor on the station. Tag a test `{ tag: '@tft' }` to run it at that size too.

**Reports.** `frontend/playwright-report/` (HTML) and `frontend/test-results/` (traces and screenshots of failures); CI uploads both as the `playwright-report` artifact when the job fails.

## 2. How to read the use-case sections

Each step is tagged with what it needs:

| Tag | Meaning |
| --- | --- |
| **[Now]** | Can be accepted now in the web app on any PC, with the seeded demo data. No scanner needed. |
| **[File-drop]** | Can be shown now with the scan agent's file-drop mode, using scanned or photographed images in a folder. Final acceptance still needs the station. |
| **[Station]** | Needs the physical scan station: ADF scanner, mini PC and output trays. Not acceptable until the station is installed. |

**Demo data:** run `python -m app.seed` to create one program (BSIT), the teacher, registrar and admin accounts, and students including Maria Santos (`2025-00001`). Every demo account must change its password at first sign-in.

**Manual acceptance:** the UAT tester fills in the result table on the school's server, with real hardware where tagged. Write Pass or Fail, the date, the tester's name, and remarks for anything unexpected.

---

## 3. Use cases (Section 22)

### UC1: Student requests a TOR

**Preconditions:** student account active; all grades for completed terms encoded and the terms locked; an active TOR template exists.

| # | Step | Expected result | Needs |
| --- | --- | --- | --- |
| 1 | Student signs in and opens "New request". | Only document types the student is eligible for can be chosen. Reasons are shown for the others. | [Now] |
| 2 | Choose TOR, purpose "Employment", 1 copy. Submit. | Request created with a reference number `REQ-YYYY-NNNNNN`. | [Now] |
| 3 | System runs the eligibility check. | Passes for a student with all grades locked and no INC. | [Now] |
| 4 | System generates the TOR. | PDF with document number `TOR-YYYY-NNNNNN`, QR code, and a footer `number \| Page X of Y \| Ref …` on every page. Snapshot and hash are saved. | [Now] |
| 5 | Student checks status. | Status **Pending** (registrar approval first, S-11). Reference number visible. | [Now] |
| A1 | Alternative: student with an INC grade requests a TOR. | Request is **Rejected** automatically: "Not eligible: Incomplete grade in [subject] (…)". | [Now] |
| A2 | Alternative: submit a second TOR request while one is pending. | Blocked: "A TOR request is already pending (REQ-…)". | [Now] |

**Automated coverage**

- `backend/tests/test_requests.py::test_uc1_eligible_request_waits_for_registrar_approval`
- `backend/tests/test_requests.py::test_registrar_approval_runs_retrieval_and_generation`
- `backend/tests/test_requests.py::test_uc1_inc_grade_rejects_with_reason`
- `backend/tests/test_requests.py::test_duplicate_pending_request_blocked`
- `backend/tests/test_requests.py::test_students_only_request_for_themselves`
- `backend/tests/test_requests.py::test_students_see_only_their_requests`
- `backend/tests/test_requests.py::test_registrar_files_walk_in_request`
- `backend/tests/test_requests.py::test_generation_blocked_then_regenerated`
- `backend/tests/test_requests.py::test_payment_required_mode`
- `backend/tests/test_requests.py::test_coe_requires_active_enrolled_student`
- `backend/tests/test_dashboard.py::test_student_eligibility`
- `backend/tests/test_access_matrix.py::test_routes_each_role_can_reach_are_pinned` (students reach only their own request routes)
- `backend/tests/test_documents.py::test_tor_context_is_all_printed_strings`
- `backend/tests/test_documents.py::test_tor_requires_a_locked_term`
- `backend/tests/test_documents.py::test_tor_context_keeps_inc_and_excludes_it_from_gwa`
- `backend/tests/test_documents.py::test_generate_tor_freezes_snapshot_and_writes_pdf`
- `backend/tests/test_documents.py::test_document_numbers_increment_per_type`
- `backend/tests/test_documents.py::test_generation_fails_cleanly_without_active_template`
- `backend/tests/test_documents.py::test_generation_writes_nothing_when_data_missing`
- `backend/tests/test_documents.py::test_hash_is_independent_of_key_order`
- `backend/tests/test_documents.py::test_qr_is_svg_data_uri_and_payload_has_no_personal_data`
- `backend/tests/test_documents.py::test_real_render_tor_and_single_page_coe` (CI only)
- `backend/tests/test_grading.py::test_gwa_is_units_weighted_and_rounded_half_up`, `::test_gwa_excludes_inc_and_drp`, `::test_gwa_none_when_no_numeric_grades`
- Preconditions (grades and locking): `backend/tests/test_records.py::test_teacher_encodes_grade_in_open_term`, `::test_submit_and_lock_term`, `::test_lock_blocks_when_grades_missing_unless_forced`, `::test_locked_term_blocks_direct_edits`

**Manual acceptance**

| Step | Result (Pass/Fail) | Date | Tester | Remarks |
| --- | --- | --- | --- | --- |
| 1–5 main flow | | | | |
| A1 INC grade rejected | | | | |
| A2 duplicate blocked | | | | |

### UC2: Registrar approves and releases a document

**Preconditions:** a request is Pending approval (S-11: the registrar approves the request, then verifies the generated document).

| # | Step | Expected result | Needs |
| --- | --- | --- | --- |
| 1 | Registrar opens the approval queue, selects the TOR and clicks **Approve request**. | Queue is oldest first. Records are retrieved and the PDF generated only now; status **Ready for verification**, student sees **Approved**. | [Now] |
| 2 | Review the PDF next to the source data. | The printed snapshot and today's records are shown side by side. Any difference is flagged. | [Now] |
| 3 | Click **Verify & release**. | Status Verified, then Released (automatic, default `ADGVS_AUTO_RELEASE_ON_APPROVAL=true`); student sees **Completed**. Download window open for 30 days. | [Now] |
| 4 | Check the audit trail. | `request_approved`, `document_generated`, `document_verified` and `document_released` entries, in that order, with the registrar and the time, on the document timeline and in the Audit trail search. | [Now] |
| A1 | Alternative: Reject (before approval or at verification) or **Send back for regeneration** with a reason, correct the record (grade change request if the term is locked), then regenerate. | Reject needs a reason, which the student sees. The old file is voided. Regeneration issues a **new** document number. | [Now] |
| A2 | Revoke a released document, with a reason. | Status Revoked. Future scans are INVALID. | [Now] |

**Note on step 4:** the request review screen shows the per-document timeline: request, every generation, approval, release, downloads, scans and review decisions (`GET /api/documents/{id}`). The **Audit trail** screen searches the whole log by text, action, record and date range (`GET /api/audit-logs`, registrar and admin).

**Automated coverage**

- `backend/tests/test_requests.py::test_uc2_verify_releases_with_30_day_window`
- `backend/tests/test_requests.py::test_manual_release_mode`
- `backend/tests/test_requests.py::test_send_back_for_regeneration_issues_new_number`
- `backend/tests/test_requests.py::test_reject_at_verification_then_regenerate`
- `backend/tests/test_requests.py::test_side_by_side_flags_records_changed_since_generation`
- `backend/tests/test_requests.py::test_cancel_rules`
- `backend/tests/test_requests.py::test_registrar_cancel_needs_a_reason`
- `backend/tests/test_requests.py::test_revoke_released_document`
- `backend/tests/test_requests.py::test_document_timeline`
- `backend/tests/test_audit_search.py::test_search_filters_and_pages`, `::test_students_and_teachers_cannot_read_the_audit_log`
- `backend/tests/test_records.py::test_grade_change_request_flow`, `::test_change_request_only_for_locked_grades`, `::test_reject_keeps_grade`
- `backend/tests/test_dashboard.py::test_registrar_dashboard`

**Manual acceptance**

| Step | Result (Pass/Fail) | Date | Tester | Remarks |
| --- | --- | --- | --- | --- |
| 1–4 main flow | | | | |
| A1 reject, correct, regenerate (new number) | | | | |
| A2 revoke | | | | |

### UC3: Student prints the document

**Preconditions:** document is Released.

| # | Step | Expected result | Needs |
| --- | --- | --- | --- |
| 1 | Student downloads the PDF from the dashboard. | PDF opens. The link is tied to the student and expires in 5 minutes. Other students get "not available". | [Now] |
| 2 | Print at 100% scale on A4. | Printout matches the PDF. QR code and footer are legible. | [Now] (any printer); final check on the school's printer |
| 3 | Check the download history. | `document_downloaded` entry in the audit trail. The student sees the time of their own download on the request. | [Now] |
| A1 | Alternative: download window expired. | Student cannot download. Status Expired. The document still scans VALID. Registrar re-opens the window with a reason. | [Now] |

**Automated coverage**

- `backend/tests/test_requests.py::test_uc3_student_downloads_released_document` (includes the student's own download history)
- `backend/tests/test_requests.py::test_download_denied_before_release_but_registrar_can_review`
- `backend/tests/test_requests.py::test_links_are_user_bound_expiring_and_tamper_proof`
- `backend/tests/test_requests.py::test_expiry_ends_downloads_but_not_validity`

**Manual acceptance**

| Step | Result (Pass/Fail) | Date | Tester | Remarks |
| --- | --- | --- | --- | --- |
| 1–3 main flow | | | | |
| A1 expired window, re-opened | | | | |

### UC4: Operator scans the printed document

**Preconditions:** scan station registered and online. Operator is a registrar with the validation permission. Scanner ready.

| # | Step | Expected result | Needs |
| --- | --- | --- | --- |
| 1 | Operator selects **Single** on the Scan station screen. | Session opens. Screen says to use the **Single** tray. | [Now] (screen); [Station] (tray) |
| 2 | Place the document in the feeder. Scan starts. | The feeder pulls the pages without help. | [Station] |
| 3 | Pages captured at 300 DPI grayscale, cleaned up and processed. | The agent reads the QR code, footer and zones. Images and fields are sent to the server. | [File-drop] now; [Station] for acceptance |
| 4 | Paper exits to the single tray. | | [Station] |
| A1 | Alternative: paper jam or double feed. | Dashboard shows "Paper jam: clear and re-feed" (or double feed). The run resumes from the failed sheet (`--start-sequence`) in the same session. | [Station]. The status display can be shown now. |
| A2 | Alternative: scanner offline. | Dashboard shows "Offline". Starting a session is refused: "Scanner is offline (…). Reconnect it, then start again." | [Station]. The block can be shown now by reporting `offline` from the agent. |

**Automated coverage**

- `backend/tests/test_scanning.py::test_station_finds_its_open_session`
- `backend/tests/test_scanning.py::test_station_lookup_returns_zones_but_no_student_data`
- `backend/tests/test_scanning.py::test_scanner_status_reaches_dashboard`
- `backend/tests/test_scanning.py::test_uc4_offline_scanner_blocks_new_sessions`
- `backend/tests/test_access_matrix.py::test_station_routes_answer_401_without_a_valid_key`
- `backend/tests/test_scanning.py::test_operator_permission_required`
- `backend/tests/test_scanning.py::test_bad_uploads_rejected`
- `backend/tests/test_records.py::test_admin_registers_station_and_key_is_shown_once`
- `scan-agent/tests/test_pipeline.py::test_tor_identity_from_qr_and_footer`
- `scan-agent/tests/test_pipeline.py::test_skewed_noisy_scan_still_reads`
- `scan-agent/tests/test_parsing.py::test_footer_tolerates_separator_misreads`, `::test_footer_unreadable_returns_none`
- `scan-agent/tests/test_filedrop.py::test_pages_come_in_name_order_and_resume_from_a_sequence`, `::test_delete_consumed_removes_only_files_that_were_read`, `::test_nothing_is_deleted_before_the_batch_is_read`
- Not automated: the TWAIN/SANE scanner source and jam detection are not written yet (`scan-agent/scan_agent/capture/`).

**Manual acceptance**

| Step | Result (Pass/Fail) | Date | Tester | Remarks |
| --- | --- | --- | --- | --- |
| 1–4 main flow (station) | | | | |
| A1 jam / double feed, resume | | | | |
| A2 scanner offline | | | | |

### UC5: System validates against the database

**Preconditions:** extracted fields received from the station.

| # | Step | Expected result | Needs |
| --- | --- | --- | --- |
| 1 | System decodes the QR token and finds the issued document. | Document found. | [File-drop] / [Station] |
| 2 | Status Released and not Revoked. Page count matches. | Checked. | [File-drop] / [Station] |
| 3 | Every field compared with the snapshot and the current records. | Field table: printed value, official value, match, confidence. | [File-drop] / [Station] |
| 4 | All match with confidence ≥ 90%. | Green **VALID**, "Information matches official records". The standard footer about physical integrity is shown. | [File-drop] / [Station] |
| A1 | Alternative: QR unreadable, document number read by OCR. | **NEEDS REVIEW** at best. | [File-drop] |
| A3 | Alternative: a QR that belongs to no issued document, on a paper with a real document number. | **NEEDS REVIEW**, "QR code does not belong to any issued document … (possible forgery)". The backend decides how the paper was identified; it does not trust the agent's claim. | [File-drop] |
| A2 | Alternative: record corrected after issue. | **NEEDS REVIEW**, "Official record updated since this document was issued". | [File-drop] |

**Automated coverage**

- `backend/tests/test_scanning.py::test_uc5_genuine_scan_is_valid`
- `backend/tests/test_e2e_scan.py::test_genuine_printout_is_valid` (rendered page → agent OCR → API)
- `backend/tests/test_validation_rules.py::test_clean_scan_is_valid`
- `backend/tests/test_validation_rules.py::test_level0_identity_failures_are_invalid`
- `backend/tests/test_validation_rules.py::test_qr_unreadable_is_at_best_review`
- `backend/tests/test_validation_rules.py::test_qr_belonging_to_no_issued_document_is_never_valid`
- `backend/tests/test_validation_rules.py::test_agent_claims_about_identity_are_ignored`
- `backend/tests/test_validation_rules.py::test_record_changed_since_issue_needs_review`
- `backend/tests/test_validation_rules.py::test_name_normalization_and_similarity`
- `backend/tests/test_validation_rules.py::test_student_number_ignores_spacing_and_dashes`
- `backend/tests/test_validation_rules.py::test_low_confidence_on_matching_critical_field_needs_review`
- `backend/tests/test_validation_rules.py::test_important_field_mismatch_needs_review_not_invalid`
- `backend/tests/test_validation_rules.py::test_footer_reference_mismatch_needs_review`
- `scan-agent/tests/test_pipeline.py::test_tor_fields_and_grades_match_snapshot`
- `scan-agent/tests/test_pipeline.py::test_coe_fields_match_snapshot`
- `scan-agent/tests/test_pipeline.py::test_multi_page_tor_keeps_terms_across_page_breaks`
- `scan-agent/tests/test_parsing.py::test_table_rows_terms_and_confidence`, `::test_term_carries_over_from_previous_page`

**Manual acceptance**

| Step | Result (Pass/Fail) | Date | Tester | Remarks |
| --- | --- | --- | --- | --- |
| 1–4 genuine TOR → VALID (station) | | | | |
| 1–4 genuine COE → VALID (station) | | | | |
| A1 QR covered → NEEDS REVIEW | | | | |
| A2 record corrected after issue → NEEDS REVIEW | | | | |
| A3 foreign QR on a real number → NEEDS REVIEW | | | | |

### UC6: System detects a mismatch

**Preconditions:** a printed TOR with one grade altered (e.g. 3.00 changed to 1.50).

| # | Step | Expected result | Needs |
| --- | --- | --- | --- |
| 1 | Scan and extract as in UC4–UC5. | | [Station] |
| 2 | The altered grade differs from the snapshot with confidence ≥ 90%. | | [File-drop] / [Station] |
| 3 | Result. | Red **INVALID**. The subject is highlighted with the printed and official values and the crop of that row. | [File-drop] / [Station] |
| 4 | Audit log. | `document_scanned` entry with the result. The registrar may revoke per policy. | [Now] |
| A1 | Alternative: low OCR confidence on that cell. | **NEEDS REVIEW**. The registrar inspects the crop and confirms Invalid with remarks. The automatic result is kept. | [File-drop] / [Station] |

**Automated coverage**

- `backend/tests/test_scanning.py::test_uc6_altered_grade_is_invalid_with_field_shown`
- `backend/tests/test_scanning.py::test_review_needs_review_result`
- `backend/tests/test_scanning.py::test_fake_document_number_is_invalid`
- `backend/tests/test_scanning.py::test_forged_qr_with_a_real_document_number_is_not_valid`
- `backend/tests/test_scanning.py::test_revoked_document_is_invalid`
- `backend/tests/test_validation_rules.py::test_uc6_altered_grade_with_high_confidence_is_invalid`
- `backend/tests/test_validation_rules.py::test_altered_grade_with_low_confidence_needs_review`
- `backend/tests/test_validation_rules.py::test_altered_critical_field_is_never_valid` (every critical field, at 99% and 60% confidence)
- `backend/tests/test_validation_rules.py::test_extra_and_missing_rows_need_review`
- `scan-agent/tests/test_pipeline.py::test_overprinted_grade_is_read_as_printed`

**Manual acceptance**

| Step | Result (Pass/Fail) | Date | Tester | Remarks |
| --- | --- | --- | --- | --- |
| 1–4 edited-PDF grade → INVALID (station) | | | | |
| A1 hand-altered grade → INVALID or NEEDS REVIEW, then reviewed | | | | |

### UC7: Bulk scanning of multiple documents

**Preconditions:** a stack of 20 documents (mix of TORs and COEs).

| # | Step | Expected result | Needs |
| --- | --- | --- | --- |
| 1 | Operator selects **Bulk** and loads the stack. | Screen says to use the **Bulk** tray. | [Now] (screen); [Station] |
| 2 | Scanner feeds all pages continuously. | | [Station] |
| 3 | Pages split into documents by first-page QR and footer page markers. | Each document gets a stack position. | [File-drop] / [Station] |
| 4 | Each document validated. Results fill the batch table. | Position, document number, result, top reason, running totals. | [File-drop] / [Station] |
| 5 | Summary exported. | CSV or PDF export of the batch. | [Now] once results exist |
| A1 | Alternative: a TOR page missing. | That document is **INVALID**, "Page count differs from issued record: page 2 of 3 missing". | [File-drop] / [Station] |
| A2 | Alternative: double feed. | Batch pauses. Operator re-feeds. Same session continues. | [Station] |

**Automated coverage**

- `backend/tests/test_scanning.py::test_uc7_bulk_session_batch_table`
- `backend/tests/test_e2e_scan.py::test_bulk_stack_genuine_tampered_fake`
- `backend/tests/test_validation_rules.py::test_missing_page_is_invalid_with_page_named`
- `backend/tests/test_dashboard.py::test_validation_stats_and_exports` (CSV)
- `backend/tests/test_dashboard.py::test_validation_pdf_export` (PDF export, with the PDF renderer stubbed)
- `backend/tests/test_scanning.py::test_history_filters_by_result_type_and_date` (history filters, date-range export)
- `backend/tests/test_dashboard.py::test_dashboard_date_range`
- `scan-agent/tests/test_pipeline.py::test_bulk_stack_splits_into_documents`
- `scan-agent/tests/test_pipeline.py::test_missing_middle_page_is_visible_to_validator`

**Manual acceptance**

| Step | Result (Pass/Fail) | Date | Tester | Remarks |
| --- | --- | --- | --- | --- |
| 1–5 stack of 20 (station) | | | | |
| A1 missing page → INVALID with page named | | | | |
| A2 double feed, resume | | | | |

### Supporting features and security (beyond the seven use cases)

These features are part of the MVP scope (Sections 4, 5A–5C, 5H, 18). They are accepted in UAT (see the sign-off sheet) and have their own automated tests.

| Area | What is checked | Automated coverage | Needs |
| --- | --- | --- | --- |
| Academic setup | Registrar adds terms (make current, lock), programs, subjects, teachers and class sections; enrolls a class list by student number (all-or-nothing, every problem listed). Teachers cannot edit the catalog. | `test_records.py::test_catalog_setup_by_registrar`, `::test_enroll_a_class_list_by_student_number`, `::test_teacher_cannot_edit_catalog` | [Now] |
| CSV/Excel imports | Student and grade imports are all-or-nothing with an error per row. Files are refused when they are too large once unpacked (XLSX "bomb") or have more than 10,000 rows. | `test_records.py::test_import_students_csv`, `::test_import_students_reports_every_row_error`, `::test_import_rejects_missing_columns`, `::test_import_grades_csv`; `test_tabular.py::test_reads_xlsx_and_normalizes_cells`, `::test_csv_with_bom_and_short_rows`, `::test_bad_files`, `::test_xlsx_declaring_a_huge_range_reads_only_the_real_rows`, `::test_too_many_rows_is_refused` | [Now] |
| Grade change approval | Change request only on locked grades; registrar approves (grade updated) or rejects (grade kept), with reason | `test_records.py::test_grade_change_request_flow`, `::test_change_request_only_for_locked_grades`, `::test_reject_keeps_grade` | [Now] |
| Audit trail | Search by text, action, record and date, newest first, paged. Registrar and admin only. Per-document timeline. | `test_audit_search.py::test_search_filters_and_pages`, `::test_students_and_teachers_cannot_read_the_audit_log`; `test_requests.py::test_document_timeline` | [Now] |
| Accounts | Only admins create staff logins through the user screen. Registrars manage students and teachers. Password reset unlocks and forces a change. Deactivated users cannot sign in. Validation permission only for registrars. | `test_users_rbac.py` (all 8 tests); `test_records.py::test_create_student_with_login` | [Now] |
| Sign-in and sessions | httpOnly cookie with no fixed lifetime; 30-minute idle timeout; logout; forced password change; 10-character minimum; lockout after 5 failures **ending after 15 minutes** (failures then count from zero); **the same answer for unknown email, wrong password, locked and deactivated accounts** | `test_auth.py` (all 13 tests), including `::test_lockout_ends_after_the_lockout_period`, `::test_failed_attempt_after_lockout_expiry_counts_from_zero`, `::test_deactivated_account_gets_the_generic_answer`, `::test_unknown_email_and_wrong_password_give_same_error`, `::test_session_cookie_has_no_fixed_lifetime` | [Now] |
| Route-by-route access | Every API route refuses anonymous callers. The routes each role (student, teacher, registrar, operator, admin) can reach are pinned, so a new route fails the test until someone decides who may call it. Station routes answer 401 without a valid key. | `test_access_matrix.py::test_every_route_rejects_anonymous_callers`, `::test_routes_each_role_can_reach_are_pinned`, `::test_station_routes_answer_401_without_a_valid_key` | Automated only |
| Cross-site writes | POST/PUT/PATCH/DELETE from another site (`Sec-Fetch-Site` same-site or cross-site, or a foreign `Origin`) are refused with 403. Reads and non-browser clients (scan station) are unaffected. | `test_access_matrix.py::test_writes_from_other_sites_are_refused` | Automated only |
| Security headers | API responses carry `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options` and `Cache-Control: no-store`. Nginx adds HSTS and CSP (checked in the CI `deploy` job). | `test_access_matrix.py::test_api_responses_carry_security_headers` | Automated; check again on the school server |
| Go-live commands and daily job | `create-admin` prints a one-time password that must be changed. `install-templates` is safe to run twice. `setup-db-roles` rejects unsafe names and weak passwords. The daily job deletes only idle sessions. | `test_manage.py` (all 6 tests), including `::test_daily_job_purges_only_idle_sessions` | [Now] on the server |
| Station file clean-up | `--delete-after-upload` deletes only files that were read, and only after the whole batch | `scan-agent/tests/test_filedrop.py` (all 3 tests) | [Station] |

**Manual acceptance:** recorded in the signed-off acceptance sheet.

---

## 4. Non-functional targets (Section 21)

| Area | Target | How it is verified | Automated evidence | Result | Date | Tester |
| --- | --- | --- | --- | --- | --- | --- |
| Performance: web | Typical pages < 2 s on the campus network | Load test in CI (below); then time the main screens on the school network | CI `deploy` job load test: 95th percentile per screen 69–150 ms, worst single load 420 ms (100 users, full stack) | Pass in CI; campus network to do | 2026-10-05 | CI |
| Performance: generation | COE < 5 s; 4-page TOR < 10 s | Asserted in CI; then time `document_generated` against `request_submitted` in the audit log on the production server | `test_documents.py::test_generation_meets_section21_time_targets`: COE 0.09 s, 4-page TOR render 0.44 s on the CI runner | Pass in CI; server to confirm | 2026-10-05 | CI |
| Performance: validation | Single result ≤ 15 s after the scan; bulk ≥ 4 one-page COEs/min | Stopwatch at the station; batch timestamps | None. The scan-agent README reports about 2 s per one-page document and 4.6 s per 3-page TOR on a laptop CPU, measured in development. | | | |
| Concurrency | 100 simultaneous web users without errors | `deploy/loadtest/` (Locust): 100 users (80 students, 20 registrar/operator) opening the real screens for 3 minutes against Nginx + API + Postgres; `check.py` fails on > 1% errors or a screen over 2 s | CI `deploy` job: 100 users, ~10,000 requests, 0 failures | Pass | 2026-10-05 | CI |
| Availability | 99% in office hours during the pilot | Uptime log during the pilot | n/a | | | |
| Scalability | 5,000 students, 20,000 documents | Load the seed scaled to that size; check that request lists and dashboards still meet the web target | None | | | |
| OCR accuracy | ≥ 98% field-level on ≥ 100 clean 300 DPI scans | Section 5 | Pipeline tests on rendered images only | | | |
| False VALID rate | 0 in the altered-document set | Section 6 | `test_validation_rules.py::test_altered_critical_field_is_never_valid`, `test_e2e_scan.py::test_bulk_stack_genuine_tampered_fake` | | | |
| Security | No critical or high findings in a basic scan | Section 7 (OWASP ZAP) | ZAP baseline in the CI `deploy` job: 0 High (see Section 7). Server-side role checks: `test_access_matrix.py`, `test_users_rbac.py`, `test_records.py::test_student_cannot_reach_other_students`, `::test_teacher_only_sees_own_sections`. Login controls: `test_auth.py` (all 13 tests). | | | |
| Usability | Registrar processes a request after 1 h of training; student submits in < 2 min; works on phone browsers | Observe during UAT and training | None | | | |
| Maintainability | Git, README, setup scripts, automated tests on eligibility, placeholder mapping and comparison rules, Docker | Review | `test_requests.py` (eligibility), `test_documents.py` (placeholders), `test_validation_rules.py` (comparison rules). CI on every push. | | | |
| Reliability | Failed jobs retried or flagged, no silent failures; scan sessions survive a jam | Force a generation failure; jam the scanner | `test_requests.py::test_generation_blocked_then_regenerated` (blocked request flagged to the registrar). A failed daily job or backup is logged and retried the next day (`deploy/daily.sh`). | | | |
| Backup / recovery | Daily backups; RPO ≤ 24 h; restore ≤ 4 h; restore tested once | Section 8 | CI `deploy` job: backup, then the `verify-backup.sh` restore check | | | |
| Language | English UI | Review | n/a | | | |

## 5. OCR accuracy run

**Target (Section 21):** at least 98% field-level accuracy on clean 300 DPI scans of system-printed documents, measured on at least 100 documents.

**Tool:** `scan-agent/scan_agent/evaluate.py`. The full procedure is in `.claude/skills/ocr-eval/SKILL.md`.

**Procedure**

1. **Issue the test set.** Issue at least 100 TOR and COE documents through the normal flow (request, approve, verify, release) on the server, using the school's real templates once they arrive. Include multi-page TORs.
2. **Print and scan** on the station at 300 DPI grayscale **[Station]**. Save the page images or the scanner's PDFs into one folder, named in stack order.
3. **Export expected values.** For each document, save `<DOCUMENT_NUMBER>.json` containing `{"snapshot": …}`. Use the `data_snapshot` field from `GET /api/documents/{id}` (registrar).
4. **Check zones.** `evaluate.py` reads the OCR zones of the built-in default templates (`backend/app/documents/defaults`). If the school's templates have different zones, point it at that template version's `field_zones` first.
5. **Run:**
   ```
   cd scan-agent
   .venv/Scripts/python -m scan_agent.evaluate --scans <scans> --expected <expected> --report ocr-accuracy.csv
   ```
6. **Read the result.** It prints documents, fields, correct and accuracy %, and lists every miss. It exits non-zero below 98%. `ocr-accuracy.csv` has one row per field: document, field, expected, read, confidence, correct.

**What is counted:** the text fields present on the document (document number, student name, student number, program, date issued, GWA, year level, semester, academic year, purpose), plus one entry per TOR grade row, matched by subject code and grade. A document whose number cannot be read counts as one failed entry.

**Rule:** do not loosen the backend thresholds (`ADGVS_OCR_CONFIDENCE_THRESHOLD`, `ADGVS_NAME_REVIEW_SIMILARITY`) to raise the number. Fix the extraction instead, and add a regression test in `scan-agent/tests/`.

| Run | Documents | Fields | Correct | Accuracy | Pass (≥ 98%)? | Date | Run by | Report file |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | | | | | | | | |
| 2 (after fixes, if any) | | | | | | | | |

## 6. Tamper test set

**Criterion (Section 21):** no altered document is ever VALID. INVALID is the goal. NEEDS REVIEW is acceptable only where the alteration is physically unclear. Any VALID on variants 2–8 blocks release. Procedure: `.claude/skills/tamper-test/SKILL.md`.

**Automated guard (run first):**
```
cd backend
.venv/Scripts/python -m pytest -q tests/test_validation_rules.py tests/test_e2e_scan.py
```

**Physical set [Station].** Issue genuine documents through the normal flow, build the variants below, and scan them as one **Bulk** stack plus a few in **Single** mode. Record results from the batch table (`GET /api/scan-sessions/{id}`).

| # | Variant | Expected | Actual result | Top reason | Pass/Fail | Date | Tester |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Genuine print | VALID | | | | | |
| 2 | Edited PDF (one grade changed), printed | INVALID | | | | | |
| 3 | Correction tape + handwritten grade | INVALID or NEEDS REVIEW | | | | | |
| 4 | Overprinted grade (sticker or reprint over the cell) | INVALID or NEEDS REVIEW | | | | | |
| 5 | Changed name or student number | INVALID | | | | | |
| 6 | Fake TOR with a made-up document number | INVALID ("not issued by this institution") | | | | | |
| 7 | Genuine document with one page removed | INVALID ("page X of Y missing") | | | | | |
| 8 | Genuine document after the registrar revokes it | INVALID ("revoked") | | | | | |
| 9 | Photocopy of a genuine document | VALID (expected; see spec Summary H) | | | | | |
| 10 | Genuine, but a grade corrected after issue | NEEDS REVIEW ("record updated since issue") | | | | | |

**False VALIDs on variants 2–8 (must be 0):** ______

## 7. Security scan

**Target:** no critical or high findings in a basic vulnerability scan before go-live (Section 21).

**Tool:** OWASP ZAP baseline scan (`zap-baseline.py`) against the full Docker stack over HTTPS, in the CI `deploy` job (`.github/workflows/ci.yml`). The job fails on any High alert and uploads the `zap-report` artifact. Run it once more against the school's server after installation.

| Check | Target | Result (critical / high / medium / low) | Pass/Fail | Date | Run by | Report |
| --- | --- | --- | --- | --- | --- | --- |
| OWASP ZAP baseline, full Docker stack (CI `deploy` job) | 0 critical, 0 high | 0 High, 0 Medium, 0 FAIL (CI run for commit `4469041`). The earlier Medium (CSP `style-src 'unsafe-inline'`) is gone after the CSP change, checked in a browser on every screen first. Remaining: 1 Low (no Cross-Origin-Embedder-Policy, left off so the built-in PDF viewer keeps working) and informational notes. | Pass | 2026-10-05 | CI | `zap-report` artifact |
| OWASP ZAP baseline against the school's server | 0 critical, 0 high | | | | | |
| HTTPS only: HTTP redirects to HTTPS; TLS 1.2+; HSTS and CSP present | Yes | Checked by the CI `deploy` job on the Docker stack. Check again on the school's server with its certificate. | | | | |
| Station connects over HTTPS and verifies the certificate (`--ca-cert` for a campus certificate) | Yes | | | | | |
| Database and API ports not reachable from outside the server (only Nginx publishes 80/443) | Yes | | | | | |
| API database login cannot change the schema or the audit log | Refused | Checked by the CI `deploy` job | | | | |

## 8. Backup and restore test

**Target (Sections 18, 21):** daily automated backups of the database **and** the storage volume (PDFs, scan images); 30 days kept; one copy off-server; RPO ≤ 24 h; restore within 4 h; one restore tested before go-live.

**How it works:** the `backup` service runs `deploy/backup/backup.sh` every day (default 02:00). It writes a `pg_dump` of the database and a tar of the storage volume, each encrypted with GPG AES-256, plus SHA-256 checksums, and deletes sets older than `BACKUP_RETENTION_DAYS` (30). `deploy/backup/verify-backup.sh` restores the newest set into a scratch database, compares row counts with the live database, lists the file archive, and drops the scratch copy. `deploy/backup/restore.sh <set> --yes` is the disaster-recovery restore. The procedure is in the admin installation guide.

| Check | Target | Result | Pass/Fail | Date | Run by |
| --- | --- | --- | --- | --- | --- |
| Backup and restore check in CI | Passes | `verify-backup.sh` restore check passed in CI (scratch DB, row counts, file archive). On-site restore test on the school server still to do. | Pass (CI) | 2026-10-05 | CI |
| Daily backup job runs on the school server (database + storage volume) | Yes | | | | |
| Backups encrypted; `BACKUP_DIR` on off-server storage (NAS or external disk); passphrase kept away from the server | Yes | | | | |
| `verify-backup.sh` on the school server | RESTORE CHECK PASSED | | | | |
| Restore to a clean server: sign in, open a released PDF, scan a document issued before the backup → VALID | Works | | | | |
| Restore time | ≤ 4 h | | | | |
| Audit-log trigger present after restore (an UPDATE on `audit_logs` fails) | Yes | | | | |

## 9. Known gaps found while preparing this plan

**Still open** (the code on main at `4469041` does not yet do what the specification says):

| # | Gap | Spec | Where | Affects |
| --- | --- | --- | --- | --- |
| 1 | Level 2: logo detection and header-text comparison are not implemented (the layout-anchor part is, see 7 below). **Blocked on the school's official templates and logo.** | Sections 8, 15, 19 | `backend/app/services/validation.py` (`layout_reasons`) | UC5, tamper variants 3–4 |
| 5 | TWAIN/SANE scanner source and jam/double-feed detection are not written (hardware track). Scanners that save to a folder are covered by the station service (`--watch`, 8 below). | Sections 9, 10 | `scan-agent/scan_agent/capture/` (README ownership table) | UC4, UC7 |

**Closed since the first version of this plan** (commits `bba499e`, `cafc6ff`, `4469041`, then `ad913a1`, `5a1db9f`):

| # | Was | Now |
| --- | --- | --- |
| 7 | No layout check. | Level 2 (basic): the agent measures the QR position and size and where the grades table starts; the backend compares them with the zones of the template version the document was printed with and sends anything more than 8 mm off to NEEDS REVIEW. Measured on real and simulated scans: QR centre within 0.1 mm, table within 1 mm. Tests: `test_validation_rules.py::test_layout_*`, `scan-agent/tests/test_pipeline.py::test_layout_anchors_are_measured_where_the_template_prints_them`. |
| 8 | The station agent ran once per batch, so "offline" never reached the dashboard. | `--watch` station service: status every 15 s, offline when the scanner folder is missing, stacks uploaded to the operator's session, retried without loss or duplicates; dashboard shows "No signal" when it stops. Tests: `scan-agent/tests/test_watch.py`, `test_scanning.py::test_station_that_stopped_reporting_shows_silent_but_is_not_blocked`. |
| 2 | An offline scanner did not block scanning. | `start_session` refuses with 409 while the station reports `offline` (`backend/app/api/scanning.py`), and the operator screen disables Single/Bulk (`frontend/src/pages/operator/OperatorHome.tsx`). Test: `test_scanning.py::test_uc4_offline_scanner_blocks_new_sessions`. |
| 3 | No audit search screen and no timeline screen. | `GET /api/audit-logs` (`backend/app/api/audit.py`) with the Audit trail screen (`frontend/src/pages/AuditTrail.tsx`); timeline on request review (`frontend/src/pages/registrar/RequestReview.tsx`). Test: `test_audit_search.py`. |
| 4 | File-drop input was never deleted from the station. | `--delete-after-upload` (`scan-agent/scan_agent/capture/filedrop.py`, `__main__.py`). It must be used on the production station. Test: `scan-agent/tests/test_filedrop.py`. |
| 6 | No Nginx/HTTPS, ZAP or backups in the repository. | `docker-compose.yml` and `deploy/`: Nginx HTTPS, daily encrypted backups kept 30 days, `verify-backup.sh`, ZAP baseline in the CI `deploy` job. |
| 7 | Abandoned login sessions were never deleted. | `python -m app.jobs daily` purges idle sessions (`backend/app/jobs.py`). Test: `test_manage.py::test_daily_job_purges_only_idle_sessions`. |
