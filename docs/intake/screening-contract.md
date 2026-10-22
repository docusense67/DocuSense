# Incoming-document screening (Part I): shared contract

Status: pre-approval build (Annex A, DS-CR-2026-01, under review). Built on feature branches and
merged into `integration/wave2`; NOT merged to `main` until the client signs Annex A.

## Flow

```
feeder -> compartment -> camera capture (scan-agent, S-01)
  -> image-quality check (agent): not usable -> recapture (max 3), then submit with quality.ok=false
  -> screening features (agent, S-02/S-03/S-05): document type, indicators, similarity, fields
  -> POST to backend (station key) -> backend decides (S-04): passed_screening -> Tray 1,
     manual_review -> Tray 2 -> agent routes the gate, reports the tray it used
  -> registrar/staff: review queue (S-09) accept / reject / request resubmission;
     extracted fields reviewed -> student profile (S-06/S-07, see "Student data" below)
```

Rules (CLAUDE.md applies):
- The agent only measures and extracts. The backend owns every screening decision and the tray
  assignment (`backend/app/services/intake.py`, pure unit-tested functions).
- Wording: "passed screening" / "needs manual review". Never "authentic", "genuine" or "verified".
- No personal data in logs. Sample documents in tests are synthetic and marked SAMPLE.

## Document types (`IntakeDocType`)

`psa_birth_certificate`, `jhs_report_card`, `good_moral_certificate`,
`jhs_completion_certificate`, `unknown`.

## Screening result JSON (agent -> backend), version 1

```json
{
  "schema": 1,
  "agent_version": "0.3.0",
  "capture": {"source": "picamera2|opencv|file", "width": 4608, "height": 2592, "attempts": 1},
  "quality": {
    "ok": true,
    "issues": [],                       // subset of: blur, too_dark, too_bright, glare, skew,
                                        //   page_not_found, page_cut_off, low_resolution
    "blur_score": 412.3,                // variance of Laplacian on the normalized page
    "brightness": 0.71,                 // mean, 0..1
    "glare_fraction": 0.002,            // share of clipped highlight pixels
    "skew_deg": 0.8,
    "coverage": 0.93,                   // page area / frame area after detection
    "effective_dpi": 312
  },
  "doc_type": {
    "predicted": "psa_birth_certificate",
    "confidence": 0.91,                 // 0..1
    "scores": {"psa_birth_certificate": 0.91, "jhs_report_card": 0.05, "...": 0.0}
  },
  "checks": {
    "expected_headings_found": 4, "expected_headings_total": 4,
    "expected_fields_found": 9,   "expected_fields_total": 10,
    "layout_score": 0.88,               // fields/headings within position tolerance, 0..1
    "seal_logo_score": 0.80,            // best template match of expected seals/logos, 0..1 (null if none defined)
    "font_consistency": 0.93,           // 1 - normalized spread of text heights within a field class
    "alignment_score": 0.90
  },
  "indicators": [                       // suspicious features found; empty when none
    {"code": "missing_heading|missing_field|field_out_of_position|seal_logo_not_found|font_inconsistent|alignment_off|unexpected_text|altered_region",
     "severity": 0.6, "detail": "short, no personal data", "box": [x, y, w, h]}
  ],
  "similarity": {"score": 0.84, "reference_count": 12, "method": "orb+layout"},
  "fields": {                           // OCR extraction, values as printed
    "last_name": {"value": "DELA CRUZ", "confidence": 0.93}
  },
  "timings_ms": {"capture": 900, "quality": 40, "screening": 2100}
}
```

Unknown keys must be ignored by the backend (forward compatible). Missing optional blocks
(`similarity`, `fields`, `checks.seal_logo_score`) mean "not measured", not "failed".

Agent-side definitions (scan-agent `scan_agent/screening/`, S-02/S-03/S-05):
- `doc_type.scores`: calibrated probability per type (the four types, no `unknown` key).
  `predicted` is the most probable type if its probability is >= 0.5, else `unknown` with
  `confidence` = 1 - that probability. This only defines "unknown"; the backend's own
  confidence threshold still applies.
- When `predicted` is `unknown` there is no profile to check against: `checks`,
  `similarity` and `fields` are omitted and `indicators` is `[]`.
- `checks.font_consistency` and `checks.alignment_score` may also be `null` (fewer than two
  members of any group could be measured): not measured, like `seal_logo_score`.
- `indicators[].severity` = measured deviation / (2 x the profile's tolerance), clipped:
  0.5 = exactly at the tolerance the reference profile states. Reported from 0.25 up.
  Missing headings/labels carry a fixed per-profile severity (defaults 0.8 / 0.7); a blank or
  unreadable value 0.3 (blank fields occur on genuine forms).
- `indicators[].box`: `[x, y, w, h]` in pixels of the submitted page image.
- `timings_ms` may carry extra keys (`screening_ocr`).

## Station API (backend), X-Station-Key auth

- `POST /api/station/intake/batches` -> `201 {"batch_id": "..."}` (mixed-document batch, S-10)
- `POST /api/station/intake/batches/{batch_id}/end` -> `200 {batch summary}`
- `POST /api/station/intake/documents` multipart: `image` (JPEG, normalized page), `result`
  (screening JSON above), `batch_id` (optional), `sequence` (int, position in batch)
  -> `201 {"intake_id": "...", "decision": "passed_screening|manual_review", "tray": 1|2,
     "reasons": ["doc_type_low_confidence", ...]}`
- `POST /api/station/intake/documents/{intake_id}/routed` `{"tray": 1|2}` -> `200` (what the gate
  actually did; a mismatch or jam is recorded and the document goes to manual review)

## Staff API (backend), registrar/admin roles

- `GET /api/intake/documents?status=&doc_type=&batch_id=` (review queue, newest first)
- `GET /api/intake/documents/{id}` (detail: result, reasons, indicators, tray, history)
- `GET /api/intake/documents/{id}/image`
- `POST /api/intake/documents/{id}/review` `{"action": "accept|reject|request_resubmission",
  "reason": "..."}` (reason required for reject and request_resubmission; audited)
- `GET/PUT /api/intake/settings` (admin): thresholds used by the decision function

## Operator console API (backend), validation operators (`require_validator`)

The intake station's TFT/operator screen (`/intake-station`, `app/api/intake_operator.py`).
Responses carry document type, decision, tray, routing state and reason texts only: never
extracted field values, indicators, images or the raw screening result.

- `GET /api/intake-station/stations`: active stations with `state` (`idle|scanning|error|
  offline|no_signal|unknown`, from `/station/status` + heartbeat age) and `intake` (has screened
  pages); intake stations first
- `GET /api/intake-station/stations/{id}/live?limit=10`: one poll for the screen: station,
  current batch (open, else the latest) with counts, latest pages newest first (the running
  batch's only), today's counts
- `GET /api/intake-station/stations/{id}/batches?limit=10`, `GET /api/intake-station/batches/{id}`
- `POST /api/intake-station/batches/{id}/end`: the operator closes a batch (audited with the
  operator's id; idempotent). The station's next page for it gets 409 and it starts a new batch.
- Counts: `tray_1`/`tray_2` = where pages belong now (by decision), `pending` (gate not reported),
  `jams`, `wrong_tray` (page landed in the other tray: the operator moves it).

## Decision (backend, S-04) defaults, configurable

manual_review if any of: `quality.ok` false; `doc_type.predicted == unknown`;
`doc_type.confidence < 0.75`; any indicator `severity >= 0.5`; `checks.layout_score < 0.70`;
`similarity.score < 0.60` (only when measured); expected headings found < total.
Otherwise passed_screening. `reasons` lists every rule that fired.


## Backend implementation notes (feat/intake-backend)

Built as specified; these points pin down what the contract left open. Items marked
**(extension)** add to the contract without changing what it already says.

- **IDs are integers.** `batch_id` and `intake_id` are JSON numbers; send them back as given.
- **Reason codes** (`reasons`, in rule order): `quality_not_ok`, `doc_type_unknown`,
  `doc_type_low_confidence`, `severe_indicator`, `layout_score_low`, `similarity_low`,
  `expected_headings_missing`. Routing adds `routed_tray_mismatch` or `routing_jam`.
- **What makes a result malformed (422).** `schema` must be `1`. Required: `quality.ok` (bool),
  `doc_type.predicted` (non-empty string), `doc_type.confidence`, `checks.layout_score`,
  `checks.expected_headings_found` / `_total` (whole numbers >= 0) and `indicators` (a list,
  each item an object with a numeric `severity`). Scores, confidences and severities must be
  finite numbers in 0..1 (`NaN` is refused). The `result` field is capped at 256,000 characters.
  Everything else is optional and only shown to staff. A `doc_type.predicted` name the backend
  does not know is not an error: it counts as `unknown` (manual review). Missing or `null`
  `similarity` / `similarity.score` = not measured.
- **Image:** JPEG or PNG; checked and recompressed exactly like scan pages
  (`services/scan_images.compress_page`, grayscale JPEG at most 1654 px wide), stored through
  `app/storage.py`, streamed to staff only by `GET /api/intake/documents/{id}/image`.
- **Batches:** `batch_id` is optional on a document. Starting a batch ends any batch the same
  station left open. Ending a batch twice returns the same summary; a document sent to an ended
  batch gets 409. A station only sees its own batches and documents (404 otherwise). Summary:
  `{batch_id, started_at, ended_at, total, passed_screening, manual_review, routed, unrouted,
  routing_issues}`.
- **Routed callback (extension):** body `{"tray": 1|2, "jam": false, "detail": "..."}`.
  `jam: true` (or no `tray`) reports a jam; `tray` alone is the contract's form. A jam or a tray
  other than the assigned one turns the decision into `manual_review` and appends the reason.
  Repeating the same report returns 200; a different second report returns 409. Response:
  `{intake_id, decision, assigned_tray, routed_tray, jam, reasons}`.
- **Review rules:** a reason (3+ characters) is required for `reject` and
  `request_resubmission`, and also for `accept` when the screening decision is `manual_review`
  (the reviewer overrides a flag, so the audit trail says why). Only documents in status
  `screened` can be reviewed (409 otherwise). The screening `decision` is never overwritten by
  the review; the outcome is in `status`.
- **Queue (extension):** besides `status`, `doc_type` and `batch_id`, `GET
  /api/intake/documents` takes `decision` and `limit` (default 100, max 500).
- **Settings:** `PUT /api/intake/settings` takes all four thresholds (0..1; the indicator
  severity must be > 0) plus a required `reason`, and is audited with old and new values.
  Every screening audit entry records the thresholds it was decided with.


## Student data (S-06/S-07, feat/intake-profiles)

Staff review the fields the station read, correct them, and link the document to a student
(existing, confirmed by staff, or created from the reviewed fields). Code:
`backend/app/services/intake_fields.py` (pure: schemas, normalization, validation, matching)
and `backend/app/api/intake_profiles.py` (routes).

**Status flow** (`IntakeStatus`): `screened -> accepted -> data_reviewed -> linked`.
- Only documents that passed screening or were accepted by staff get there. Rejected and
  resubmission-requested documents can't be used (409); a `manual_review` document still in
  `screened` must be accepted first (409, the review rules above apply).
- A `passed_screening` document in `screened` may go straight to `data_reviewed`: saving its
  fields accepts it, audited as `intake_document_reviewed` with `implicit` set.
- `linked` locks the fields; `unlink` (reason required) returns the document to `data_reviewed`
  and leaves the student profile unchanged.

**Field schema** per document type (station field names in brackets; keys shared across types):

| Type | Fields (required *) | Student attribute |
| --- | --- | --- |
| PSA birth certificate | `last_name`* `first_name`* `middle_name` [`child_*_name`], `sex`, `birth_date`* [`date_of_birth`], `birth_place` [`place_of_birth`], `mother_name` / `father_name` [first + middle + last joined] | `last_name`, `first_name`, `middle_name`, `sex`, `birthdate`, `birth_place`, `mother_name`, `father_name` |
| JHS report card | names* (split from `learner_name`), `lrn`, `sex`, `school_year`, `grade_level` [`grade`], `section`, `general_average`, `school_name` | names, `lrn`, `sex` |
| Good moral certificate | names* (split from `student_name`), `school_name`, `date_issued` | names |
| JHS completion certificate | names* (split from `learner_name`), `lrn`, `school_name`, `date_issued` [`date_given`] | names, `lrn` |

Prefill normalizes what it safely can (printed dates such as "MARCH 5, 2010" or "5th day of
June, 2024" to ISO, numeric dates month first; "M"/"F" to Male/Female; LRN spaces removed;
"S.Y. 2025 - 2026" to 2025-2026; "Grade 10" to 10) and never invents values. Each prefilled
field carries the station's `read_as` text, its `confidence` (lowest of the station fields used;
0..100 readers are scaled) and `derived` (split from a full name).

**Validation** (422 `{"detail": {"message", "errors": {key: text}}}`): required names; names are
letters, spaces, `.`, `'`, `-`; dates `YYYY-MM-DD`, real, not in the future, not before 1900;
LRN exactly 12 digits; sex Male/Female; school year `YYYY-YYYY` consecutive; grade 1-12;
general average 60-100, at most 3 decimals; text length limits; keys outside the schema refused.

**Matching** (suggestions only, never an automatic link), strongest first: `lrn` (same LRN),
`name_birthdate` (same last + first name and birth date), `name` (same names, one side has no
birth date), `name_birthdate_differs`, `first_name_birthdate` (same first name + birth date,
different last name). Names compare case-, accent-, space- and punctuation-insensitively.
`strength` is `strong` for `lrn` / `name_birthdate` without conflicts (`lrn_differs`,
`name_differs`, `birthdate_differs`), else `possible`.

**Routes** (registrar + admin; `{id}` = intake id):
- `GET /api/intake/documents/{id}/fields[?doc_type=]` -> schema, `extracted` (prefill), `reviewed`
  (`values`, `changed` = keys corrected vs the station), `editable`, `blocked`, linked `student`.
  `doc_type` previews another type's schema (for `unknown` documents).
- `PUT /api/intake/documents/{id}/fields` `{"values": {key: text|null}, "doc_type"?}` -> saves the
  reviewed set (the station's `screening_result` is never changed); `doc_type` corrects the type
  (required when it is `unknown`).
- `GET /api/intake/documents/{id}/candidates` -> `{"candidates": [{student_id, student_number,
  name, birthdate, lrn, match, strength, conflicts}]}` (needs reviewed fields).
- `POST /api/intake/documents/{id}/link` `{"student_id", "reason"?}`: a reason (3+ chars) is
  required unless the student is a `strong` candidate.
- `POST /api/intake/documents/{id}/create-student` `{"student_number", "program_id"?, "year_level"?,
  "admission_date"?, "confirm_new"?}` -> 201. Profile values come only from the reviewed fields;
  master-list rules apply (unique student number and LRN -> 409). While candidates exist,
  `confirm_new: true` is required (409 otherwise).
- `POST /api/intake/documents/{id}/unlink` `{"reason"}`.
- `GET /api/intake/documents?student_id=` lists a student's linked documents (any status);
  rows now carry `fields_reviewed_at`, `student_id`, `linked_at`.

**Audit** (`entity_type` `intake_document`): `intake_fields_reviewed` (`fields_corrected`,
`fields_edited` vs the previous review, `fields_empty`, old/new status and type),
`intake_document_linked` (`student_id`, `student_number`, `how` existing|created, `match`,
`conflicts`, `reason`), `intake_document_unlinked`; `student_created` with
`source: intake_document` lists `fields_from_document` by name. No extracted or reviewed value
is ever written to the audit log.
