# Applicant intake: documents from another school, uploaded online

Applicants are people who are not students yet, such as transferees. They send their
**Transcript of Records (TOR)** or **Certificate of Enrollment (COE)** from their previous
school online. The registrar reviews each document and, if accepted, turns the applicant into a
student.

The feature reuses the incoming-document screening flow ([screening contract](intake/screening-contract.md)):
once an upload has been read, it becomes an **intake document with source `online`**. From
there it goes through the same staff review, field review and student link as a paper document
from the intake station. There is no second review system.

Wording is the same as for screening: **passed screening** / **needs manual review**. Neither
means a document is authentic; staff make the decision.

## Status

**Phase 1** covers applicant logins, uploads, the OCR reading, staff review and the student link.

| Part | State |
|---|---|
| Backend: API, rules, migration `a9d4e2c7b1f5`, tests (`tests/test_applicant_intake.py`) | done |
| OCR worker on the station (`scan_agent/applicant_worker.py`, `adgvs-applicant-worker.service`, tests `scan-agent/tests/test_applicant_worker.py`) | done |
| Web app: applicant "My documents" page; staff "Applicants" page (logins, uploads, queue again); online filter and all pages in Incoming documents | done (no Playwright test yet) |

**Phase 2** is a separate decision. It covers reference layouts for each school (which would
let an upload pass screening), a second OCR pass and per-submission exports.

## The flow

1. **Login.** A registrar or administrator creates the applicant's login
   (`POST /api/applicants`). There is no public sign-up. The applicant changes the temporary
   password at first login.
2. **Upload.** The applicant chooses TOR or COE, names the issuing school and agrees to the
   privacy notice, which records `consent_at`. They then upload the pages and submit:
   - **File types:** a PNG or JPEG page, or a PDF, which the backend splits into page images.
   - **Size:** each upload is one request under 4 MB, because the Vercel proxy refuses request
     bodies over 4.5 MB.
   - **Limits:** at most 12 pages per submission, and at most 5 unfinished submissions per
     applicant.
   - **Duplicates:** the same file can't be uploaded twice to one submission.
   - **Withdrawing:** before the document is read, the applicant can withdraw it. The pages
     are deleted.
3. **Reading.** The OCR worker runs on the station and authenticates with the station key:
   - It **claims** the oldest waiting submission with a 10-minute lease.
   - It downloads the pages with the lease token and runs OCR and TOR/COE/unknown classification.
   - It sends back a reading: document type and confidence, fields and confidence, and page
     quality.
   - **Leases:** a worker that stops loses its lease, and the next claim takes the submission
     over.
   - **Errors:** an error is retried up to 3 times. After that the submission is **failed**,
     and staff can queue it again with a reason.
4. **Decision.** The backend decides with `services/applicant_intake.decide`, a pure,
   unit-tested function. There are no reference layouts for other schools yet, so every upload
   **needs manual review** (`no_reference_profile`). The other rules still run, so staff see
   what else to look at:
   - `upload_quality_not_ok`
   - `doc_type_unknown`
   - `declared_type_mismatch`
   - `doc_type_low_confidence`

   Every code has plain-language text in `services/intake.REASON_TEXT`.
5. **Staff review.** This is the existing intake queue (`GET /api/intake/documents?source=online`):
   - **Decide:** accept (a reason is required, because the upload is flagged), reject, or ask
     for a resubmission.
   - **Fields:** review the TOR/COE fields. The other school's student number is kept as a
     source reference only.
   - **Student:** link the document to an existing student, or create a new student with a
     student number from this school.
6. **Login hand-over.** When an online document is linked to a student who has no login, the
   **applicant's login becomes that student's login**. This is audited as
   `applicant_login_became_student`. If the student already has a login, the applicant login
   stays as it is, and staff decide what to do with it.

The applicant sees one plain status per submission:
- waiting to be read
- being read
- waiting for the registrar's review
- accepted
- please submit again
- not accepted

They never see staff notes, the reasons or the reading.

## Rules

- **Access:** role checks are enforced on the server. `tests/test_access_matrix.py` pins every
  route:
  - Applicants reach only their own submissions; another applicant's submission answers 404.
  - The worker routes need a station key.
- **Audit:** every step is audited. Audit entries never carry field values or document text;
  `tests/test_applicant_intake.py` checks this.
- **Storage:** pages stay in private storage and are only streamed by the API. Storage URLs
  never reach the browser.
- **Station screen:** online documents stay out of the station operator's console. They never
  went through its trays.
- **Supabase:** the new tables are locked down (RLS on, Data API roles revoked) by their
  migration.

## Running the worker

The worker reads submissions from the API. In the cloud setup it talks to Render directly,
like the scan station, because page downloads are too large for the Vercel proxy.

On the Pi, `pi-setup.sh` installs the unit but doesn't enable it. The worker uses the
station's settings file, `/etc/adgvs/station.env`: `ADGVS_SERVER` (the Render API address in
the cloud setup), `ADGVS_STATION_KEY` and `TESSERACT_CMD`.

```bash
sudo systemctl enable --now adgvs-applicant-worker
journalctl -u adgvs-applicant-worker -f
```

On any machine with the scan-agent installed:

```bash
python -m scan_agent.applicant_worker --server https://adgvs-api.onrender.com --key <station key>
python -m scan_agent.applicant_worker --once ...   # one job, then exit
```

How it reads a submission:
- It runs Tesseract on each page locally, and nothing is sent to a cloud service.
- A small Naive Bayes model, trained on a synthetic keyword corpus, classifies the document as
  TOR, COE or unknown. Its confidence feeds the backend's low-confidence rule.
- It finds fields by their printed labels: name, student number, program, school year, date
  issued. The school name comes from the letterhead.
- A page with too little readable text sets `quality.ok = false`.

Without a running worker, submissions stay queued; nothing is lost.

## Decisions for the school

These are open:
- Who may create applicant logins: the registrar only, or admissions as well?
- Which document types to accept besides the TOR and COE.
- The wording of the privacy notice shown at upload.
- How long rejected and withdrawn submissions are kept. See the
  [privacy data inventory](privacy-data-inventory.md), section 7.
