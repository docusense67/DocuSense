# Scan operator guide

This guide explains the **Scan station** screen in the browser and what each scan result means. It is for registrar staff who have scan operator permission. If **Scan station** is missing from your top menu, ask the system administrator to give you this permission.

**The most important rule:** VALID means the printed information **matches the school's official records**. It does not certify the paper itself. Every result screen ends with this note: "This verification confirms that the printed information matches the institution's records. It does not certify the physical integrity of the paper."

## 1. Choose your station

1. Sign in and click **Scan station**.
2. At the top right, pick your scanner from the **Station** list. The browser remembers your choice next time.
3. Check the status next to the list:

| Status shown | Meaning | What to do |
| --- | --- | --- |
| ● Ready | The scanner is connected and waiting. | Start a session. |
| ● Scanning | Paper is being read. | Wait. |
| ! Paper jam: clear and re-feed | A sheet is stuck. | Clear it and feed that sheet again. |
| ! Double feed: re-feed the sheet | Two sheets went in together. | Feed the sheets again, one document at a time. |
| ✕ Offline | The scanner is not connected. Scanning is blocked. | Reconnect the scanner. See "Scanner hardware" below, or call support. |
| ○ No status reported yet | The station has not checked in since it was set up. | Turn on the station PC and scanner. |
| ○ No signal from the station since … | The station PC or its scanning program is not running. Scanning is not blocked, but nothing will arrive. | Check the station PC is on and signed in; restart it if needed. |

The status updates every few seconds.

## 2. Scan

1. Under **Scan mode**, click **Single document** for one document, or **Bulk stack** for a pile of documents. These buttons work only after you choose a station. While the station reports offline they stay greyed out, and the screen shows "✕ Scanner offline: reconnect it before scanning."
2. A banner appears, for example: "Session 14 (bulk) · place paper in the feeder; collect it from the BULK tray."
3. Place the paper in the feeder. Collect it from the tray named in the banner.
4. Results appear by themselves within a few seconds of each document. While pages are still coming in, the station status reads "Receiving pages".
   - If paper was scanned before you chose Single or Bulk, the station status says "… file(s) waiting: start Single or Bulk on the dashboard to check them". Click the mode; the waiting pages are checked straight away.
5. When you finish, click **End session**. Starting a new session also ends the previous one on that station.

## 3. Read the session panel

The left panel shows the whole session:

- Running totals: **Total**, **✓ Valid**, **✕ Invalid** and **! Review**.
- A table with **#** (the document's position in the stack), **Document** (its number) and **Result**. A short reason appears under each number.
- **CSV** and **PDF** links at the top right. These download the session results as a report.

The right panel shows the latest result. To look at an earlier one, click its document number. To go back, click **Follow latest result**.

In bulk mode, use the **#** column to find a flagged document in the paper stack.

## 4. Read a result

The large badge shows the result.

| Result | What it means | What to do |
| --- | --- | --- |
| **✓ VALID** (green) | The document was issued and released by the school. It has not been revoked. Every checked field matches the school's official records. | Nothing. The information matches. |
| **✕ INVALID** (red) | The document does not match the school's official records. | Set the paper aside and follow your school's policy. Tell the registrar. |
| **! NEEDS REVIEW** (amber) | The system could not fully confirm the match. This is not an accusation. Often the print or scan was simply unclear. | A registrar decides (section 5). |

Under the badge you see the student name, document type and number, and how many times this document has been scanned. Below that is a list of reasons. A VALID result reads "Information matches official records as of" followed by the scan date and time. The match is a statement about the records at that moment.

### Common reasons

INVALID:

- "Document not issued by this institution (no matching document number or QR code)": no document with this number exists in the school's records.
- "Document has been revoked by the registrar": the school withdrew this document.
- "Document was never released by the registrar": the document was prepared but never officially issued.
- "Page count differs from issued record: page 2 of 3 missing": a page is missing or extra.
- "... does not match official records", for example a grade or the student name: the system read it clearly, and it differs.

NEEDS REVIEW:

- "QR code unreadable; document identified from the printed number only"
- "QR code does not belong to any issued document; document identified from the printed number only (possible forgery)": check this paper carefully.
- "Could not read ..." or "... read with low OCR confidence": the text was too faint or blurred to read with certainty.
- "... is similar but not identical to official records": usually a small spelling difference in a name.
- "Official record updated since this document was issued": the paper matches what was printed, but the school's records changed afterwards, for example through a grade correction.
- "Page markers unreadable or out of order", or "Row for IT101 ... not found on the paper"

### The field table

| Column | Meaning |
| --- | --- |
| Field | What was checked. "(critical)" marks the student number, name, document number and grades. |
| On paper | What the scanner read. |
| Official | What the school's records say was printed. |
| Result | ✓ Match, ✕ Mismatch, ! Similar, ! Low confidence or ! Not read. "OCR 97%" shows how sure the reading was. |

Problem rows are listed first and shaded. Under each one, "On the scan (page N):" shows the exact part of the scanned page, so you can judge it with your own eyes. Click **page 1**, **page 2** and so on to see whole pages.

"Expired" only means the student's download period ended. It does not affect the scan result. A revoked document always comes out INVALID.

## 5. Decide a NEEDS REVIEW result

Only results marked NEEDS REVIEW can be decided.

1. Open the result. You can reach it from the session table, **Recent scans**, or the dashboard's **Needs review** list.
2. Under "! Registrar decision needed", compare the image of each flagged field with the official value. Compare with the original paper too.
3. In **Remarks (what you checked)**, describe what you saw. You need at least 3 characters.
4. Click **Confirm valid** or **Confirm invalid**.

The screen then shows "Reviewed: confirmed ..." with your remarks. The automatic NEEDS REVIEW result is kept on record next to your decision. You cannot change a decision after you save it.

## 6. Recent scans and failing fields

- **Recent scans** lists the latest 50 scans. Filter by result (**All results**, **Valid**, **Invalid**, **Needs review**), by type (**All types**, **TOR**, **COE**), and by **From** and **To** dates. "reviewed: VALID" means a registrar has decided that scan.
- **Export: CSV PDF** downloads the scans that match your current filters, for example one day's results for a report.
- **Fields that fail most** counts which fields most often fail to match, using the same filters. If one field keeps failing on many documents, the cause is usually the scanner or the template, not tampering. Report it to support.

## 7. Scanner hardware

> The physical scanner, feeder, output trays and wiring are maintained by the hardware team. See [docs/hardware/](../hardware/).

## 8. Troubleshooting and common questions

- **"Choose a station first."** Pick your scanner in the **Station** list at the top right.
- **No result appears ("Waiting for the station to send results…").** Check the station status. Look for a jam or double feed.
- **"✕ Scanner offline: reconnect it before scanning."** The scan buttons stay greyed out until the station reports it is back. Reconnect the scanner, or call the hardware team.
- **Many NEEDS REVIEW results on genuine documents.** Check print quality and paper alignment, and check **Fields that fail most**. Tell support if it continues.
- **A student's printout was resized or cropped.** This can cause NEEDS REVIEW or missing-field results. Ask for a print at 100% scale.
- **I was signed out.** You are signed out after 30 minutes without activity. Sign in again. Your station choice is remembered.
- **"Invalid email or password…"** After 5 wrong tries, your account locks for 15 minutes and then unlocks by itself. The system administrator can unlock it sooner.

## 9. Incoming documents station (intake)

The intake station screens the enrollment documents students hand in (PSA birth certificate, JHS report card, good moral certificate, JHS completion certificate). A camera photographs each page, the system checks it, and a gate drops the page into one of two trays. Click **Intake station** in the top menu (scan operator permission needed). On the station's own small screen this page is shown full time.

**What the trays mean.** The system decides the tray, not the station and not you:

| Tray | Shown as | Meaning |
| --- | --- | --- |
| **Tray 1** (green, ✓) | Passed screening | No automatic check flagged the page. |
| **Tray 2** (amber, !) | Needs manual review | At least one check flagged the page, or it could not be checked. The reasons are listed. |

Passed screening only means no automatic check flagged the page. Registrar staff review every incoming document in **Incoming documents**, including the ones in Tray 1. This screen never shows what was read from a page (names, dates, grades) and never shows the page image: the review stays with the registrar.

### Choose the station

Pick the station in the **Station** list at the top right. Stations that have already screened incoming documents come first; the others say "(no incoming documents yet)". The browser remembers your choice. On a screen that was never set up, the first incoming documents station is shown.

### Status banner

| Banner | Meaning | What to do |
| --- | --- | --- |
| ● Online · ready for the next stack | The station is running and waiting. | Place the next stack in the feeder. |
| ● Online · screening pages | Pages are being photographed and checked. | Wait; results appear within a few seconds. |
| ! Paper jam: clear the feeder and re-feed the page | A page is stuck. | Clear it and feed that page again. |
| ! Double feed: take the pages out and re-feed them one at a time | Two pages went in together. | Re-feed them one at a time. |
| ✕ Station offline · *message* | The station reported that it cannot work, with its reason (for example "Camera not found"). | Fix what the message says, or call the hardware team. |
| ○ No signal from the station since … | The station program is not running. | Check that the station is switched on; restart it if needed. |
| ○ No status reported yet | The station has not checked in since it was set up. | Switch the station on. |

The screen updates every 2 seconds.

### Reading the screen

- **The big panel** shows the latest page: the tray in large letters (**Tray 1** in green or **Tray 2** in amber, "Going to Tray N" while the gate is still moving), the page number in the stack (**Page #3**), the decision, the document type and up to two reasons in plain words.
- **Tray counts** next to it: how many pages of the running batch belong in Tray 1 and in Tray 2, and the total. Between batches the counts are for today. "waiting for the gate", "jammed" and "in the wrong tray" appear when they apply.
- **Pages in this batch** lists the last pages, newest first: number in the stack, tray and decision, document type, reasons and time.
- **Recent batches** lists the last five batches with their counts.

### When the gate did not do what it was told

The station reports where each page actually landed. If something went wrong, the system moves the page to "needs manual review" and the screen tells you what to do, in red:

- "Jam: take page #N out of the gate and put it in Tray 2."
- "Page #N went into Tray 1: move it to Tray 2."

Use the page number to find the sheet in the tray. The registrar sees the same problem in the review queue.

### Ending a batch

A batch is one stack fed in one go. The station starts and ends batches by itself. If a batch stays open (for example the station was switched off in the middle of a stack), click **End batch**, then **Yes, end batch**. Ending a batch is recorded in the audit trail with your name. Any page the station still sends for that batch is refused, and the station starts a new batch for the next stack.

### Common questions

- **The latest page says Tray 2. Is the document fake?** No. Tray 2 only means a person has to look at it. Often the photo was unclear or the document type was not recognised.
- **Can I see what was read from the page?** No. Only registrar staff see the extracted details, in **Incoming documents**.
- **The counts look wrong.** Check for "in the wrong tray" or "jammed" pages and move them as the screen says. The counts show where pages *belong*.
