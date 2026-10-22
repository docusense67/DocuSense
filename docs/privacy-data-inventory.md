# Data flow and personal-data inventory

Academic Document Generation & Validation System. Prepared 2026-10-05 for the school's Data Protection Officer (DPO), registrar and IT staff.

This is the deliverable named in the MVP specification, Section 18 ("a short data-flow description and list of personal-data fields to help the DPO") and Section 26 ("Data-flow and personal-data inventory to support the DPO").

**What this document is not.** It does not say the system is compliant with the Data Privacy Act of 2012 (RA 10173), and the system should not be described that way. The system provides technical controls (access by role, audit trail, encrypted transport, minimal data in the QR code) that **support** the school's own compliance. Compliance is the school's responsibility: lawful basis, privacy notice, retention policy, registration with the National Privacy Commission (NPC), and breach procedures. Section 7 lists the decisions the DPO needs to make.

Every statement below was checked against the code on 2026-10-05 (main at commit `4469041`). File references point to the code that does what is described. The table definitions are in [data-dictionary.md](data-dictionary.md).

---

## 1. Systems and where data lives

| Component | Runs on | Holds personal data? |
| --- | --- | --- |
| Web app (React) | User's browser | Only what the signed-in user is allowed to see, while the page is open. The API sends `Cache-Control: no-store` on every `/api/` response so browsers do not cache it (`backend/app/main.py`). |
| Nginx (container `web`) | School server | Terminates HTTPS and serves the web app. Passes API traffic through. Stores no records. |
| Backend API (FastAPI) | School server (Docker container `api`) | Processes everything. Stores nothing outside the database and the storage folder. Not published to the network: only Nginx reaches it. |
| PostgreSQL 16 | Same server (container `db`) | All records (Section 4). Not published to the network (`docker-compose.yml`). |
| Protected file storage | Same server, Docker volume `storage` (`/data/storage`) | Issued PDFs and scan page images. Not inside any public web folder. |
| Backups (container `backup`) | `BACKUP_DIR` on the server, which should be a disk or NAS that is not the system disk | Daily copy of the whole database and the storage volume, encrypted (see Section 6). |
| Scan agent | Scan station mini PC at the registrar's office | Page images in memory while it processes them. See Section 3. |

## 2. Data flows

```
 Student / Teacher / Registrar / Admin browser
        │  HTTPS (TLS), session cookie: httpOnly, Secure, SameSite=Strict
        ▼
 Nginx reverse proxy (terminates HTTPS)
        │
        ▼
 Backend API ──► PostgreSQL (records, snapshots, audit log)
        │   └──► File storage (PDFs, scan images)
        ▲
        │  HTTPS, header X-Station-Key (the station's own API key)
 Scan station (scan agent) ◄── USB ── ADF scanner
```

**Deployment.** `docker-compose.yml` runs Nginx as the only service published to the network (ports 80 and 443). Port 80 only redirects to HTTPS. TLS 1.2 and 1.3 only (`deploy/nginx/templates/default.conf.template`). The scan agent connects over HTTPS too. For a campus or self-signed certificate it verifies the server against the PEM file given with `--ca-cert` (`scan-agent/scan_agent/client.py`). The school supplies the certificate (`deploy/certs/`).

**Client IP addresses.** Nginx **replaces** any `X-Forwarded-For` header the client sends with the address of the connecting client (it does not append to it). The API records that address in `user_sessions` and `audit_logs`. A client therefore cannot write a false IP into the audit log. If the whole campus reaches the server through one NAT address, the log shows that NAT address.

### 2.1 Browser users

1. **Sign-in.** The user sends email and password. The server checks the password against an Argon2 hash, creates a random session token, stores only the token's SHA-256 hash in `user_sessions` along with the client IP address, and sets the token in an httpOnly, Secure, SameSite=Strict cookie (`backend/app/api/auth.py`). Sessions end after 30 minutes idle, on logout, or on password change or reset. Five failed sign-ins in a row lock the account for 15 minutes, or until a registrar or admin resets the password. The sign-in form gives the same answer for an unknown email, a wrong password, a locked account and a deactivated account, so it does not reveal whether an account exists. Writes sent from other websites are refused (`backend/app/main.py`, `same_origin_writes`).
2. **Records entry.** The registrar types in or imports (CSV/XLSX, up to 2 MB) student profiles, subjects, sections and enrollments. Teachers type in or import grades for their own sections. Every change is written to `audit_logs` in the same transaction.
3. **Request and generation.** A student (or the registrar, for a walk-in student) submits a request. The server checks eligibility, fills the active template from the database, renders a PDF, and saves the **snapshot** (exactly what was printed), its SHA-256 hash and a random QR token. The PDF goes to `storage/documents/` (`backend/app/documents/service.py`).
4. **Approval and download.** The registrar reviews the PDF next to the current records and approves or rejects it. To download a released PDF, a user first gets a signed link that is tied to that user and expires after 300 seconds (`backend/app/api/documents.py`). Every download is logged. The student sees the dates and times of their **own** downloads of each document. The registrar sees all downloads (`backend/app/api/requests.py`, `_downloads`).
5. **Dashboards.** Aggregates computed on request. Nothing extra is stored.

### 2.2 Scan station

1. An admin registers the station once (`POST /api/stations`). The API key is shown once, and only its SHA-256 hash is stored (`backend/app/api/stations.py`).
2. An operator (registrar account with validation permission) opens a Single or Bulk session on the dashboard.
3. The scan agent reads the pages, cleans them up, decodes the QR code and reads the page footers. It then asks the server for the **template zones and page count** of that document (`POST /api/station/lookup`). The lookup answer contains `found`, `document_number`, `document_type`, `page_count` and `field_zones`. It contains **no student data** (`backend/app/api/scanning.py`; tested by `test_station_lookup_returns_zones_but_no_student_data`).
4. The agent reads the printed fields with OCR and posts the values it read, their confidence scores and the page images (PNG) to the server over HTTPS (`scan-agent/scan_agent/client.py`).
5. The **server** compares the values with the snapshot and current records, decides VALID / INVALID / NEEDS REVIEW, stores the images under `storage/scans/<session>/<scan>/`, and writes the result and an audit entry (`backend/app/services/scanning.py`). The station receives only the result and the reasons.

## 3. What the scan station keeps locally

What the code does today (`scan-agent/scan_agent/`):

- **Images from the scanner are held in memory only.** The agent reads each page, processes it, encodes it to PNG in memory and uploads it. It writes no image, temporary file or log file to disk.
- **File-drop mode (in use until the scanner driver is added).** The agent reads images or PDFs from a folder the operator chooses (`scan_agent/capture/filedrop.py`). This also covers the scanner's own software saving PDFs to a folder for the agent to pick up. With the option **`--delete-after-upload`**, the agent deletes every input file it read, once all documents in the batch have been uploaded (`FolderSource.delete_consumed`, called from `scan_agent/__main__.py`). Files are not deleted if the run stops early, for example on a jam, so the batch can be resumed. **Without the option, the files stay in the folder.** Tested by `scan-agent/tests/test_filedrop.py`.
- **Scanner driver (TWAIN/SANE), not yet implemented.** It will be added in `scan_agent/capture/`. Whether the driver or the vendor software writes temporary files has to be checked when it is added.
- **Console output.** The agent prints each document's position, the document number it read, and the result. It does not print names or grades.
- **Credentials.** The station API key is passed on the command line (`--key`) or in the environment variable `ADGVS_STATION_KEY`. Treat it like a password.

**Requirement for the production station (Section 18, "scan images deleted from the station after upload").** The production station **must run the agent with `--delete-after-upload`** whenever it uses file-drop mode. Set it in the station's start-up script so it does not depend on the operator. After a run that stopped early (jam or error), the operator either resumes it (`--start-sequence`) or clears the drop folder by hand.

## 4. Personal-data inventory

**Roles used below:**

- **Student**: the student's own record only.
- **Teacher**: only students enrolled (not dropped) in sections assigned to that teacher.
- **Registrar**: all records.
- **Operator**: a registrar account with the validation permission (`can_validate`).
- **Admin**: system administrator.
- **Station**: the scan agent's API key.
- **DB/IT only**: no API route returns the field. It can be read only with direct database or server access.

Access is enforced on the server on every route (`backend/app/api/deps.py`: `require_roles`, `require_validator`, `ensure_section_access`, `get_station`). A student asking for another student's request gets "not found" (`backend/app/api/requests.py`).

**Retention:** the code deletes nothing automatically except idle login sessions (daily) and backup sets older than 30 days. Unless noted, retention is **set by the school; deletion is manual in the MVP** (Section 18: automatic deletion is Phase 2).

### 4.1 Identity and contact

| Field | Table | Whose data | Purpose | Who can see it | Printed on | Retention |
| --- | --- | --- | --- | --- | --- | --- |
| `email` (login) | `users` | Students, teachers, staff | Sign-in name | The user (own, `/api/auth/me`). Registrar (student and teacher accounts only). Admin (all accounts). Registrar and Admin also see the email of the person behind each audit event (dashboard and audit trail search). | — | Set by the school; manual deletion in MVP. Accounts are deactivated, not deleted. |
| `password_hash` | `users` | All users | Verify sign-in | No one (never returned by the API) | — | As long as the account exists |
| `ip_address` | `user_sessions` | All users | Session security | DB/IT only | — | Row deleted at logout, password change or reset, or deactivation. Sessions idle for more than 30 minutes are deleted by the daily job (`python -m app.jobs daily`). |
| `student_number` | `students` | Students | Unique ID on official documents | Student (own). Teacher (own sections' class lists). Registrar. Admin (dashboard "data health" list). Operator (in the field comparison). | TOR, COE, header | Set by the school; manual deletion in MVP |
| `first_name`, `last_name`, `suffix` | `students` | Students | Name on documents | Student (own). Teacher ("Last, First" in own sections' class lists). Registrar. Admin ("Last, First" in the dashboard approval queue and data-health list). Operator (printed name in the field comparison). | TOR, COE, formatted as `JUAN D. DELA CRUZ JR.` | Set by the school; manual deletion in MVP |
| `middle_name` | `students` | Students | Name on documents | Student (own). Registrar. | Middle **initial** only, TOR and COE | Set by the school; manual deletion in MVP |
| `birthdate` | `students` | Students | Official master record (Section 5B) | Student (own). Registrar. | Not printed | Set by the school; manual deletion in MVP |
| `sex` | `students` | Students | Official master record | Student (own). Registrar. | Not printed | Set by the school; manual deletion in MVP |
| `contact_email`, `contact_phone` | `students` | Students | Contacting the student | Student (own). Registrar. | Not printed | Set by the school; manual deletion in MVP |
| `lrn`, `birth_place`, `mother_name`, `father_name` | `students` | Students and their parents | Enrollment record from the documents submitted (S-07) | Student (own). Registrar. Registrar and Admin also see them as candidate matches and reviewed fields on an incoming document. | Not printed | Set by the school; manual deletion in MVP |
| `reviewed_fields` (names, birth date and place, parents, LRN, school data) | `intake_documents` | Applicants and their parents | Staff-checked copy of what the intake station read (S-06); the station's reading stays in `screening_result` | Registrar, Admin | — | Set by the school; manual deletion in MVP. Never copied into the audit log (field names only). |
| `admission_date`, `status`, `year_level`, `program_id` | `students` | Students | Eligibility and document content | Student (own). Registrar. | Program name: TOR and COE. Year level: COE. | Set by the school; manual deletion in MVP |
| `employee_number`, `full_name`, `department` | `teachers` | Teachers | Faculty profile, section assignment | Registrar | Not printed | Set by the school; manual deletion in MVP |

### 4.2 Academic records

| Field | Table | Whose data | Purpose | Who can see it | Printed on | Retention |
| --- | --- | --- | --- | --- | --- | --- |
| Enrollment (student ↔ section, status) | `enrollments` | Students | Class lists, eligibility, COE | Student (own, through records). Teacher (own sections). Registrar. | COE (current term) | Set by the school; manual deletion in MVP |
| `grade_value`, `remarks` | `grades` | Students | Official grades | Student (own records). Teacher (own sections). Registrar. | TOR grades table (grade and remarks per subject, term GWA, cumulative GWA) | Set by the school; manual deletion in MVP |
| Computed GWA | not stored (computed) | Students | TOR | Student (own). Registrar. | TOR | — |
| `old_value`, `new_value`, `reason` | `grade_change_requests` | Students (grades). Requester (reason). | Corrections after lock | Teacher (requests they filed). Registrar (all). | — | Set by the school; manual deletion in MVP |

### 4.3 Requests and issued documents

| Field | Table | Whose data | Purpose | Who can see it | Printed on | Retention |
| --- | --- | --- | --- | --- | --- | --- |
| `purpose`, `remarks`, `copies`, status, dates | `document_requests` | Students | Handle the request | Student (own). Registrar. | Purpose: COE | Set by the school; manual deletion in MVP |
| `reject_reason`, `processing_note` | `document_requests` | Students | Tell the student or registrar why it stopped | Student (own, reject reason and note). Registrar. | — | Set by the school; manual deletion in MVP |
| `data_snapshot` | `generated_documents` | Students | The exact printed values, used for validation. Never changed after approval. | Student (own request detail). Registrar (request and document detail). Operator (student name only, in the validation detail). | It **is** the printed content | Set by the school; manual deletion in MVP. Deleting it makes the document impossible to validate. |
| `data_hash` | `generated_documents` | Derived from the snapshot | Detect changes. First 12 characters printed. | Registrar | Footer `Ref xxxxxxxxxxxx` on every page | Same as the snapshot |
| `qr_token` | `generated_documents` | Not personal data (random) | Look up the document at scan time | Not shown in the web app | QR code | Same as the snapshot |
| Issued PDF file | storage `documents/` | Students | The document | Student (own, released, within the 30-day download window). Registrar (any, including unreleased ones for review). | — | Set by the school; manual deletion in MVP |
| Download history (times a document was downloaded) | `audit_logs` (`document_downloaded`) | Students | Show the student when they downloaded; accountability | Student (**own downloads only**, on their requests). Registrar (all downloads). | — | As the audit log |

### 4.4 Scanning and validation

| Field | Table | Whose data | Purpose | Who can see it | Printed on | Retention |
| --- | --- | --- | --- | --- | --- | --- |
| Scan page images | storage `scans/`, paths in `scanned_documents.image_paths` | Students (everything printed on the paper). Also any third party the paper came from, plus anything handwritten or stamped on it. | Evidence for the result. Shown to the reviewer. | Operator | — | Set by the school; manual deletion in MVP. **Section 18 asks the DPO to set this period.** |
| `extracted_fields` (OCR text: name, student number, grades as read) | `scanned_documents` | Students | Input to the decision | Operator (as "printed" values in the field comparison) | — | Set by the school; manual deletion in MVP |
| `field_results` (printed vs official per field), `reasons` | `validation_records` | Students | The result and why | Operator | — | Set by the school; manual deletion in MVP |
| `review_remarks`, `reviewed_by` | `validation_records` | Reviewer, student | Human decision on NEEDS REVIEW | Operator | — | Set by the school; manual deletion in MVP |
| `operator_id`, `station_id`, times | `scan_sessions` | Operator | Accountability | Operator. The registrar dashboard shows counts only. | — | Set by the school; manual deletion in MVP |
| Scan count per document | computed from `scanned_documents` | Students | Notice repeated use of copies | Operator | — | — |

### 4.5 Audit trail

| Field | Table | Whose data | Purpose | Who can see it | Printed on | Retention |
| --- | --- | --- | --- | --- | --- | --- |
| `user_id`, `action`, `timestamp`, entity | `audit_logs` | The acting user | Accountability (Section 17) | Registrar and Admin: the **Audit trail** search screen (`GET /api/audit-logs`: filter by text, action, record, date range) and the last 20 events on the dashboard. Registrar: the per-document timeline on the request review screen (request, every generation, approvals, downloads, scans and reviews). | — | Append-only (database trigger). Section 17 asks for retention at least through the pilot; long-term retention is set by the school. Rows cannot be deleted through the application. |
| `old_value`, `new_value` | `audit_logs` | Students, teachers, staff | Before/after values and reasons | Registrar and Admin (audit trail search returns them in full). Registrar (timeline shows `new_value`). | — | As above. Note: `student_created` stores the full profile (birthdate, contact details), `student_updated` stores old and new values, and a sign-in attempt with an unknown email stores the email that was typed. |
| `ip_address` | `audit_logs` | All users | Where an action came from. The address Nginx saw (see Section 2). | Registrar and Admin (audit trail search) | — | As above |

## 5. What the QR code holds

The QR code contains `ADGVS1:` followed by a 32-character random token from Python's `secrets` module (`backend/app/documents/identity.py`). It holds no name, student number, grade or other personal data. Scanning it with a phone shows only that string. The token is meaningful only to this system's database, which maps it to the issued document. This is tested by `test_qr_is_svg_data_uri_and_payload_has_no_personal_data`.

Every page also carries a printed footer: `<document number> | Page X of Y | Ref <first 12 characters of the SHA-256 data hash>`.

## 6. Security controls that support the school's compliance

| Control | Where in the code |
| --- | --- |
| Argon2 password hashing. Minimum 10 characters. 15-minute lockout after 5 failures. One answer for every failed sign-in. Forced change on first sign-in and after a reset. Nginx throttles sign-in requests per IP. | `app/security.py`, `app/api/auth.py`, `app/services/accounts.py`, `app/config.py`, `deploy/nginx/templates/default.conf.template` |
| Server-side sessions: 30-minute idle timeout, logout invalidates the session, idle sessions purged daily, httpOnly + Secure + SameSite=Strict cookie. Writes from other websites refused. | `app/api/deps.py`, `app/api/auth.py`, `app/jobs.py`, `app/main.py` |
| HTTPS only (TLS 1.2+), HTTP redirected, HSTS, Content-Security-Policy and other security headers | `deploy/nginx/`, `app/main.py` |
| Role and row-level checks on every route | `app/api/deps.py`. A route-by-route test is in `backend/tests/test_access_matrix.py`. |
| Session tokens, station keys and download links are never stored in plain text. Download links are signed and expire. | `app/security.py`, `app/api/documents.py` |
| PDFs and scan images served only through authorized API calls, with a path check against the storage folder | `app/api/documents.py`, `app/api/scanning.py` |
| Station lookups return zones and page counts only, no student data | `app/api/scanning.py` |
| Append-only audit log: database trigger, plus insert-only rights for the app's database login. The API never connects as the database owner and cannot change the schema. | `alembic/versions/a70bba7fa7db_initial_schema.py`, `app/manage.py setup-db-roles`, `docker-compose.yml` |
| Daily backups of the database and stored files, each **encrypted with GPG (AES-256, symmetric, passphrase `BACKUP_PASSPHRASE`)**, with SHA-256 checksums. Backups older than 30 days are deleted. A restore check (`verify-backup.sh`) restores into a scratch database and compares it with the live one. | `deploy/backup/backup.sh`, `deploy/backup/verify-backup.sh`, `docker-compose.yml` (service `backup`) |
| Scan station deletes file-drop input after upload (`--delete-after-upload`) | `scan-agent/scan_agent/capture/filedrop.py` |
| OCR runs locally with Tesseract. No student data is sent to a cloud service. | `scan-agent/` |
| Template upload refuses scripts and external URLs | `app/documents/templating.py` |

These are technical measures only. They do not by themselves meet the Act's requirements for notice, lawful basis, retention, breach handling or registration.

**Still to be done on site (not something the repository can do):** encryption of the server's disk or volume; pointing `BACKUP_DIR` at storage that is off the server, or copying backups off it (by default backups are written to `./backups` on the same server); keeping `BACKUP_PASSPHRASE` somewhere safe away from the server, since without it the backups cannot be read; installing the school's certificate; and a restore test on the school's server. These are tracked as go-live items in `docs/test-plan.md`.

## 7. Open items for the DPO

These are the points from Section 18 of the specification, written as questions. The development team needs an answer to each before go-live, or a written decision to defer it.

1. **Lawful basis and notice.** Under what lawful basis does the school process student records, grades and birthdates in this system? What privacy notice should appear on the portal (what is collected, why, and how long it is kept), and who will write and approve it?
2. **Sensitive personal information.** Which of the fields in Section 4 does the school classify as sensitive personal information (for example, education records)? Does that classification require extra controls?
3. **Proportionality.** Is it acceptable for operators (registrar staff) to see the student's printed name, the field-by-field comparison and the scan images when validating? If third parties such as employers ever verify documents directly (Phase 2), should they see only a match result and not the student's record?
4. **Consent for verification.** Should students consent to third-party verification of their documents when they request them? If so, where and how should consent be recorded?
5. **Retention.** How long should each of these be kept: scan images, validation records, issued PDFs and snapshots, request records, and audit logs? (Section 17 asks for audit logs at least through the pilot.) Who carries out manual deletion in the MVP, and how is it recorded? Automatic deletion is Phase 2.
6. **Breach response.** Does the school have an incident procedure that covers this system? Who notifies the NPC and affected students, and within what window under current NPC rules? (72 hours is commonly cited; please confirm.)
7. **Registration and PIA.** Does this processing system need to be registered with the National Privacy Commission? Should a Privacy Impact Assessment be done before go-live, and who leads it?
8. **Data residency.** If the server is cloud-hosted rather than on campus, which hosting locations and vendor agreements are acceptable? (Section 28, question 12 is still open.)
9. **Scan station local files.** The station deletes file-drop input after upload when run with `--delete-after-upload` (Section 3). Is that, plus clearing the folder by hand after an interrupted run, enough? Who checks that the station's start-up script uses the option?
10. **Audit-log contents and access.** The audit log keeps before/after values of profile edits (including birthdate and contact details), client IP addresses, and the email typed in failed sign-in attempts. Registrars and admins can search all of it. Is this content and this access acceptable, and for how long should it be kept?
11. **Backup copies.** Where should the off-server backup copy be kept, who holds the backup passphrase, and how long should backups be kept? The default is 30 days. Backups contain all personal data, so deleting a record does not remove it from backups until they age out.
