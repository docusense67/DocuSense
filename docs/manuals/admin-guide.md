# System admin guide

This guide covers the System Admin screens: staff accounts, scan station registration, the read-only overview and the audit trail. Installation, settings and backups are covered in a separate installation guide.

As System Admin you do not handle requests, student records, grades or templates. Those belong to the registrar.

## 1. Sign in

1. Sign in with your email and password.
2. On the first sign-in, set a new password of at least 10 characters.
3. The top menu shows **Accounts & stations**, **Overview** and **Audit trail**.

You are signed out after 30 minutes without activity.

## 2. Accounts

### Create a staff account

1. Open **Accounts & stations**.
2. Under **Accounts**, enter the person's **Email**.
3. Choose a **Role**:
   - **Registrar**: registrar staff. Manages requests, records, academic setup and templates.
   - **System Admin**: manages accounts and stations.
4. Type a **Temporary password** of at least 10 characters.
5. For registrar staff who will scan documents, tick **Scan operator**. This box works only when the role is Registrar.
6. Click **Create**.
7. Give the person their email and temporary password privately, in person or through an official school channel. They must change the password on first sign-in.

Each email can be used only once. A duplicate shows "Email already in use".

**Student and teacher logins** are not created here. The registrar creates them with the student's or teacher's record, so each login is linked to it. Students are added in **Records**, and teachers in **Academic setup → Teachers**.

### The account list

The list shows every account, including students and teachers.

| Column | Meaning |
| --- | --- |
| Email | The sign-in email. |
| Role | The role. "· operator" means the person has scan operator permission. |
| Status | **Active**: normal. **Must change password**: has not yet replaced the temporary password. **Locked**: 5 wrong passwords in a row. **Deactivated**: can no longer sign in. |
| Last login | The date and time of the last successful sign-in. |

### Reset, unlock or deactivate

Each active account has these buttons. Every action asks for a reason, which is kept in the audit trail.

- **Reset password**: enter a **New temporary password (10+ characters)** and a reason. The person must change it at the next sign-in. For a locked account the button reads **Unlock / reset password**, and it unlocks the account at once.
- **Deactivate**: the person can no longer sign in. You cannot deactivate your own account.

After 5 wrong passwords, an account locks for 15 minutes and then unlocks by itself the next time the person signs in correctly. The list may still show **Locked** until then. Use **Unlock / reset password** only if the person cannot wait.

Registrars can also reset student passwords. Teacher and staff passwords are reset here.

## 3. Scan stations

Each scanner PC needs its own registration and key before it can send scans.

1. Under **Scan stations**, type a **Station name**, for example "Registrar Window 1". Each name must be unique.
2. Click **Register**.
3. A box appears: "! API key for Registrar Window 1 (shown once; copy it to the station now)".
4. **Copy the key now** and give it to the person setting up that scanner PC (see [docs/hardware/](../hardware/)). The key is shown only once. The system does not store a readable copy, so nobody can display it again.
5. Treat the key like a password. Anyone who has it can send scans as that station.

The station list shows **Name**, **Active** (Yes or No) and **Registered** (date). Operators pick the station by name on their **Scan station** screen.

To retire a station, click **Deactivate**, type a reason, and confirm. Its key stops working at once. Do this for a lost or replaced station PC, or a leaked key. Then register a new station under a new name.

## 4. Overview (read-only)

**Overview** shows the same numbers as the registrar dashboard, but you cannot open or change anything:

- **Requests**: Total, Pending, For approval, Released and Rejected for **Today**, **Last 7 days**, **Last 30 days** or a **Date range** (From and To).
- A warning when requests are blocked during generation.
- **Approval queue**, **Documents generated per month**, **Validation summary** and **Needs review**.
- **Data health**: students whose records will block a TOR request.
- **Recent activity**: the latest recorded actions.

The overview refreshes itself every 30 seconds.

## 5. Audit trail

**Audit trail** lists every recorded action: sign-ins, account changes, record changes, approvals, downloads and scans. Each entry shows who did it, when, and from which computer. Nobody can edit or delete these entries, including administrators.

1. In **Search**, type a document number, student number, email or action. Optionally set **From** and **To** dates. Click **Search**.
2. Each entry shows **When**, **Who**, **Action**, **Record** and **Details**.
3. Use **Newer** and **Older** to page through the results, 50 at a time.

## 6. Troubleshooting and common questions

**A user cannot sign in and sees "Invalid email or password. After 5 failed attempts an account is locked for 15 minutes…"**
The same message appears for a wrong password and for a locked account. Check the **Status** column. If it shows **Locked**, the person can wait 15 minutes, or you can use **Unlock / reset password**. If it shows **Deactivated**, the account was switched off on purpose. Check the audit trail for the reason.

**A user is stuck on "Set a new password".**
This screen appears on every first sign-in and after every reset. The user must enter the temporary password as **Current password**, then a different new password of at least 10 characters.

**"Password must be at least 10 characters".**
Temporary passwords follow the same rule as normal ones.

**The Scan operator box is greyed out.**
Only Registrar accounts can hold scan operator permission. Choose **Registrar** as the role first.

**I need a student or teacher login.**
Ask the registrar. Student and teacher logins are created with their records.

**"Station name already exists".**
Choose a different name.

**A station shows "✕ Offline" or "○ No status reported yet" on the operator screen.**
The scanner or the station PC is not working or not connected. Check it with the hardware team ([docs/hardware/](../hardware/)). If the key was entered wrongly on the station PC, the station cannot report at all. In that case, deactivate it, register a new station, and enter the new key.

**I was signed out.**
You were inactive for 30 minutes. Sign in again.
