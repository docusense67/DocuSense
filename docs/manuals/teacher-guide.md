# Teacher guide

This guide shows how to encode and submit grades for your classes. You see only the class sections assigned to you, the sections of your advisory class if the registrar made you a class adviser, and only the students enrolled in them.

The grades you encode are printed on students' Transcripts of Records. Please check every entry before you submit.

## 1. Sign in

1. Open the school's document system. Enter your **Email** and **Password**, then click **Sign in**.
2. The first time, the **Set a new password** screen appears. Enter the temporary password you received in **Current password**. Enter a new password of at least 10 characters in **New password**. Click **Save password**.
3. To leave, click **Sign out** at the top right.

You are signed out automatically after 30 minutes without activity.

## 2. Open a class

1. Click **My classes** in the top menu.
2. Under **Class sections**, click a class button. Each button shows the subject code, semester and school year, for example "IT101 · 1st 2025-2026". A 🔒 means the term is locked.
3. The grade sheet opens below. The title shows the subject code, subject title and section. The right side shows the term and one of these labels:

| Label | Meaning |
| --- | --- |
| *(no label)* | Open. You can encode and change grades. |
| submitted | You have submitted the grades as final. You can still correct them until the registrar locks the term. |
| 🔒 locked | The registrar has locked the term. Grades are final. Use **Request change** to correct one (section 5). |

The grade sheet has four columns: **Student number**, **Name**, **Grade** and **Remarks**. Students who dropped the class through the registrar do not appear.

### Advisory class

If the registrar made you the class adviser of a section (for example BSIT-1A in the 2nd semester), an **Advisory class · BSIT-1A** card appears below **Class sections**. It lists the class's students (student number, name, program and year level) and one button per subject, with the subject teacher's name.

Click a subject to open its grade sheet. It says "Advisory class: view only. The subject teacher encodes these grades." You can read the grades but not change them or request changes, unless you also teach that subject (then it is under **Class sections** too).

## 3. Encode grades

1. Click the **Grade** box for a student and type the grade.
2. Repeat for the other students. You do not need to finish the whole class at once.
3. Click **Save**. The button shows how many rows you changed, for example **Save 12 change(s)**.
4. When saving works, you see "✓ Saved 12 grade(s)."

### Grades the system accepts

Below the grade sheet, the screen shows: "Scale: 1.00 to 3.00 in steps of 0.25, or 5.00, INC, DRP". That is the standard list. Your school may set a different one.

| You type | Meaning | Remarks shown |
| --- | --- | --- |
| 1.00, 1.25, 1.50, 1.75, 2.00, 2.25, 2.50, 2.75, 3.00 | Passing grade | Passed |
| 5.00 | Failing grade | Failed |
| INC | Incomplete | INC |
| DRP | Dropped | DRP |

Tips:

- "1.5" is saved as 1.50. Small letters ("inc") also work.
- Values outside the list are refused, for example 4.00 or 3.10.
- The **Remarks** column fills in by itself after you save. You do not type it.
- You cannot clear a saved grade by deleting it. Type the correct grade instead.

### When a row has a problem

The system checks every row before it saves anything.

- If even one row is wrong, **no grades from that save are kept**, including the correct rows. A red message appears: "No grades were saved. Fix the entries below and try again."
- Each wrong row gets a red border with the reason under it. For example: "4.00 is not on the grading scale (...)" or "'1.0O' is not a grade".
- Your typing stays on the screen. Fix the red rows and click **Save** again. Every row you changed is saved together.

### Upload grades from a file instead

You can upload a whole class at once.

1. Prepare a CSV or Excel file with two columns: **student_number** and **grade**. See [Import templates](../import-templates/README.md) for an example.
2. Below the Save button, click **Upload grades from a file instead**.
3. Choose the file in **Grade file**, then click **Upload**.
4. If any row has a problem, **nothing is saved**. Each problem is listed as "Row N: ...", for example a student who is not in this section or a grade not on the scale. Fix the file and upload it again.
5. When it works, you see "✓ N grade(s) saved from the file."

## 4. Submit grades as final

When every student in the class has a grade:

1. Save all your changes first. **Submit grades as final** stays greyed out while you have unsaved changes.
2. Click **Submit grades as final**.
3. You see "✓ Submitted. The registrar will lock the term." The class now shows "submitted".

If any student has no grade, you see "Grades missing for:" followed by their student numbers. Encode those grades, save, and submit again.

Until the registrar locks the term, you can still correct a grade and save it again.

## 5. After the term is locked: Request change

After the registrar locks the term, grades are final. The class shows 🔒 and the message "This term is locked. To correct a grade, use “Request change”; the registrar approves it." The grades are shown as plain text and cannot be edited.

To correct a grade:

1. In the student's row, click **Request change**.
2. Type the new grade in **Correct grade**.
3. Type why in **Reason (kept in the audit trail)**, at least 3 characters.
4. Click **Send request**. The row shows "✓ Sent to the registrar".

Your requests appear under **My grade change requests**, with the change (old → new) and a status of **pending**, **approved** or **rejected**. You can have only one pending request per grade.

## 6. Troubleshooting and common questions

**"No classes assigned."**
The registrar has not assigned any class section to you yet (an advisory class alone shows under **Advisory class**). Contact the registrar's office.

**A student is missing from my list, or someone is listed who should not be.**
Class lists come from the registrar's enrollment records. Ask the registrar to correct the enrollment. You cannot add or remove students yourself.

**I clicked Save and nothing was saved.**
At least one row had an error, shown in red. Fix it and save again. See "When a row has a problem" above.

**"Term is locked. Submit a grade change request instead."**
The registrar locked the term while you had the page open. Your changes were not saved. Reload the page and use **Request change**.

**"A change request is already pending for this grade."**
Wait for the registrar to decide the first request.

**"Session expired", or I was sent back to the sign-in screen.**
You were inactive for 30 minutes. Sign in again. Unsaved grades on the screen are lost, so save often.

**"Set a new password" appears when I sign in.**
This happens on your first sign-in, and after your password is reset. Choose a new password of at least 10 characters. It must be different from the temporary one.

**"Invalid email or password. After 5 failed attempts an account is locked for 15 minutes…"**
Check your email spelling and Caps Lock. After 5 wrong tries in a row, your account locks for 15 minutes and then unlocks by itself. The message is the same whether or not the account is locked. If you cannot wait, ask the registrar (or the system administrator) to reset your password.
