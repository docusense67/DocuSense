# Data dictionary

Academic Document Generation & Validation System. Database schema as of 2026-10-05 (migration head `b3f1c9a2d7e4`).

**For:** the school's IT staff and Data Protection Officer (DPO).
**Source of truth:** the SQLAlchemy models in `backend/app/models/` and the Alembic migrations in `backend/alembic/versions/`. If this document and the code disagree, the code wins. Please report the difference so this page can be fixed.
**Diagrams:** class diagrams generated from those models (tables, relationships, enumerations) are in [docs/diagrams/](diagrams/README.md); they are checked against the code by the test suite.

## How to read this

- **Database:** PostgreSQL 16 in production. The automated tests use SQLite, so every type below has a SQLite equivalent.
- **Tables:** 19 application tables plus Alembic's `alembic_version`, which records which migration is applied. The specification (Section 12) lists 17 tables. Two were added: `user_sessions` (server-side login sessions) and `scan_stations` (one API key per scan station).
- **Null?** "No" means the column is `NOT NULL`. "Yes" means it may be empty.
- **Primary keys:** every table has an `id` column (integer, auto-increment, primary key). It is not repeated in the tables below.
- **Timestamps** are `timestamptz` (date and time with time zone, stored in UTC). Columns marked "set by the database" default to the time the row was inserted.
- **JSON columns** are `jsonb` on PostgreSQL (`JSON` on SQLite).
- **Personal data** columns are marked **[PD]**. The full inventory, with who can see each field, is in [privacy-data-inventory.md](privacy-data-inventory.md).

### Enumerated values (important for SQL reports)

Columns with a fixed list of allowed values are stored as `varchar`, not as PostgreSQL enum types. The database stores the value's **code name in capitals**. The web app and API show the **display value**. For example, the database stores `FIRST` in `terms.semester` and the API returns `1st`. Write SQL queries against the stored code names.

The database has no CHECK constraint on these columns. The application is the only thing that limits them to the listed values, so do not insert rows by hand.

| Enum | Used in | Stored code → display value |
| --- | --- | --- |
| Role | `users.role` | `STUDENT` → student, `TEACHER` → teacher, `REGISTRAR` → registrar, `ADMIN` → admin |
| StudentStatus | `students.status` | `ACTIVE` → active, `GRADUATED` → graduated, `INACTIVE` → inactive |
| Semester | `terms.semester` | `FIRST` → 1st, `SECOND` → 2nd, `SUMMER` → Summer |
| EnrollmentStatus | `enrollments.status` | `ENROLLED` → enrolled, `DROPPED` → dropped |
| GradeRemark | `grades.remarks` | `PASSED` → Passed, `FAILED` → Failed, `INC` → INC, `DRP` → DRP |
| ChangeRequestStatus | `grade_change_requests.status` | `PENDING` → pending, `APPROVED` → approved, `REJECTED` → rejected |
| DocumentType | `document_templates.document_type`, `document_requests.document_type` | `TOR` → TOR, `COE` → COE |
| TemplateStatus | `document_templates.status` | `DRAFT` → draft, `ACTIVE` → active, `RETIRED` → retired |
| RequestStatus | `document_requests.status` | `REQUESTED`, `APPROVED`, `FOR_VERIFICATION`, `VERIFIED`, `RELEASED`, `REJECTED`, `CANCELLED`, `EXPIRED`, `REVOKED` (API values are the same words in lower case; students see `client_status`: pending, approved, rejected, completed, cancelled, expired, revoked). `PROCESSING` and `FOR_APPROVAL` were retired by migration `e7b2c4d9a1f3` (S-11). |
| GeneratedDocumentStatus | `generated_documents.status` | `GENERATED`, `APPROVED`, `RELEASED`, `REVOKED` (lower case in the API) |
| ScanMode | `scan_sessions.mode` | `SINGLE` → single, `BULK` → bulk |
| ValidationResult | `validation_records.result`, `validation_records.final_result` | `VALID`, `INVALID`, `NEEDS_REVIEW` (same in the API) |
| IntakeStatus | `intake_documents.status` | `SCREENED` → screened, `ACCEPTED` → accepted, `REJECTED` → rejected, `RESUBMISSION_REQUESTED` → resubmission_requested, `DATA_REVIEWED` → data_reviewed, `LINKED` → linked (the last two from migration `d5a8c3e1f2b7`, S-06/S-07) |

### Migrations

| Revision | Date | Change |
| --- | --- | --- |
| `a70bba7fa7db` | 2026-10-04 | Initial schema: all tables, plus the `audit_logs` append-only trigger on PostgreSQL |
| `a6cbc9686657` | 2026-10-04 | `class_sections.grades_submitted_at` |
| `1c02edf5cd9d` | 2026-10-04 | `document_requests.processing_note` and `document_requests.updated_at` |
| `dc496e55fb14` | 2026-10-04 | `scan_stations.last_status`, `last_status_detail`, `last_seen_at` |
| `b3f1c9a2d7e4` | 2026-10-05 | `users.locked_at` (lockouts end after 15 minutes). Accounts already locked when it runs start their 15 minutes then. |
| `d5a8c3e1f2b7` | 2026-10-12 | S-06/S-07: `students.lrn` (unique), `birth_place`, `mother_name`, `father_name`; `intake_documents.reviewed_fields`, `fields_reviewed_by`, `fields_reviewed_at`, `student_id`, `linked_by`, `linked_at`. Revises the merge head `fb5d28bdec60`. |
| `e7b2c4d9a1f3` | 2026-10-12 | S-11 approve-first request states (in-flight requests: `APPROVED` → `VERIFIED`, `FOR_APPROVAL` → `FOR_VERIFICATION`, `PROCESSING` → `REQUESTED`, each audited as `request_status_migrated`) and the S-08 `section_advisers` table |

In the production stack (`docker-compose.yml`), the one-shot `migrate` service runs `alembic upgrade head` and then `python -m app.manage setup-db-roles` as the database owner before the API starts.

### Database logins

`python -m app.manage setup-db-roles` (`backend/app/manage.py`) creates or updates a separate database login for the API, named by `ADGVS_APP_DB_USER`. This login can read and write data but cannot change the schema. It has no UPDATE, DELETE or TRUNCATE rights on `audit_logs` and no rights on `alembic_version`. The database owner runs migrations and this command. **The API connects with this least-privilege login, not the owner** (`docker-compose.yml`, service `api`: `ADGVS_APP_DB_USER`, default `adgvs_app`). CI checks on every push that this login is refused UPDATE, DELETE and TRUNCATE on `audit_logs` and DROP/CREATE TABLE (`.github/workflows/ci.yml`, job `deploy`).

---

## 1. People

### `users` — login accounts

One row per person who can sign in. There are four roles. "Validation operator" is not a role. It is the `can_validate` flag, given to registrar accounts.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `email` **[PD]** | varchar(255) | No | Login name. **Unique** (index `ix_users_email`). Sign-in compares it without regard to upper or lower case. |
| `password_hash` | varchar(255) | No | Argon2 hash of the password. The plain password is never stored. |
| `role` | varchar(20), enum Role | No | `STUDENT`, `TEACHER`, `REGISTRAR` or `ADMIN`. Decides which API routes the account may call. |
| `is_active` | boolean | No | False after an admin or registrar deactivates the account. Inactive accounts cannot sign in. Default true. |
| `can_validate` | boolean | No | Validation-operator permission. It only has an effect on `REGISTRAR` accounts: scan sessions and validation results need role registrar **and** this flag. Default false. |
| `must_change_password` | boolean | No | True for new accounts and after a password reset. Until the user sets a new password, every other route is blocked. Default true. |
| `failed_logins` | integer | No | Consecutive failed sign-ins. Reset to 0 on success or on a password reset. |
| `is_locked` | boolean | No | Set to true after 5 consecutive failures (`ADGVS_MAX_FAILED_LOGINS`). The lockout lasts 15 minutes (`ADGVS_LOCKOUT_MINUTES`) from `locked_at`. After that, the next sign-in attempt clears it and the failure count starts from zero. A registrar or admin password reset clears it at once. |
| `locked_at` | timestamptz | Yes | When the account was locked. Empty when not locked. |
| `last_login_at` | timestamptz | Yes | Time of the last successful sign-in. |
| `created_at` | timestamptz | No | Set by the database. |

### `user_sessions` — signed-in sessions

One row per active browser session. The browser holds a random token in an httpOnly cookie, and the database stores only its SHA-256 hash.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `token_hash` | varchar(64) | No | SHA-256 of the session cookie value. **Unique**. A copy of this table cannot be used to sign in. |
| `user_id` | integer | No | FK → `users.id`, **ON DELETE CASCADE**. |
| `created_at` | timestamptz | No | When the session started. |
| `last_seen_at` | timestamptz | No | Last request. If more than 30 minutes ago (`ADGVS_SESSION_IDLE_MINUTES`), the next request is refused and the row is deleted. |
| `ip_address` **[PD]** | varchar(45) | Yes | Client IP address at sign-in (IPv4 or IPv6). |

Rows are deleted on logout, on an expired-session request, on a password change (the user's other sessions), and on a password reset or deactivation (all of the user's sessions). The daily job (`python -m app.jobs daily`, run by the `jobs` service) also deletes every session idle for longer than the timeout, so abandoned sessions are removed within a day.

### `students` — official student master list

One row per student. This is the data printed on TORs and COEs. A student can exist without a login (`user_id` empty).

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `user_id` | integer | Yes | FK → `users.id`, **unique** (one login per student at most). Empty if the student has no login. |
| `student_number` **[PD]** | varchar(30) | No | Official student number, e.g. `2021-00123`. **Unique** (index `ix_students_student_number`). Printed on every document. |
| `first_name` **[PD]** | varchar(100) | No | Printed on documents. |
| `middle_name` **[PD]** | varchar(100) | Yes | Only the middle initial is printed. |
| `last_name` **[PD]** | varchar(100) | No | Printed on documents. |
| `suffix` **[PD]** | varchar(20) | Yes | e.g. `JR.`, `III`. Printed on documents. |
| `birthdate` **[PD]** | date | Yes | Kept on the record. Not printed on TOR or COE. |
| `sex` **[PD]** | varchar(10) | Yes | Free text, up to 10 characters. Not printed. |
| `program_id` | integer | Yes | FK → `programs.id`. Must be set before a TOR or COE can be issued. |
| `year_level` | integer | Yes | 1–6 (checked by the API). Printed on the COE as e.g. "3rd Year". |
| `status` | varchar(20), enum StudentStatus | No | `ACTIVE`, `GRADUATED` or `INACTIVE`. Default `ACTIVE`. A COE needs `ACTIVE`. A TOR is refused for `INACTIVE`. |
| `admission_date` | date | Yes | Kept on the record. Not printed. |
| `contact_email` **[PD]** | varchar(255) | Yes | Contact address. This is separate from the login email in `users.email`. Not printed. |
| `contact_phone` **[PD]** | varchar(30) | Yes | Not printed. |
| `lrn` **[PD]** | varchar(12) | Yes | DepEd Learner Reference Number, exactly 12 digits (checked by the API). **Unique** (index `ix_students_lrn`; several students may have none). From enrollment documents or typed by the registrar. Not printed. |
| `birth_place` **[PD]** | varchar(200) | Yes | Place of birth as on the PSA birth certificate. Not printed. |
| `mother_name` **[PD]** | varchar(200) | Yes | Mother's maiden name as on the PSA birth certificate (one field). Not printed. |
| `father_name` **[PD]** | varchar(200) | Yes | Father's name as on the PSA birth certificate (one field). Not printed. |

### `teachers` — faculty profiles

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `user_id` | integer | Yes | FK → `users.id`, **unique**. Empty if the teacher has no login. |
| `employee_number` **[PD]** | varchar(30) | No | **Unique**. |
| `full_name` **[PD]** | varchar(200) | No | One field, not split into parts. Not printed on documents. |
| `department` | varchar(100) | Yes | |

---

## 2. Academics

### `programs` — degree programs

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `code` | varchar(20) | No | e.g. `BSIT`. **Unique**. Student CSV imports use it to find the program. |
| `name` | varchar(200) | No | e.g. "Bachelor of Science in Information Technology". Printed on TOR and COE. |
| `department` | varchar(100) | Yes | |
| `total_units` | integer | Yes | Informational. Not used in any calculation. |

### `subjects` — course catalog

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `code` | varchar(30) | No | e.g. `IT101`. **Unique**. Printed in the TOR grades table and compared row by row at scan time. |
| `title` | varchar(200) | No | Printed in the TOR grades table. |
| `units` | numeric(4,1) | No | Credit units. Printed on the TOR and used to weight the GWA. |
| `program_id` | integer | Yes | FK → `programs.id`. Empty for general-education subjects. |

### `terms` — academic periods

**Unique** on (`academic_year`, `semester`).

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `academic_year` | varchar(9) | No | e.g. `2025-2026`. |
| `semester` | varchar(10), enum Semester | No | `FIRST`, `SECOND` or `SUMMER`. |
| `is_grades_locked` | boolean | No | True once the registrar locks the term. Grades can then change only through a `grade_change_requests` row approved by the registrar. Only locked terms appear on a TOR. Default false. |
| `is_current` | boolean | No | The term a COE certifies. When the registrar marks a term current, the API clears the flag on every other term. Default false. |

### `class_sections` — one subject taught in one term

**Unique** on (`subject_id`, `term_id`, `section_code`).

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `subject_id` | integer | No | FK → `subjects.id`. |
| `term_id` | integer | No | FK → `terms.id`. |
| `section_code` | varchar(20) | No | e.g. `BSIT-1A`. |
| `teacher_id` | integer | Yes | FK → `teachers.id`. The only teacher who can grade this section. Its class adviser (`section_advisers`) may also view it. |
| `grades_submitted_at` | timestamptz | Yes | When the teacher marked the section's grades final ("submit for lock"). The registrar then locks the whole term. |

### `section_advisers` — class adviser of a block section (S-08)

**Unique** on (`term_id`, `section_code`). A block section is every `class_sections` row of one term with that section code.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `term_id` | integer | No | FK → `terms.id`. |
| `section_code` | varchar(20) | No | e.g. `BSIT-1A`; matches `class_sections.section_code`. |
| `teacher_id` | integer | No | FK → `teachers.id`. May view (never edit) the block's rosters and grades. Removing the adviser deletes the row. |
| `assigned_by` | integer | Yes | FK → `users.id`, the registrar who designated the adviser. |
| `assigned_at` | timestamptz | No | When the row was created. Changes are in `audit_logs` (`section_adviser_assigned`). |

### `enrollments` — a student in a class section

**Unique** on (`student_id`, `class_section_id`). Indexed on both columns.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `student_id` | integer | No | FK → `students.id`. |
| `class_section_id` | integer | No | FK → `class_sections.id`. |
| `status` | varchar(20), enum EnrollmentStatus | No | `ENROLLED` or `DROPPED`. Dropped enrollments are left out of class lists and documents. Default `ENROLLED`. |

### `grades` — final grade per enrollment

One grade per enrollment at most (`enrollment_id` is **unique**).

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `enrollment_id` | integer | No | FK → `enrollments.id`, **unique**. |
| `grade_value` **[PD]** | numeric(3,2) | Yes | 1.00–5.00 on the configured scale (`ADGVS_GRADE_SCALE`). Empty for `INC` and `DRP`. |
| `remarks` **[PD]** | varchar(10), enum GradeRemark | No | `PASSED`, `FAILED`, `INC` or `DRP`. Worked out from the grade: numeric grades up to `ADGVS_PASSING_GRADE` (3.00) are Passed. |
| `encoded_by` | integer | Yes | FK → `users.id`. The last person who saved this grade directly (a teacher or the registrar). |
| `is_locked` | boolean | No | Copied to true when the term is locked. Default false. |
| `updated_at` | timestamptz | No | Set by the database on insert and on every update. |

### `grade_change_requests` — corrections after a term is locked

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `grade_id` | integer | No | FK → `grades.id`. Indexed. |
| `old_value` **[PD]** | varchar(10) | Yes | The grade when the request was filed, as displayed (e.g. `2.25` or `INC`). |
| `new_value` **[PD]** | varchar(10) | No | The proposed grade. |
| `reason` | text | No | Why the change is needed. Required. |
| `requested_by` | integer | No | FK → `users.id` (the teacher or registrar who filed it). |
| `approved_by` | integer | Yes | FK → `users.id`. The registrar who approved **or rejected** it. |
| `status` | varchar(10), enum ChangeRequestStatus | No | `PENDING`, `APPROVED` or `REJECTED`. Only one pending request per grade is allowed (checked by the API). Approving it writes `new_value` into `grades`. |
| `created_at` | timestamptz | No | Set by the database. |

---

## 3. Documents

### `document_templates` — versioned TOR and COE layouts

Each upload or edit is a version. **Unique** on (`document_type`, `version`). The API allows only one `ACTIVE` version per document type. Activating a new version retires the old one. Issued documents keep the version they were printed with.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `document_type` | varchar(10), enum DocumentType | No | `TOR` or `COE`. |
| `version` | integer | No | 1, 2, 3… per document type. |
| `html_content` | text | No | The HTML/CSS template with `{{ placeholders }}` from the fixed catalog (see `docs/template-authoring.md`). It is checked before activation: unknown or missing placeholders, remote resources or scripts block it. |
| `placeholder_list` | jsonb | No | List of the placeholder names found in `html_content`, e.g. `["student_name", "student_number", ...]`. |
| `field_zones` | jsonb | Yes | OCR zone map for this version: `{"unit": "mm", "page_width": 210, "page_height": 297, "fields": {"student_name": {"x", "y", "w", "h", "pages"}, ...}}`. Coordinates are millimetres from the top-left of an A4 page. `pages` is `first`, `rest` or `all`. The scan station receives these zones. The version cannot be activated until every required zone is defined. |
| `status` | varchar(10), enum TemplateStatus | No | `DRAFT`, `ACTIVE` or `RETIRED`. Default `DRAFT`. |
| `created_by` | integer | Yes | FK → `users.id`. Empty for the built-in stand-in templates installed by the system. |
| `created_at` | timestamptz | No | Set by the database. |

### `document_requests` — student requests

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `reference_no` | varchar(30) | No | e.g. `REQ-2026-000123`. **Unique**. Shown to the student. |
| `student_id` | integer | No | FK → `students.id`. Indexed. The student the document is about. |
| `document_type` | varchar(10), enum DocumentType | No | `TOR` or `COE`. |
| `purpose` **[PD]** | varchar(200) | No | e.g. "Employment". Printed on the COE. |
| `copies` | integer | No | 1–10 (checked by the API). Default 1. |
| `remarks` **[PD]** | text | Yes | Free text from the requester. Not printed. |
| `status` | varchar(20), enum RequestStatus | No | Lifecycle (Section 14, approve-first order S-11): `REQUESTED` → (registrar approves the request; records retrieved, document generated) `APPROVED` → `FOR_VERIFICATION` → (registrar verifies) `VERIFIED` (manual release only) → `RELEASED` → `EXPIRED` or `REVOKED`, with `REJECTED` and `CANCELLED` as end states. A student may have only one open (`REQUESTED`, `APPROVED`, `FOR_VERIFICATION`, `VERIFIED`) request per document type. `EXPIRED` only closes the download window. The document can still be validated by scan. |
| `payment_confirmed` | boolean | No | Ticked by the registrar when `ADGVS_PAYMENT_REQUIRED` is on. The system does not handle payments. Default false. |
| `requested_by` | integer | Yes | FK → `users.id`. The student, or the registrar filing for a walk-in student. |
| `requested_at` | timestamptz | No | Set by the database. |
| `reviewed_by` | integer | Yes | FK → `users.id`. The registrar who approved or rejected the request. |
| `reject_reason` | text | Yes | Why it was rejected. Either the registrar's reason (required) or "Not eligible: …" from the automatic eligibility check. Shown to the student. |
| `processing_note` | text | Yes | Why generation is blocked (missing data, no active template). While this is set, the request waits in `APPROVED` and is counted as blocked on the registrar dashboard. |
| `updated_at` | timestamptz | No | Set by the database on insert and on every update. |

### `generated_documents` — each issued document file

One row per generated PDF. A regeneration creates a **new row with a new document number**. The previous unreleased file is marked `REVOKED` (voided) so it can never become official.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `document_number` | varchar(30) | No | e.g. `TOR-2026-000123`. **Unique** (index). Printed in the header and in the footer of every page. |
| `request_id` | integer | No | FK → `document_requests.id`. Indexed. |
| `template_id` | integer | No | FK → `document_templates.id`. The exact template version used, so the scanner uses that version's zones. |
| `data_snapshot` **[PD]** | jsonb | No | **Exactly what was printed**, as the printed strings. Keys: `student_name` (e.g. `JUAN D. DELA CRUZ JR.`), `student_number`, `program`, `date_issued`, `document_type`, `document_number`. A TOR adds `gwa` and `terms` (a list with `semester`, `academic_year`, `gwa` and `subjects`, each subject having `subject_code`, `subject_title`, `units`, `grade` and `remarks`). A COE adds `year_level`, `semester`, `academic_year` and `purpose`. Scans are compared against this. **It is never updated after approval.** No code path in the application writes to it after the row is created. If records are corrected, the document is regenerated under a new number. |
| `data_hash` | varchar(64) | No | SHA-256 (hex) of `data_snapshot` in canonical JSON. The first 12 characters are printed in the footer as `Ref …`. A footer reference that does not match sends a scan to NEEDS REVIEW. |
| `qr_token` | varchar(64) | No | Random token (`secrets.token_urlsafe`, 32 characters) encoded in the QR code as `ADGVS1:<token>`. **Unique** (index). It contains no personal data. The scanner looks documents up by it. |
| `pdf_path` | varchar(500) | Yes | Path of the PDF relative to the protected storage folder (`ADGVS_STORAGE_DIR`), e.g. `documents/TOR-2026-000123.pdf`. The file is only served through an authorized, short-lived download link. |
| `page_count` | integer | No | Pages in the issued PDF. If a scan has a different number of pages, the result is INVALID. Default 1. |
| `status` | varchar(20), enum GeneratedDocumentStatus | No | `GENERATED` → `APPROVED` → `RELEASED`, or `REVOKED`. Only a `RELEASED` document can be downloaded by the student or validated as VALID. A `REVOKED` document is INVALID at every later scan. |
| `approved_by` | integer | Yes | FK → `users.id`. The registrar who approved it. |
| `released_at` | timestamptz | Yes | When it was released. |
| `download_expires_at` | timestamptz | Yes | End of the student's download window (default 30 days after release, `ADGVS_DOWNLOAD_WINDOW_DAYS`). The registrar can re-open the window. This date does not affect validation. |
| `revoked_reason` | text | Yes | Why it was revoked or voided. Required when the registrar revokes. |
| `created_at` | timestamptz | No | Set by the database. Generation time. |

---

## 4. Validation (scanning)

### `scan_stations` — registered scan station PCs

Each station signs in with its own API key, not with a user account. The key is shown once when an admin registers the station. Only its hash is stored.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `name` | varchar(100) | No | e.g. "Registrar window 1". **Unique**. |
| `api_key_hash` | varchar(64) | No | SHA-256 of the station's API key. **Unique**. |
| `is_active` | boolean | No | False after an admin deactivates the station. Its key then stops working. Default true. |
| `registered_by` | integer | Yes | FK → `users.id` (the admin). |
| `created_at` | timestamptz | No | Set by the database. |
| `last_status` | varchar(20) | Yes | Last scanner state the station reported: `ready`, `scanning`, `jam`, `double_feed` or `offline` (checked by the API). Shown on the operator dashboard. |
| `last_status_detail` | text | Yes | Free-text detail from the station, e.g. "Clear and re-feed from sheet 4". |
| `last_seen_at` | timestamptz | Yes | When the station last reported its status. |

### `scan_sessions` — one single or bulk scanning run

An operator opens a session on the dashboard. Opening a new session on a station closes any session still open there.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `mode` | varchar(10), enum ScanMode | No | `SINGLE` or `BULK`. Also tells the operator which output tray to use. |
| `operator_id` | integer | No | FK → `users.id`. The registrar with validation permission who opened the session. |
| `station_id` | integer | Yes | FK → `scan_stations.id`. |
| `started_at` | timestamptz | No | Set by the database. |
| `ended_at` | timestamptz | Yes | Empty while the session is open. A station can only submit to an open session. |
| `total_docs` | integer | No | Documents submitted in this session. Default 0. |

### `scanned_documents` — one physical document as scanned

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `session_id` | integer | No | FK → `scan_sessions.id`. Indexed. |
| `position_in_stack` | integer | Yes | 1-based position in the scanned stack, so flagged paper can be found in the bulk tray. |
| `image_paths` **[PD]** | jsonb | No | List of page-image paths relative to the storage folder, e.g. `["scans/12/345/page-1.png", ...]`. The images show everything printed on the paper. They are served only to validation operators. Default `[]`. |
| `matched_document_id` | integer | Yes | FK → `generated_documents.id`. The issued document this paper claims to be, if found. Empty for papers the system never issued. The number of scans per document is counted from this column. |
| `extracted_fields` **[PD]** | jsonb | Yes | What the scan agent read off the paper, unchanged: `position`, `identity_source` (`qr`, `ocr` or `none`; stored for diagnosis only, because the backend decides how the paper was identified from `qr_token` and `document_number_read`), `qr_token`, `document_number_read`, `lookup_document_number`, `pages` (per page: `sequence`, `skew`, `qr_present`, and `footer` with `document_number`, `page`, `pages`, `ref` and `confidence`), `fields` (per field: `value` and `confidence` 0–100) and `table` (the grade rows read, and the term GWAs). |
| `ocr_confidence` | double precision | Yes | Lowest per-field OCR confidence used in the decision (0–100). |
| `created_at` | timestamptz | No | Set by the database. Scan time. |

### `validation_records` — the result for each scanned document

One per scanned document (`scanned_document_id` is **unique**). The backend decides the result with the rules in `backend/app/services/validation.py` (Section 15). VALID means "the printed information matches official records". It does not mean the paper is authentic.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `scanned_document_id` | integer | No | FK → `scanned_documents.id`, **unique**. |
| `result` | varchar(20), enum ValidationResult | No | The automatic result: `VALID`, `INVALID` or `NEEDS_REVIEW`. **It is never overwritten**, even after a review. |
| `field_results` **[PD]** | jsonb | No | One entry per compared field: `field`, `printed` (read from paper), `official` (from the snapshot), `status` (`match`, `mismatch`, `similar`, `uncertain` or `missing`), `confidence`, `critical` (true/false), and `zone` (page and mm box of a table row, when known). Grade rows are named like `grade IT101 (1st Semester, AY 2025-2026)`. Default `[]`. |
| `reasons` | jsonb | No | List of plain-language reasons, e.g. `["Grade for IT101 does not match official records"]`. The first is shown as the top reason. Default `[]`. |
| `final_result` | varchar(20), enum ValidationResult | Yes | The registrar's decision on a `NEEDS_REVIEW` result: `VALID` or `INVALID`. Empty until reviewed. It can be set only once. |
| `reviewed_by` | integer | Yes | FK → `users.id`. |
| `review_remarks` | text | Yes | The reviewer's remarks. Required when reviewing. |
| `created_at` | timestamptz | No | Set by the database. |

---

## 4b. Incoming enrollment documents (Part I)

The screening columns of `intake_documents`, `intake_batches` and `intake_settings` are described in [intake/screening-contract.md](intake/screening-contract.md). The student-data step (S-06/S-07) added these columns to `intake_documents`:

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `reviewed_fields` **[PD]** | jsonb | Yes | The field set staff reviewed and saved: `{"values": {key: text or null}, "changed": [keys corrected compared with the station's reading]}`. The station's own reading stays unchanged in `screening_result.fields`. Empty until the fields are reviewed. |
| `fields_reviewed_by` | integer | Yes | FK → `users.id`. Who last saved the reviewed fields. |
| `fields_reviewed_at` | timestamptz | Yes | When. |
| `student_id` | integer | Yes | FK → `students.id`. Indexed. The student the document belongs to; a student can have many documents. Set when staff link the document (or create the student from it). |
| `linked_by` | integer | Yes | FK → `users.id`. Who linked it. |
| `linked_at` | timestamptz | Yes | When. Cleared when the document is unlinked. |

Page images: `<ADGVS_STORAGE_DIR>/intake/<station_id>/<intake_id>.jpg`, served only by `GET /api/intake/documents/{id}/image` (registrar, admin).

---

## 5. Audit

### `audit_logs` — append-only activity log

Who did what, when and from where (Section 17). The backend writes a row in the same database transaction as the action it records, so the action and its log entry are saved together or not at all.

**Append-only:**
- On PostgreSQL, the trigger `audit_logs_append_only` runs before every UPDATE or DELETE on this table and raises the error "audit_logs is append-only". This applies to every database user, including the table owner. Getting around it means dropping or disabling the trigger, which only the table owner or a superuser can do.
- The API connects with the least-privilege login from `setup-db-roles` (no DDL), which has INSERT and SELECT but no UPDATE, DELETE or TRUNCATE rights on the table.
- The application has no route that edits or deletes log entries.

| Column | Type | Null? | Meaning |
| --- | --- | --- | --- |
| `timestamp` | timestamptz | No | Set by the database. Indexed. |
| `user_id` | integer | Yes | FK → `users.id`. Indexed. The person who acted. Empty for system actions, e.g. automatic rejection, scheduled expiry, an unknown-email login attempt, or an admin created from the server console. For scans, this is the operator who opened the session. |
| `action` | varchar(60) | No | Event name. Indexed. Examples: `login_success`, `login_failed`, `account_locked`, `password_changed`, `password_reset`, `user_created`, `user_deactivated`, `student_created`, `student_updated`, `students_imported`, `grade_encoded`, `grade_edited`, `grades_submitted`, `term_locked`, `grade_change_requested`, `grade_change_approved`, `grade_change_rejected`, `request_submitted`, `eligibility_checked`, `payment_confirmed`, `request_approved`, `request_rejected`, `generation_blocked`, `document_generated`, `document_verified` (`document_approved` before S-11), `document_released`, `request_status_migrated`, `section_adviser_assigned`, `document_downloaded`, `document_revoked`, `document_voided`, `download_reopened`, `request_expired`, `station_registered`, `scan_session_started`, `scan_session_ended`, `document_scanned`, `validation_reviewed`, and for incoming documents `intake_document_screened`, `intake_document_reviewed`, `intake_fields_reviewed`, `intake_document_linked`, `intake_document_unlinked` (these record field names, never field values). |
| `entity_type` | varchar(40) | Yes | Kind of record affected, e.g. `student`, `grade`, `document_request`, `generated_document`, `scanned_document`, `scan_session`, `user`. |
| `entity_id` | varchar(40) | Yes | The `id` of that record, as text. |
| `old_value` **[PD]** | jsonb | Yes | Values before the change, where relevant. For example `{"grade": "2.25"}` or the student fields changed by an edit. |
| `new_value` **[PD]** | jsonb | Yes | Values after the change, plus context. This includes the **reason** for registrar changes, eligibility reasons, document numbers, scan results and review remarks. When a student is created, it holds the full profile, including birthdate and contact details. When someone tries to sign in with an unknown email, it holds the email they typed. |
| `ip_address` **[PD]** | varchar(45) | Yes | Client IP address as seen by Nginx. Nginx replaces any `X-Forwarded-For` header sent by the client with the real connecting address (`deploy/nginx/templates/default.conf.template`), so a client cannot write a false IP into the log. Behind a campus NAT, this is the NAT's address. Empty for console and scheduled actions, and for scan results submitted by a station. |

**Who can read it:** registrars and admins, through the audit trail search (`GET /api/audit-logs`, screen "Audit trail"), which returns every column including `old_value`, `new_value` and `ip_address`. Registrars also see a per-document timeline on the request review screen. Students and teachers have no access.

Retention is not automated. The specification (Section 17) asks for logs to be kept at least for the pilot. The school sets the long-term period.

---

## Relationships at a glance

```
users 1─0..1 students ─* enrollments *─1 class_sections *─1 subjects
users 1─0..1 teachers 1─* class_sections *─1 terms
enrollments 1─0..1 grades 1─* grade_change_requests
students 1─* document_requests 1─* generated_documents *─1 document_templates
students 1─* intake_documents *─0..1 intake_batches   (student_id, optional until linked)
scan_stations 1─* scan_sessions 1─* scanned_documents 1─1 validation_records
generated_documents 1─* scanned_documents   (matched_document_id, optional)
users 1─* user_sessions, audit_logs, and the *_by columns above
```

## Files outside the database

| What | Where | Notes |
| --- | --- | --- |
| Issued PDFs | `<ADGVS_STORAGE_DIR>/documents/<document_number>.pdf` | Outside any public web folder. Served only by `GET /api/files/{token}` after a signed link that expires in 300 s and is tied to one user. |
| Scan page images | `<ADGVS_STORAGE_DIR>/scans/<session_id>/<scanned_document_id>/page-N.png` (or `.jpg`) | Served only to validation operators. |

In the Docker setup, `ADGVS_STORAGE_DIR` is `/data/storage` on the `storage` volume. The `backup` service backs up this volume together with the database every day (`deploy/backup/backup.sh`). A database restored without these files cannot show PDFs or scan images.
