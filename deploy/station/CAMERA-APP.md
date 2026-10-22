# ADGVS login app with the Pi camera paper mechanism

This integrates the existing React login/Scan station page, FastAPI validation API,
scan-agent OCR pipeline, and the local GPIO bridge. It is an optional custom mechanism;
existing SANE/file-drop stations keep their original workflow.

## What a cycle does

1. Choose the camera station. Save the one-time Tray position setup popup. The Feed button opens its session automatically.
2. Click **Feed one sheet & validate**. Servo 1 moves 90 → 180 → 90.
3. L298N runs backward at 100% for 5 seconds, then stops. Camera captures the stationary sheet.
4. Existing QR/OCR processing extracts fields; the backend compares them with official records,
   saves the image/result, and appends the normal validation audit entry.
5. For VALID or INVALID, servo 3 selects VALID → Up (180°) or INVALID → Down (90°) **before** servo 2 runs for 8 seconds.
6. NEEDS REVIEW, a multi-page document, stop, or capture/OCR/network failure holds the sheet.
   Remove it and acknowledge that the path is clear before feeding again.

VALID means “matches official records.” It is never inferred from the presence of text or a QR.
This version handles one sheet per cycle, not automatic bulk stacks or multi-page assembly.
Movement is timed: no paper sensor confirms that feeding/ejection physically succeeded.

## Install and run in the Pi desktop Terminal

Close the previous desktop GUI and standalone motor/servo programs. The old SANE station
service must not capture the same papers or report status for this camera station. If that
service is enabled, stop it before using this alternative workflow:

```bash
sudo systemctl stop adgvs-station
```

Install frontend/backend dependencies once:

```bash
cd /home/adgvs/adgvs-src
bash deploy/station/setup-local.sh
```

Node must satisfy `frontend/package-lock.json` (20.19+ or 22.12+); Python 3.12+ is required.
System Picamera2, libcamera, gpiozero/lgpio, Tesseract and libzbar must be installed. The
existing `/opt/adgvs/venv/bin/python` is used for camera/OCR on this Pi; set
`ADGVS_CAMERA_PYTHON` if using another environment with those imports. The setup script
creates a scan-agent environment if the installed station Python is absent.

### Explicit demo with fictional records

```bash
bash deploy/station/run-local.sh --demo
```

Open **http://localhost:5173**, sign in as `registrar@school.edu.ph` with
`ChangeMe-2026`, choose **Dev Station**. Open Tray position setup, check Up and Down, then Save setup once. Load one sheet and click **Feed one sheet & validate**.

Demo credentials/records come from the existing dev seed. This mode still moves REAL
hardware after you click Feed. Results compare against the demo database, not school records.
Stop with **Stop mechanism** or Ctrl+C in the launcher Terminal.

### Existing official database

Create `deploy/station/local.env` (shell environment assignments, chmod 600) with:

```bash
ADGVS_DATABASE_URL='your existing database connection string'
ADGVS_SECRET_KEY='your configured application secret'
ADGVS_CAMERA_STATION_KEY='API key of the registered camera station'
ADGVS_MECHANISM_STATION_ID=123
# Optional: use a stable random shared bridge secret (32+ characters).
# Otherwise the launcher generates an ephemeral secret for its child processes.
# ADGVS_MECHANISM_KEY='...'
```

Use the station ID that belongs to that key. Create/register stations through the existing
admin UI. Use the existing database/migrations and a registrar account with **can_validate**.
The launcher does not initialize or seed the official database.

```bash
bash deploy/station/run-local.sh
```

The launcher starts the existing login app on loopback port 5173, API on 8000, and GPIO bridge
on 8765. It forces the camera to use this local API. A cloud-hosted API cannot connect to the
Pi's loopback bridge. All three local processes stop if a child exits or the launcher receives
Ctrl+C. This is a local development deployment, not a public production server.

## Wiring (BCM numbering)

| Device signal | GPIO | Physical pin |
|---|---:|---:|
| L298N IN1 | 17 | 11 |
| L298N IN2 | 27 | 13 |
| L298N ENA, jumper removed | 18 | 12 |
| Servo 1: gate | 12 | 32 |
| Servo 2: continuous eject | 13 | 33 |
| Servo 3: VALID / INVALID selector | 19 | 35 |

Motor is connected to OUT1/OUT2. Servo power comes from regulated external 5 V; all grounds
join the Pi ground. The motor supply does not power a servo directly. Servo 2's maximum
command is 2 ms at 50 Hz, followed by 1.5 ms neutral; its actual continuous operation and
neutral need physical calibration. Removing an SG90 plastic stop alone does not guarantee
continuous rotation because its position feedback still exists.

## Stops, storage and access

- Every new operator route requires the existing validator role guard. Browser requests use
  the authenticated API; station/bridge keys stay in server processes.
- Motion commands are audited as requests; successful physical motion is not claimed.
- The browser sends a heartbeat while its camera cycle is active. Four seconds without one
  cancels the cycle and stops outputs. Late OCR/network completion cannot resume ejection.
- Feed starts only after camera, Tesseract, station key and session preflight succeed.
- The bridge saves private raw images under `mechanism/.camera_pending/` and retains them
  on failures. Uploaded processed images use existing backend storage/access controls.
  These captures are not automatically deleted by the camera code.
- A private paper-state journal prevents automatic refeeding after a process restart. Check
  the paper path and acknowledge it in the app.
- Diagnostics: `mechanism/server.log` and each process's Terminal output.

## Current deployment limit

The coding tool environment cannot access GPIO/camera devices or bind localhost sockets.
Node/npm and the backend environment were also absent there. Source integration is saved;
installation, browser build, camera alignment/OCR quality, fixed tray movement and physical
motion still require the Pi Terminal and hardware. No end-to-end hardware result is claimed.

## Start automatically after reboot

The desktop startup integration is installed for user `adgvs` in demo mode. The Pi's
existing LightDM desktop auto-login opens the desktop; the enabled user service starts
ADGVS and an XDG autostart entry waits for the local API/page, then opens Chromium at
`http://localhost:5173/scan` in kiosk mode. The previous cloud kiosk startup line is
replaced, with its original file saved as `~/.config/labwc/autostart.before-adgvs-local`.

- The desktop shortcut **ADGVS Paper Validation** opens the same page in a normal window.
- Validator accounts land on the scan page after sign-in. Authentication is still required
  if the session has expired.
- Motors and servos do not resume a previous movement after reboot or power loss. Held
  paper must be removed/acknowledged before a new feed.
- `adgvs-local.service` restarts the app if a process exits. A single-instance lock prevents
  launching two copies through `run-local.sh`.
- No network connection is needed to open the local app after dependencies are installed.
  An official remote database/storage configuration can still depend on connectivity.

Reinstall configuration (choose the database explicitly):

```bash
python3 deploy/station/install-autostart.py --demo
# For a configured official database in local.env, omit --demo instead.
```

If enabling during an existing desktop session, close the manually launched copy first,
then run in that desktop's Terminal:

```bash
systemctl --user daemon-reload
systemctl --user start adgvs-local.service
bash /home/adgvs/adgvs-src/deploy/station/open-local.sh --window
```

On the next reboot/new desktop login, these steps are automatic. Startup diagnostics:

```bash
journalctl --user -u adgvs-local.service -n 60 --no-pager
cat ~/.local/state/adgvs/browser-startup.log
```

The installer saves/enables configuration through user-owned files. In the restricted
coding tool, the user service bus is unavailable, so live service activation and a real
reboot could not be exercised there. Exit kiosk with Alt+F4.

## Read the text extracted from the paper

After **Feed one sheet & validate**, the scan result includes **Text read from scanned
paper (OCR)**. A dedicated text panel directly under the live camera preview
loads the current paper’s text automatically; it does not require opening another page.
Starting another scan hides the previous paper’s text while extraction is running.
The same text appears when opening the saved scan result. It retains
newlines and groups text by document page; it is a transcription from the scanned pixels,
not a copy of the official record. The existing field comparison remains separate.

Full-page OCR also runs on papers that do not match an issued document. Empty/unreadable
pages and OCR timeouts have explicit messages. Older scans without saved full-page text
must be scanned again; existing extracted field comparisons remain available.
Text is limited to 50,000 characters per page, with a truncation notice when needed.
Only authorized validation operators can access the scan and its transcription.

Restart the local app after this update and refresh the browser before scanning a new
sheet. The extra full-page transcription can add processing time; the progress remains
at OCR while it is running. A full-page transcription timeout does not replace the
existing field-based verification result.

If the saved picture visibly contains text but OCR returns empty, open the saved scan
image and check focus, angle and framing. An out-of-focus or cropped camera capture can
remain unreadable even when a person can infer some words. Position the camera square
to a flat, well-lit sheet, include all four edges, and adjust camera distance/focus until
small letters are sharp before capturing again. Focus adjustment depends on the camera
module; an autofocus mode is only usable when the module exposes autofocus controls.

Full-page transcription uses the original camera image dimensions, independently of
the A4-normalized images used for template zones. If paragraph OCR returns no text,
contrast-enhanced sparse-text passes try straightening camera tilt, downscaling
oversized text without stretching, and both light/dark polarities. Low-confidence
noise is rejected, but short confidently read labels such as LTE are retained. Field comparisons and
verification rules are not relaxed by the transcription fallback.

The sparse fallback has a twelve-second processing budget after the paragraph pass.
Candidate scores select one OCR transcription rather than merging guesses from
several variants. The straightening/contrast variants are for displayed text only;
zone-based verification and the original saved images are unchanged.

The updated reader was also checked against an actual camera photo of large white
text on a tilted dark product box. It extracted LTE, POCKET and WIFI. Synthetic
regression coverage reproduces the same contrast/tilt conditions without storing
that camera photo in the test fixtures.

## Automatic camera focus

Autofocus is enabled only when the connected module exposes both `AfMode` and
`AfTrigger` through libcamera. Live preview requests continuous autofocus, using the
full autofocus range when available. After paper feeding stops, the scan starts an
explicit autofocus cycle and waits up to four seconds before capturing the document.
The lens stays locked during that capture rather than continuing to hunt for focus.

The preview shows autofocus mode, focusing, focus lock, or an explanatory failure.
When autofocus fails or times out on a supported camera, the workflow holds the paper
without submitting OCR or routing it. Stop cancels the pending camera job. The focus
wait uses the camera I/O lock, leaving browser heartbeats and Stop controls available.

A camera without autofocus controls is identified as manual/fixed focus. It can still
capture; software cannot add a missing focus motor. Set that module's lens/camera
distance manually. This status concerns camera focus only; removed GPIO checks and
hardware alert notifications remain removed.

Activate updates with `systemctl --user restart adgvs-local.service`, refresh the web
app and open live preview. Physical autofocus still needs confirmation on the actual
camera module; tests use simulated supported/unsupported cameras and focus outcomes.

## Recover from Scan stopped

An interrupted cycle, autofocus failure, or pending review can leave the paper-hold
journal set. Restarting the app preserves this hold so the next sheet cannot feed
into an uncleared path. Remove the sheet, check **I removed the paper and cleared
the path**, then click **Clear stop · Ready for next sheet** directly below progress.
This clears the completed workflow's stop/error state without moving any hardware.

If the camera/OCR operation is still active, recovery shows a waiting message; reset
becomes available after the operation stops. Stop/restart is not evidence that the
paper was physically removed. Autofocus failures are logged locally as well as shown
in the workflow so they can be distinguished from browser heartbeat timeouts.

## Fixed tray positions

The controller uses fixed positions: 180° Up (VALID) and 90° Down (INVALID).
The login app puts the position checks in a Tray position setup popup; its controls
are hidden on the main page. Position testing and Save setup are optional; feeding
does not require calibration or confirmation. Any saved setup is retained per station across
feeds, page refreshes and app restarts. Changing physical setup can be checked again
by reopening the popup. NEEDS REVIEW still holds the paper. Neither a stale browser
request nor the API's scan body can override the automatic routing angles.

After this update, double-click **Restart ADGVS** on the Desktop and refresh the app.
Stop/clear-paper recovery and station/session permission checks remain required.

## Repeated feeding after setup

Use **Feed one sheet & validate** directly for each sheet. The same
open scan session is reused; the tray popup does not reopen after a successful cycle.
Complete a cycle before feeding another sheet. Review/error holds still require
paper removal and acknowledgement. No continuous unattended motor feed is started.
