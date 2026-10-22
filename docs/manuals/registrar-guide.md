# Registrar guide

Sign in with your email and password. On the first sign-in, set a new password of at least 10 characters. The top menu has **Dashboard**, **Requests**, **Records**, **Academic setup**, **Templates** and **Audit trail**. If you have scan operator permission, it also has **Scan station** (see the [Operator guide](operator-guide.md)).

Actions that change official records ask for a **reason** of at least 3 characters. The reason is kept in the audit trail with your name and the time. Nobody can edit or delete it.

## 1. Request statuses

Requests follow the approve-first order: you approve the **request** first; only then does the system retrieve the student's records and generate the document; you then **verify** the generated document before it is released. Students see only four words, shown here in brackets: **Pending**, **Approved**, **Rejected** and **Completed** (plus Cancelled, Expired and Revoked).

| Status (what the student sees) | What it means | Your action |
| --- | --- | --- |
| Pending approval (Pending) | Submitted and eligible. Nothing has been retrieved or generated yet. If your school requires payment, it may also be waiting for payment. | **Confirm payment** if needed, then **Approve request** or **Reject**. |
| Approved · generating (Approved) | You approved it; the document is being generated. If a "Generation blocked:" note shows, the document could not be prepared. | If blocked: fix the cause, then **Retry generation**. |
| Ready for verification (Approved) | The PDF is generated and waiting for your check. | Verify it, send it back for regeneration, or reject. |
| Verified · awaiting release (Approved) | Verified but not released (pickup setup only). | **Release** it when the student collects it. |
| Released (Completed) | The student can download it, and it passes scans. | None. |
| Rejected (Rejected) | Refused. The student sees the reason. | **Reopen and regenerate** after a fix. |
| Cancelled | Withdrawn by the student or a registrar. | None. |
| Expired | The 30-day download period ended. The document **still passes scans**. | **Reopen download** if the student asks. |
| Revoked | You withdrew the document. Every later scan shows INVALID. | None. This cannot be undone. |

The system rejects ineligible requests by itself, with a reason starting "Not eligible:", when they are submitted and again when you approve them (records may have changed in between). Examples: "Incomplete grade in IT101 (1st Sem AY 2025-2026)" or "No completed (locked) terms on record" for a TOR, and "Not enrolled in the current term" for a COE.

## 2. Approve, verify and release

### Approve the request

1. On the **Dashboard**, the **Approval queue** lists the oldest pending requests first. Or open **Requests** and filter by **Pending approval**.
2. Click a reference number, such as REQ-2026-000012. The page says "No document yet": records are retrieved and the document is generated when you approve.
3. Click **Approve request**, or type a reason and click **Reject**. Approving retrieves the records and generates the PDF with a document number; the status becomes **Ready for verification**.

### Payment

- **If your school does not charge fees** (the usual setup), you can approve as soon as the request arrives.
- **If your school requires payment**, the request page shows "Waiting for payment. Confirm it, then approve the request." After the student pays, click **Confirm payment**, then **Approve request**. (Approve stays greyed out until payment is confirmed.)

### Verify the generated document

1. On the **Dashboard**, the **Verification queue** lists generated documents waiting for you. Or filter **Requests** by **Ready for verification**.
2. The **Document** panel shows the PDF as it will print. **Check against records** lists each printed value beside the student's records today. Its heading reads "✓ Matches current records" or "≠ Records changed since generation". Rows that differ are marked "≠ differs".
3. Check the name, student number, program, grades and GWA. At the bottom, **History (audit trail)** lists every step, from the request and your approval to each scan, with who did it and when.
4. If something is wrong, correct the records, type a reason and click **Send back for regeneration**. The old PDF is voided and a new one is generated with a **new document number**.

### Verify and release

- **If your school releases automatically**, the button reads **Verify & release**. The status goes straight to **Released** (the student sees **Completed**), and the student can download for 30 days.
- **If your school uses pickup**, the button reads **Verify document**, with the note "Release is a separate step at this school (e.g. pickup)." When the student collects the document, open the request and click **Release (student can download)**. The 30-day period starts then.

### Actions that need a reason

Type the reason in **Reason (required to reject, regenerate, cancel, revoke or reopen)**. The buttons stay greyed out until you have typed at least 3 characters.

| Button | When it appears | What it does |
| --- | --- | --- |
| **Reject** | Pending approval, Approved · generating, Ready for verification | Refuses the request. The student sees your reason. Any prepared PDF can never become official. |
| **Retry generation** / **Send back for regeneration** / **Reopen and regenerate** | Approved · generating, Ready for verification, Rejected | Checks eligibility again and generates a new PDF with a **new document number**. Use this after you correct records or fix a template. |
| **Cancel request** | Pending approval, Approved · generating, Ready for verification | Withdraws the request, for example when the student asks at the window. |
| **Revoke document** | Released, Expired | Withdraws an issued document. The student can no longer download it. Every later scan shows **INVALID** with "Document has been revoked by the registrar". |
| **Reopen download** | Expired | Gives the student another 30 days to download. |

### Walk-in requests

On **Requests**, under **Walk-in request**, choose the **Student** and **Document**, type the **Purpose**, and click **File request**. The same checks run as for online requests, and the request then waits for your approval like any other.

### Blocked requests

The dashboard may show "! N request(s) blocked during generation (missing data or no active template)". Click **Review**, fix the cause shown after "Blocked:", then **Retry generation**.

## 3. Records

### Grade change requests

After a term is locked, teachers ask for corrections. Each request appears at the top of **Records** under **Grade change requests (N pending)**. Each row shows the student, subject, change (old → new), reason and sender.

- Click **Approve** to change the official grade.
- Click **Reject**, type a reason, then click **Reject** again.

Documents already issued keep the grade they printed. If one of them is scanned later, the scan asks for review. After you approve a correction:

- Send back for regeneration any request for that student that is waiting for verification.
- Revoke any released document that shows the old grade, and ask the student to request it again.

### Add a student

1. Under **Add student**, fill in **Student number**, **First name**, **Middle name**, **Last name**, **Program** and **Year level**.
2. To give the student a login now, fill in **Login email (optional)** and a **Temporary password** of at least 10 characters. The student must change it on first sign-in.
3. Click **Add student**. Each student number can be used only once.

### Import students (CSV or Excel)

1. Prepare the file as shown in [Import templates](../import-templates/README.md). Under **Import students (CSV or Excel)**, choose it in **Student list** and click **Upload**.
2. If any row has a problem, **nothing is imported**. Every problem is listed as "Row N: ...". Fix the file and upload it again.
3. When the import works, the screen says "✓ N student(s) imported." Imported students have no login yet. Create logins from the Students list.

### Manage a student

Search the **Students** list by name or number, then click **Manage** on a row. Each action asks for a reason, except **Create login**.

| Action | What it does |
| --- | --- |
| **Create login** | Gives the student a login. Enter a **Login email** and a **Temporary password**. Shown only when the student has no login yet. |
| **Reset password** | Sets a new temporary password (10+ characters). This **also unlocks** a locked account. The student must change it at the next sign-in. |
| **Deactivate login** | The student can no longer sign in. Their record stays. |
| **Mark inactive** / **Mark active** | Changes the student's record status. An inactive student cannot request a TOR or COE. |

## 4. Academic setup

### Terms and grading periods

1. To add a term, enter the **Academic year** (for example 2026-2027) and choose the **Semester** (1st, 2nd or Summer). Click **Add term**.
2. Click **Make current** on the term in session. COE requests check enrollment in the current term.
3. When grading is finished, click **Lock grades**. Locking makes all grades in the term final and cannot be undone.
4. If some enrolled students still have no grade, a warning appears with a **Lock anyway** button. If you lock anyway, those students cannot get a TOR. Check with the teachers first.

A student needs at least one locked term, with no missing or INC grades, to request a TOR.

### Class sections

1. Choose a term in the list at the top right of **Class sections**. ("All terms" shows every section but cannot add one.)
2. To add a section, choose the **Subject**, type the **Section** (for example BSIT-1A), and choose the **Teacher** (or leave it "Not assigned"). Click **Add section**.
3. To change a teacher, choose a different name in the **Teacher** column.
4. To enroll students, click **Enroll students** under the subject. Paste the student numbers, one per line or separated by commas. Click **Enroll**. If any number is wrong or already enrolled, **nobody is enrolled**, and the error lists each problem. Enrollment is not available once the term is locked.

### Class advisers

Every class section of a term that shares a section code (for example all BSIT-1A subjects in the 2nd semester) forms one advisory class. **Class advisers** lists them with their number of subjects and students.

1. Optionally choose a term at the top right of **Class advisers**.
2. In the **Adviser** column, choose the teacher. Choose "No adviser" to remove one.
3. The adviser sees the class's students (name, student number, program, year level) and can view, not edit, every subject's grades in that class. Grades are still encoded only by each subject's teacher. Each change is recorded in the audit trail.

### Programs, Teachers and Subjects

- **Programs**: enter the **Code** (for example BSIT) and **Name**, then click **Add**.
- **Teachers**: enter the **Employee number**, **Full name** and **Department**. To give the teacher a login, also fill in **Login email (optional)** and **Temporary password**. Click **Add teacher**. A teacher without a login cannot encode grades.
- **Subjects**: enter the **Code**, **Title** and **Units**, and choose a **Program** (or "General"). Click **Add**.

## 5. Templates

Templates set how each TOR and COE looks. Only one version per document type is active at a time. Documents already issued keep the version they were printed with.

1. Open **Templates** and choose **TOR** or **COE**. The **Versions** table lists each version (draft, active or retired) and its **Checks**.
2. Click **New version** (or **Edit draft** if a draft exists). The editor shows **HTML template** and **OCR field zones (mm on A4)**.
3. Paste in the template author's changes and click **Save as new draft** (later: **Save draft**).
4. Click **Preview PDF** to see it with sample data. Nothing is issued. Preview and checks use the last saved draft.
5. "✓ All checks pass" means it is ready. Otherwise problems are listed, such as "Unknown placeholder" or "Missing OCR zone".
6. Click **Activate**. The old version is retired.

Template authors should follow the [Template authoring guide](../template-authoring.md).

## 6. Dashboard

The dashboard refreshes itself every 30 seconds.

- **Requests**: choose **Today**, **Last 7 days**, **Last 30 days** or **Date range**. Date range adds **From** and **To** dates, which also apply to the validation summary. The cards use the words students see: Total, Pending (waiting for your approval), Approved (generating, waiting for verification or for release), Completed (released, including expired) and Rejected.
- **Approval queue**: the oldest requests waiting for your approval. **Verification queue**: generated documents waiting for your check. Click **All** for the full list.
- **Documents generated per month** (click **Show table** for numbers), **Validation summary**, and **Needs review** (scans waiting for a decision).
- **Data health**: students whose records will block a TOR request (missing grades, INC grades, no program).
- **Recent activity**: the latest recorded actions.

## 7. Audit trail

Every sign-in, record change, request, approval, download and scan is recorded with who, when and from which computer. Nobody can edit or delete these entries.

1. Open **Audit trail**.
2. In **Search**, type a document number, student number, email or action. Optionally set **From** and **To** dates. Click **Search**.
3. Each entry shows **When**, **Who**, **Action**, **Record** and **Details**. Details include "before:" values for changes.
4. Use **Newer** and **Older** to page through the results, 50 at a time.

For one document's full history, open its request and scroll to **History (audit trail)**.

## 8. Validation review queue

1. On the dashboard, click a document number under **Needs review**. Only registrars with scan operator permission can open these. Others see the list without links.
2. Compare each flagged field's paper value, official value and scan image.
3. Type what you checked in **Remarks (what you checked)**, then click **Confirm valid** or **Confirm invalid**.

The automatic result stays on record next to your decision. VALID means "matches the school's official records". Details are in the [Operator guide](operator-guide.md).

## 9. Common questions

- **A student says the download expired.** Use **Reopen download**. The document stays valid for scans either way.
- **A request was rejected for an INC grade.** The teacher sends a grade change request, and you approve it. Then **Reopen and regenerate** the rejected request, or the student submits a new one.
- **"A TOR request is already pending."** Each student can have only one open request per document type. Finish, reject or cancel the open one first.
- **A button is greyed out.** Type a reason of at least 3 characters first.
- **A student cannot sign in.** After 5 wrong passwords, an account locks for 15 minutes and then unlocks by itself. To unlock it sooner, use **Records → Manage → Reset password** for a student, or **Academic setup → Teachers → Reset password** for a teacher. Registrar and admin accounts are reset by the system administrator.
- **A teacher sees "No classes assigned."** Assign them in **Academic setup → Class sections** (to teach) or **Class advisers** (to advise).
- **I was sent back to the sign-in screen.** You are signed out after 30 minutes without activity. Sign in again.
