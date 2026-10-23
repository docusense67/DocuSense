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
means a document is authentic; staff make the decision. Reference samples carry the dataset
labels "known-good sample" and "altered sample": **a match with a reference sample is not proof of
authenticity**, only that no automatic check flagged the upload.

## Status

| Part | State |
|---|---|
| **Phase 1.** Applicant logins, uploads, the OCR reading, staff review and the student link. Backend (migration `a9d4e2c7b1f5`, `tests/test_applicant_intake.py`), OCR worker (`scan_agent/applicant_worker.py`), web app (applicant "My documents", staff "Applicants", online documents in Incoming documents) | done |
| **Phase 2.** Reference samples per school, assessment against them (an upload can pass screening), second OCR pass, per-submission report, staff upload on an applicant's behalf, intake notifications, application history, per-student advisers. Migration `d3f8a6c2e9b4`; tests `tests/test_applicant_assessment.py`, `tests/test_applicant_references_api.py`, `tests/test_applicant_followup.py`, `tests/test_student_advisers.py`, `scan-agent/tests/test_applicant_worker.py` | done (no Playwright test of the new pages yet) |

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

   **Staff upload on the applicant's behalf** (documents brought in person): on the
   Applicants page a registrar or administrator picks an existing, active applicant login, starts
   the submission (confirming the applicant agreed to the privacy notice), adds the pages and
   sends it (`POST /api/applicant-submissions`, `.../pages`, `.../submit`). The submission
   belongs to the applicant; `uploaded_by` and the audit trail record the staff member.
3. **Reading.** The OCR worker runs on the station and authenticates with the station key:
   - It **claims** a job with a 10-minute lease: first readings of uploads (applicants are
     waiting), then second passes, then reference samples.
   - It downloads the pages with the lease token and runs OCR, the TOR/COE/unknown
     classification and the layout measurements.
   - It sends back a reading: document type, confidence, model version and evaluation; fields;
     page quality; and per page the **layout**: positions of text anchors (words such as
     "transcript", "name", "registrar"), median word height and gap, median line gap and line
     count. All are fractions of the page size.
   - **Leases:** a worker that stops loses its lease, and the next claim takes the job over.
   - **Errors:** an error is retried up to 3 times. After that the job is **failed**, and
     staff can queue it again with a reason.
4. **Decision.** The backend decides with `services/applicant_intake.assess_upload`, a pure,
   unit-tested function. It compares the upload with the **latest approved known-good-sample
   training reference of the same school and type** (`services/applicant_references.assess`;
   school names are matched after normalising case, accents, punctuation, "&", a leading
   "the" and a few abbreviations). Without such a reference the upload needs manual review
   (`no_reference_profile`). Every rule is a reason code with plain-language text in
   `services/intake.REASON_TEXT`:

   | Code | Fires when |
   |---|---|
   | `no_reference_profile` | no approved reference for this school and type |
   | `upload_quality_not_ok` | a page could not be read well |
   | `doc_type_unknown` | the document is not a TOR or COE |
   | `declared_type_mismatch` | the document read differs from the type chosen |
   | `doc_type_low_confidence` | the classifier is below the admin's confidence threshold |
   | `reference_field_missing` | a required field of the reference's rules was not found |
   | `layout_not_measured` | the upload (or the reference) has no layout measurements |
   | `page_count_differs` | the page count differs from the reference's |
   | `layout_shifted` | a text anchor moved further than the reference's layout tolerance |
   | `reference_regions_missing` | anchors on the reference were not found on the upload |
   | `text_spacing_differs` | word height, word gap or line gap differs by more than half |
   | `layout_model_review` | the layout model placed the upload with the altered samples, or was not confident (below 75%) |

   When a reference matches and **no rule fires, the upload passed screening**: staff may
   accept it without a reason, and saving its reviewed fields accepts it. The findings (which
   field, which anchor, how far), the reference id and version and the layout model's output are
   stored with the intake document (`screening_result.assessment`) and shown in the review.
5. **Staff review.** This is the existing intake queue (`GET /api/intake/documents?source=online`):
   - **Decide:** accept (a reason is required when the upload needs manual review), reject, or
     ask for a resubmission.
   - **Fields:** review the TOR/COE fields. The other school's student number is kept as a
     source reference only.
   - **Student:** link the document to an existing student, or create a new student with a
     student number from this school (`students.add_student`, master-list rules).
6. **Second OCR pass.** Accepting an online document (explicitly, or by saving its fields)
   queues a **second pass** over the same pages with independent preprocessing (Otsu threshold)
   and segmentation (Tesseract psm 6). Its reading is stored apart from the first pass
   (`applicant_second_passes`), and the review page lists every field where the two passes
   disagree, with both values. The audit log records only the names of those fields.
7. **Login hand-over.** When an online document is linked to a student who has no login, the
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

## References

On **Reference samples** (registrar, administrator) staff upload labelled samples of a
school's documents. A match with a reference sample is not proof of authenticity.

- **Label:** *known-good sample* (a known-good document of that school), *altered sample* (a
  known-altered one; trains the layout model) or *other* (not a TOR/COE; trains the
  classifier's "unknown" class, type `unknown`).
- **Use:** *training* (compared with, extends the classifier, trains the layout model) or
  *held-out* (only evaluates the models; never trains anything).
- **Rules** per reference: `required_fields` (default `student_name`), `field_labels` (the
  school's printed labels for a field, e.g. `student_name: Name of learner`; the worker uses
  them to find fields on that school's uploads) and `layout_tolerance` (how far an anchor may
  move, a fraction of the page, 0.02 to 0.5, default 0.15).
- **Versions and status:** every upload is a new version (numbered per school, type, label and
  use; it may name the version it replaces). The worker reads it (a "reference" job with the
  same lease), then it is **pending**. Staff check the pages and what was read, and
  **approve** it with a reason. Approved versions can't be edited: upload a new version and
  **retire** the old one, with a reason. A reference the worker could not read can be queued
  again. Only approved references are used.
- **Classifier:** the worker's Naive Bayes model starts from a synthetic corpus; approved
  training references extend it and held-out references evaluate it. The model version,
  whether it is still synthetic only, the training reference ids and the evaluation counts and
  accuracy are stored in each reading.
- **Layout model:** a nearest-neighbour model over layout features (page count, line count,
  word height and gap, line gap). It is used only when the school has approved *training*
  samples labelled both known-good sample **and** altered sample for that type. It runs in the
  backend, records its version, training reference ids and held-out evaluation, and its verdict
  is **preliminary**: it can send an upload to manual review, never pass one on its own.

One request per reference upload (a PDF, or the page images in order), under 4 MB in total.

## Report

Each submission has a report for staff: **JSON, CSV or PDF**
(`GET /api/applicant-submissions/{id}/report?format=...`, links on the Applicants page and in the
review). It holds the submission (applicant, uploader, consent, attempts, last error), both OCR
passes, the decision with every reason, the assessment findings, the reference, classifier and
layout model versions, the review decisions (accept/reject reason, field review, student link)
and the history. It contains what was read, so only registrar staff can get it; each export is
audited (format only). The PDF uses the existing renderer (`app/documents/pdf.py`); without
WeasyPrint the API answers 503.

## Notifications

Intake events join the existing bell (derived from the audit log; read markers in
`notification_reads`):

- **Registrar, administrator:** an online upload was read (passed screening / needs manual
  review), could not be read, its second pass disagrees or failed; a reference sample is
  ready to approve or could not be read.
- **Applicant, and the student they became:** your document was read, accepted, please submit
  again, not accepted. Only their own submissions, never staff notes or reasons.

## Application history

A student who came in as an applicant keeps a read-only **Application history** page
(`GET /api/students/me/applications`): their past submissions with the plain status only.

## Advisers

**Advisers** assigns **one teacher-adviser per student per term** (`student_advisers`), with a
reason that is audited (`student_adviser_assigned` / `student_adviser_removed`). Registrars
assign them; administrators may too. Students see **My adviser**; teachers see **My advisees**,
only the students assigned to them (name, number, program, year level; no grades). This is
separate from the class advisers of block sections (S-08, Academic setup), which stay as they
are.

## Rules

- **Access:** role checks are enforced on the server. `tests/test_access_matrix.py` pins every
  route:
  - Applicants reach only their own submissions; another applicant's submission answers 404.
  - Reference samples, staff uploads and reports are registrar/admin only.
  - The worker routes need a station key and the job's lease token.
- **Audit:** every step is audited. Audit entries never carry field values or document text;
  the tests check this.
- **Decisions:** the worker extracts and measures; every decision is a pure function in the
  backend (`services/applicant_intake.py`, `services/applicant_references.py`).
- **Storage:** pages stay in private storage and are only streamed by the API. Storage URLs
  never reach the browser.
- **Station screen:** online documents stay out of the station operator's console. They never
  went through its trays.
- **Supabase:** the new tables are locked down (RLS on, Data API roles revoked, sequences too)
  by their migrations.

## Running the worker

The worker reads jobs from the API. In the cloud setup it talks to Render directly, like the
scan station, because page downloads are too large for the Vercel proxy.

On the Pi, `pi-setup.sh` installs the unit but doesn't enable it. The worker uses the
station's settings file, `/etc/adgvs/station.env`: `ADGVS_SERVER` (the Render API address in
the cloud setup), `ADGVS_STATION_KEY` and `TESSERACT_CMD`. `ADGVS_APPLICANT_JOB_KINDS`
(default `first,second,reference`) limits the job kinds one worker runs.

```bash
sudo systemctl enable --now adgvs-applicant-worker
journalctl -u adgvs-applicant-worker -f
```

On any machine with the scan-agent installed:

```bash
python -m scan_agent.applicant_worker --server https://adgvs-api.onrender.com --key <station key>
python -m scan_agent.applicant_worker --once ...           # one job, then exit
python -m scan_agent.applicant_worker --kinds first ...    # first readings only
```

How it reads a job:
- It runs Tesseract on each page locally, and nothing is sent to a cloud service.
- First readings and reference samples: the text (`ocr_page_text`), the layout from
  Tesseract's word boxes (psm 3), the classification and the fields (with the school's printed
  labels when its reference has them). The school name comes from the letterhead.
- Second pass: Otsu threshold and psm 6 on the same pages, independent of the first pass.
- A page with too little readable text sets `quality.ok = false`.

A worker from phase 1 (without `kinds`) keeps getting first readings only. Without a running
worker, jobs stay queued; nothing is lost.

## Design notes

- Phase 2 reuses the existing tables wherever one exists: online documents are
  `intake_documents` (the field review in `reviewed_fields` replaces separate "profile
  drafts"), the student link is the existing link/create-student flow, notification read
  markers are the existing `notification_reads`. New tables only where nothing equivalent
  existed: `applicant_references`, `applicant_second_passes` and `student_advisers` (named
  apart from `section_advisers`).
- The lease of every job kind is the same mechanism (`services/applicant_jobs.py`): status,
  attempts and a hashed lease token on the row being read.
- The worker sends measurements; the comparison with the reference and the layout model are
  backend functions, so the decision can be tested and changed without touching the station.

## Decisions for the school

These are open:
- Who may create applicant logins: the registrar only, or admissions as well?
- Which document types to accept besides the TOR and COE.
- The wording of the privacy notice shown at upload, and of the consent staff confirm when they
  upload on an applicant's behalf.
- How long rejected and withdrawn submissions, retired reference samples and second-pass
  readings are kept. See the [privacy data inventory](privacy-data-inventory.md), sections
  4.4.1 and 7.
- Where the real reference samples come from (each issuing school's own documents, with its
  permission), how many per school before an upload may pass screening, and who signs off
  their labels.
- The default layout tolerance (0.15), the spacing tolerance (50%) and the layout model's
  minimum confidence (75%): measured on real samples before they are relied on. No accuracy
  is claimed for real documents until labelled real samples have been evaluated.
- Whether a second-pass disagreement should block the student link, or stay a warning (now: a
  warning shown in the review and in the bell).
- Whether administrators should assign student advisers, or only registrars (now: both).
