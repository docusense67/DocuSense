# Scan agent

Runs on the scan station (Raspberry Pi 5 with SANE, or a mini PC / laptop). Pipeline (spec Sections 9–10):

```
PageSource (scanner or folder) -> normalize page (flatten sheet, deskew, A4 @ 300 DPI)
  -> QR decode + footer OCR -> split stack into documents -> lookup zones on backend
  -> zone OCR (fields + grades table) -> POST fields + page images -> VALID/INVALID/REVIEW
```

The agent only **extracts**. The backend decides the result (`backend/app/services/validation.py`). Lookups return template zones and page counts, never student data.

## Ownership

| Part | Owner | Module |
| --- | --- | --- |
| Capture contract | shared | `scan_agent/capture/base.py` |
| File-drop source (images or PDFs) | Software | `scan_agent/capture/filedrop.py` |
| **TWAIN source (Windows) / SANE source (Linux)** | **Hardware** | `scan_agent/capture/twain.py` (to add) / `sane.py` |
| SANE station service (capture → spool → upload) | Software | `scan_agent/station.py` |
| Camera source (Picamera2 / OpenCV / file), auto-capture | Software | `scan_agent/capture/camera.py` |
| Page detection + perspective correction, image-quality check | Software | `scan_agent/capture/page.py`, `scan_agent/quality.py` |
| Intake cycle (feed → capture → screen → upload → route → report), intake API client | Software | `scan_agent/intake.py`, `scan_agent/client.py` |
| **Feeder + gate hardware (pins, motor driver, servo, sensors)** | Hardware (wiring), software (code) | `scan_agent/hardware/` (`gpio.py`, settings in `config.py`) |
| **Jam / double-feed → `ScannerError`, status reporting** | **Hardware** | inside the TWAIN/SANE source; report via `BackendClient.status()` |
| Page clean-up, QR, OCR, parsing | Software | `scan_agent/processing/` |
| Bulk splitting, extraction | Software | `scan_agent/pipeline.py` |
| Backend client, CLI | Software | `scan_agent/client.py`, `scan_agent/__main__.py` |

A hardware source only has to implement `PageSource`: report `status()` and yield `CapturedPage` objects (300 DPI grayscale) in feed order. On a jam it raises `ScannerError(state, next_sequence)`, and the run can resume with `--start-sequence`.

## Run a session

1. An admin registers the station once (**Accounts & stations → Scan stations**) and copies the API key, which is shown only once.
2. The operator picks **Single** or **Bulk** on the dashboard. This opens a scan session, and the dashboard says which output tray to use.
3. Run the agent:

```bash
python -m scan_agent --server https://registrar.school.edu.ph --key $ADGVS_STATION_KEY \
    --ca-cert fullchain.pem --folder ./drop --delete-after-upload
```

The folder can hold page images or PDFs (e.g. the scanner's PDF output), named in stack order. Results print per document and appear on the dashboard.

## Station service (production)

On the real station, run the agent once at sign-in and leave it running. It watches the folder the scanner software saves into:

```bash
python -m scan_agent --server https://adgvs.school.edu.ph --key $ADGVS_STATION_KEY \
    --ca-cert fullchain.pem --folder D:\scans --watch --delete-after-upload
```

- Reports Ready / Receiving pages / Offline to the dashboard every 15 s (`--heartbeat`). If the folder disappears (drive or share not mounted) the station shows **Offline** and new sessions are blocked (UC4). If the agent stops, the dashboard shows "No signal from the station since …".
- A stack is complete when no file has changed for 5 s (`--settle`; raise it for slow scanners). It is checked in the session the operator opened; with no session open, the files wait and the dashboard says to start Single or Bulk.
- If the network drops mid-stack, files whose documents already arrived are finished and the rest are retried, so nothing is lost or listed twice.
- Without `--delete-after-upload` (testing), checked files move to `uploaded/<time>/` instead.

## Scanner driven directly (SANE, the Raspberry Pi station)

On Linux the agent can feed the ADF scanner itself instead of watching another program's
output. This is how the Pi station runs (`deploy/station/adgvs-station.service`; setup in
[docs/hardware/README.md](../docs/hardware/README.md)):

```bash
python -m scan_agent --server https://adgvs.school.edu.ph --key $ADGVS_STATION_KEY \
    --source sane --folder /var/lib/adgvs/scans --watch --delete-after-upload
```

- When a session is open and paper is in the feeder, the stack is fed (300 DPI, 8-bit gray,
  one side per sheet, A4 area) and each page is spooled to `<folder>/.capture/`. When the
  feeder runs empty the pages move into the folder and the watcher uploads them as above:
  same retries, deleted only after the upload (`watch.py` stays the only code that deletes).
- Jam, double feed, open cover: the pages already scanned are kept, the dashboard shows
  "Paper jam: clear the feeder and re-feed from sheet N", and the re-fed sheets continue
  the same stack. Restarting the service gives up on a stopped stack: its pages are checked
  as they are (the unfinished document shows its missing pages), never silently dropped.
- No scanner, USB error, busy device: **Offline**, with the reason as the status detail.
- Simplex on purpose: documents are printed one-sided and every captured image counts as a
  page, so a duplex scan's blank backs would read as extra pages.
- `--device` (or `ADGVS_SANE_DEVICE`): a name from `scanimage -L`; default the first
  sheet-fed scanner. `--sane-option NAME=VALUE` (or `ADGVS_SANE_OPTIONS="a=1 b=2"`): extra
  backend options from `scanimage -A`, e.g. enabling double-feed detection.
- Without `--watch`, `--source sane` scans one stack and submits it (for testing); a jam
  then uploads nothing and the whole stack is scanned again.

## Camera capture (camera compartment station)

The revised station photographs each sheet in a compartment instead of scanning it
(`docs/intake/screening-contract.md`, S-01). The agent measures and reports; the backend
decides.

```
CameraSource: wait for a page that is present and still (preview frames)
  -> focus -> full-resolution still -> find the sheet (capture/page.py: largest bright
  quadrilateral on the dark background, sides fitted as lines) -> perspective-correct to
  A4 at 300 DPI -> measure quality (quality.py) -> recapture blur/exposure/glare up to 3x
  -> CameraPage: normalized page + contract `capture` and `quality` blocks + timings
```

- Backends (`ADGVS_CAMERA_SOURCE` / `--source`): `picamera2` (Raspberry Pi Camera Module 3
  or V2; from apt `python3-picamera2`, so the station venv is created with
  `--system-site-packages` by `pi-setup.sh --camera`, never pip), `opencv` (USB webcam or
  stream URL, largest MJPG mode), `file` (a folder of frames, one page per image: dev,
  tests, replaying real captures).
- `quality` block: `ok`, `issues` (`blur`, `too_dark`, `too_bright`, `glare`, `skew`,
  `page_not_found`, `page_cut_off`, `low_resolution`), `blur_score` (variance of the
  Laplacian on the normalized page), `brightness` and `glare_fraction` (on the page as
  captured, before the paper is levelled to white for OCR), `skew_deg`, `coverage`,
  `effective_dpi`.
- Recapture is capture QA, not screening: only for issues a new shot can fix (blur,
  exposure, glare); a misplaced sheet (skew, cut off, missing, too small) is reported at
  once. After 3 attempts the best one is sent with `ok: false`.
- The paper of a camera page is brightened to scanner white (`page.level_page`, one global
  gain): `cleanup.remove_rules` paints erased table rules white, and on grey paper the
  grades table read nothing.

Try it (Windows dev PC with a webcam, or the Pi):

```bash
python -m scan_agent camera-test --source opencv --device 0 --out camtest   # USB webcam
python -m scan_agent camera-test --source file --device ./frames --out camtest
python -m scan_agent camera-test --source picamera2 --rotate 90 --out /tmp/camtest  # Pi
```

It writes `page.png` (normalized page), `frame.jpg` (the raw frame) and `result.json`
(`capture`, `quality`, `timings_ms` + diagnostics) and prints each step's time and every
quality value next to its threshold. Exit code 0 ok, 1 quality issues, 2 camera unavailable.

Settings (environment, defaults in `scan_agent/config.py`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `ADGVS_CAMERA_SOURCE` | `picamera2` | `picamera2`, `opencv` or `file` |
| `ADGVS_CAMERA_DEVICE` | | camera number / OpenCV index or URL / image folder |
| `ADGVS_CAMERA_ROTATE` | `0` | clockwise turn (0/90/180/270) that makes the page upright |
| `ADGVS_CAMERA_RESOLUTION` | largest | OpenCV only, e.g. `3840x2160` |
| `ADGVS_CAMERA_FOCUS` | `auto` | autofocus cycle per shot, or a lens position in dioptres |
| `ADGVS_CAMERA_EXPOSURE` / `_GAIN` | `auto` | `lock` (meter once, then fixed) or a time in us |
| `ADGVS_CAMERA_AWB` | `auto` | `lock` or `red,blue` colour gains |
| `ADGVS_CAMERA_STILL_FRAMES` / `_STILL_DIFF` | `3` / `2.0` | still preview frames / max mean grey change |
| `ADGVS_CAMERA_TIMEOUT` | `10` | seconds to wait for a still page (then `page_not_found`) |
| `ADGVS_CAMERA_PAGE_ASPECT` | `a4` | `keep`: keep the measured aspect (Letter, folded cards) |
| `ADGVS_CAPTURE_MAX_ATTEMPTS` | `3` | captures per page before sending `ok: false` |
| `ADGVS_QUALITY_BLUR_MIN` | `40` | blur below this |
| `ADGVS_QUALITY_BRIGHTNESS_MIN` / `_MAX` | `0.35` / `0.95` | too dark / too bright |
| `ADGVS_QUALITY_GLARE_MAX` (`_GLARE_LEVEL`) | `0.005` (`250`) | share of clipped pixels |
| `ADGVS_QUALITY_SKEW_MAX_DEG` | `10` | sheet rotation in the frame |
| `ADGVS_QUALITY_COVERAGE_MIN` | `0.10` | smaller bright regions are not a page |
| `ADGVS_QUALITY_DPI_MIN` | `200` | low resolution |

The quality thresholds come from synthetic frames (`tests/camera_frames.py`) and the TOR
fixture: OCR read every field and grade down to a blur score of ~36 and made its first
errors at 30; sharp pages score 190-600. Recalibrate on real captures with `camera-test`.
Station setup: [docs/hardware/README.md](../docs/hardware/README.md#camera-station-bring-up).

## Intake station (feeder + camera + two-tray gate)

`python -m scan_agent intake` runs the incoming-document station
(`scan_agent/intake.py`, contract `docs/intake/screening-contract.md`). The agent measures;
the backend decides passed screening (Tray 1) / manual review (Tray 2); the gate only moves
on that answer.

```
FEED     feeder.feed_one(): fed | empty (-> END) | jam (-> STOP)
BATCH    first fed sheet only: POST /api/station/intake/batches
CAPTURE  CameraSource.capture(): still page, focus, shot, flatten, quality, recapture policy
SCREEN   screening.screen_page() merged with capture/quality/timings_ms (schema 1)
UPLOAD   POST /api/station/intake/documents (JPEG + result + batch_id + sequence)
         -> {intake_id, decision, tray, reasons}       unreachable -> HOLD, refused -> STOP
ROUTE    gate.route(tray): ok (tray reached) | jam -> report jam, STOP
REPORT   POST /documents/{id}/routed {tray} | {jam: true, detail}
CLEAR    compartment empty within ADGVS_INTAKE_CLEAR_TIMEOUT, then FEED; else STOP (jam)
END      feeder empty: POST /batches/{id}/end, dashboard "Batch complete: N sheet(s), ..."
```

Failure handling (the sheet is never routed without a backend decision):

| Situation | What the station does | Dashboard | `--once` exit |
| --- | --- | --- | --- |
| Feeder empty at the start | nothing (no batch is opened) | Ready "Waiting for paper" | 0 |
| Network error / 5xx | client retries 4x (1, 2, 4 s), then HOLD: sheet stays in the compartment, upload retried every 10 s for 120 s, then STOP: "take sheet N out" | Offline "sheet N held" / "Stopped: server unreachable" | 4 |
| 4xx on upload (422 malformed, 409 batch ended, 401 key) | no retry, STOP, sheet held: staff review it by hand | Offline "Stopped: the server refused sheet N" | 5 |
| Camera / screening failure | STOP, sheet held | Offline "Stopped: ..." | 5 |
| Feeder jam | STOP (no document to report) | Jam | 3 |
| Gate jam | routed callback `{jam: true, detail}` (backend -> manual review), STOP | Jam | 3 |
| Gate reached another tray | reported as is (backend adds `routed_tray_mismatch`) | - | - |
| Routed callback unreachable | kept and re-sent before the next upload / at batch end | - | - |
| Routed callback 409 (already reported differently) | logged; the backend keeps the first report | - | - |

Service mode (no `--once`, `adgvs-intake.service`): a stop is latched and re-reported every
heartbeat. A held sheet (server, camera, refused) resumes automatically once the
compartment has been clear for `ADGVS_INTAKE_RESUME_AFTER` s; a jam needs a person to clear
it and `sudo systemctl restart adgvs-intake`. A retried upload whose first response was lost
can create a second intake record (the first stays "unrouted" in the batch summary); the
backend has no idempotency key yet.

Logs carry sheet numbers, the predicted type, decision, reason codes and per-step timings
(`feed capture quality screening upload route report clear` ms), never field values.

```bash
python -m scan_agent intake --check                      # validate + print settings, moves nothing
python -m scan_agent intake --hardware manual --source picamera2 --once    # operator = feeder + gate
python -m scan_agent intake --feeder manual --gate gpio --once             # gate bring-up
python -m scan_agent intake --hardware gpio                                # service (systemd)
python -m scan_agent intake --hardware fake --source file --device ./frames --once   # dry run
```

Hardware (`scan_agent/hardware/`): `Feeder` (`feed_one()`, `sheet_present()`,
`paper_loaded()`) and `Gate` (`route(tray)`, `sheet_cleared()`), implemented by `fake.py`
(tests), `manual.py` (terminal prompts) and `gpio.py` (gpiozero: feed motor as
`OutputDevice` or H-bridge `Motor`, `AngularServo` gate, `DigitalInputDevice` sensors,
optional eject motor). gpiozero + lgpio come from apt on the Pi (`pi-setup.sh --intake`),
are imported only for gpio, and are never pinned in `constraints.txt`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ADGVS_INTAKE_HARDWARE` | `manual` (unit: `gpio`) | `gpio`, `manual` or `fake` |
| `ADGVS_INTAKE_HOLD_TIMEOUT` / `_RETRY_INTERVAL` | `120` / `10` | seconds a sheet is held while the server is unreachable / between tries |
| `ADGVS_INTAKE_HTTP_ATTEMPTS` / `_HTTP_BACKOFF` | `4` / `1.0` | tries per request (network errors, 5xx) / first backoff, doubling, max 8 s |
| `ADGVS_INTAKE_CLEAR_TIMEOUT` | `30` | after routing: seconds for the compartment to be empty |
| `ADGVS_INTAKE_RESUME_AFTER` | `5` | service: compartment clear this long after a held-sheet stop |
| `ADGVS_INTAKE_IDLE_POLL` / `_HEARTBEAT` | `2` / `15` | idle paper check / status report interval |
| `ADGVS_INTAKE_JPEG_QUALITY` | `90` | uploaded page (the backend recompresses it) |
| `ADGVS_FEEDER_MOTOR_PIN` | required (gpio) | feed motor (relay/MOSFET) or H-bridge forward |
| `ADGVS_FEEDER_MOTOR_BACKWARD_PIN` / `_ENABLE_PIN` / `_SPEED` | none / none / `1.0` | H-bridge (gpiozero `Motor`) |
| `ADGVS_FEEDER_MOTOR_ACTIVE_HIGH` | `1` | `0` for active-low relay boards |
| `ADGVS_FEEDER_SHEET_PIN` | required (gpio) | sheet-in-compartment sensor (shared with the gate) |
| `ADGVS_FEEDER_STACK_PIN` | none | paper-on-feeder sensor: tells empty from misfeed; recommended |
| `ADGVS_FEEDER_SENSOR_PULL_UP` | `1` | sensor pulls the pin low when it sees paper |
| `ADGVS_FEEDER_FEED_TIMEOUT` / `_OVERRUN` | `5` / `0.15` | seconds for a sheet to arrive / motor run-on after the sensor trips |
| `ADGVS_GATE_SERVO_PIN` | required (gpio) | routing servo signal |
| `ADGVS_GATE_TRAY1_ANGLE` / `_TRAY2_ANGLE` / `_HOME_ANGLE` | `-45` / `45` / `0` | degrees; home holds the sheet in the compartment |
| `ADGVS_GATE_MIN_ANGLE` / `_MAX_ANGLE` | `-90` / `90` | servo travel mapped to the pulse range |
| `ADGVS_GATE_MIN_PULSE_MS` / `_MAX_PULSE_MS` | `1.0` / `2.0` | servo pulse widths |
| `ADGVS_GATE_MOVE_TIME` / `_DETACH` | `0.6` / `1` | seconds to reach an angle / stop pulses once there |
| `ADGVS_GATE_EJECT_PIN` / `_EJECT_ACTIVE_HIGH` / `_EJECT_TIME` | none / `1` / `1.5` | optional motor/solenoid pushing the sheet out |
| `ADGVS_GATE_EXIT_PIN` | none | optional sensor on the way to the trays |
| `ADGVS_GATE_SENSOR_PULL_UP` | `1` | as for the feeder sensors |
| `ADGVS_GATE_CLEAR_TIMEOUT` | `5` | seconds for the sheet to leave after the gate moved, else jam |

Pins are BCM numbers (`17`, `GPIO17`) or `BOARD<n>` (physical header pin). Every value is
checked at start-up (all problems listed, exit 2); `intake --check` prints them. Wiring
and bring-up: [docs/hardware/README.md](../docs/hardware/README.md#intake-station-feeder-gate-and-trays).

## Options

- `--ca-cert`: the server's certificate (or the school's CA) when it is self-signed or campus-issued. Omit for a publicly trusted certificate.
- `--delete-after-upload`: on the production station, removes the input files once every document in the batch was uploaded (Section 18). Leave it off when testing with sample files you want to keep. After a jam the files stay, and the run resumes with `--start-sequence`.

## What's verified (tests/)

- Clean 300 DPI TOR/COE: every field and every grade row matches the issued snapshot.
- A 3-page TOR: rows are kept under the right term across page breaks.
- A mixed bulk stack is split into the right documents; a missing middle page is visible.
- A skewed, noisy scan on a dark scanner bed still reads.
- A simulated clean scan of the 3-page TOR reads exactly like the noiseless render. Table and
  field crops are thresholded before Tesseract (its LSTM reads the grey image, so scanner
  noise used to turn 1.75 into "1/5"), and a row's doubtful code or malformed grade is read
  again on its own. CI's 100-document set (`ocr-accuracy` job) reads 100% of grades.
- An overprinted grade is read as printed, so the backend can flag it.
- Speed on a laptop CPU: about 2 s per one-page document and 4.6 s for a 3-page TOR (spec: under 15 s).
- Camera: every quality issue code fires on its synthetic defect and only then; sheet
  corners are found to within 1.5 px under rotation and keystone; a TOR photographed in
  perspective (and lying across a 16:9 frame) OCRs exactly like the flat page after
  correction; Picamera2 (fake module) and OpenCV paths, auto-capture and the recapture
  policy are covered.
- Intake cycle (`tests/test_intake*.py`): fake feeder/gate + the file camera + a fake backend
  mirroring `backend/app/api/intake.py` (passed -> Tray 1, flagged/unknown -> Tray 2, jams,
  empty feeder, server down holds the sheet and never routes, refused uploads, 409 on the
  routed callback, lost routed reports re-sent); the client's retries and request shapes;
  the GPIO feeder/gate against a fake `gpiozero` paper-path simulation; settings validation.

Real scans may differ from rendered PDFs. Re-run the tests with actual scans from the station during the Epic 8 OCR accuracy run.

## Screening incoming documents (S-02, S-03, S-05)

`scan_agent/screening/` turns one normalized page (perspective-corrected, ~300 DPI) into the
`doc_type`, `checks`, `indicators`, `similarity` and `fields` blocks of
[docs/intake/screening-contract.md](../docs/intake/screening-contract.md). It only measures
and extracts; passed screening / manual review and the tray are decided by the backend.
Types: PSA Certificate of Live Birth, JHS report card (SF9), Certificate of Good Moral
Character, JHS Certificate of Completion, else `unknown`. OpenCV, numpy and Tesseract only.

```bash
python -m scan_agent screen page.jpg [--refs DIR | --no-refs]     # prints the JSON
python -m scan_agent screening-enroll --type psa_birth_certificate --refs DIR samples/psa/
python -m scan_agent screening-eval --dir samples/ --refs DIR      # accuracy, flags, ms/page
python -m scan_agent screening-calibrate --dir samples/ --refs DIR # refit the confidence
python -m scan_agent screening-eval --synthetic 15 --rough 3       # synthetic SAMPLE set
```

```python
from scan_agent.screening import screen_page, classify, screen, similarity
result = screen_page(image, capture=..., quality=..., timings_ms=...)  # capture/quality passed through
```

### How each number is measured

The page is read by Tesseract once (full page, rules erased, thresholded); every step works
on those words and on the image. Reference profiles (`screening/profiles/*.json`, format in
[profiles/README.md](scan_agent/screening/profiles/README.md)) say what each form should
contain and where.

| Output | How |
| --- | --- |
| `doc_type` | Per type: weighted share of the profile's keywords found (half weight outside the expected zone; fuzzy match >= 0.88 so "CERTIFICATE OF RECOGNITION" is not "... OF COMPLETION"). Once every type has references: 0.65 x keywords + 0.35 x similarity. Calibrated with a logistic fit on (score, margin to the next type) -> per-type probability; `unknown` if the best is < 0.5. |
| headings / fields found | Heading phrase found anywhere; field = label found near its zone and a value read in the value zone (which moves with the label). |
| `layout_score` | Found headings and labels vs. their expected positions after a page-level shift (or similarity transform, if it fits more elements): 1 within tolerance, falling to 0 at 3x tolerance; mean. |
| `seal_logo_score` | Normalized cross-correlation of each seal/logo template (75 DPI, 3 scales) inside its zone; mean. `null` without templates. |
| `font_consistency` | Glyph height (90th-percentile connected component, independent of which letters a word has) of each member of a font group; 1 - mean((max - min) / median). |
| `alignment_score` | Glyph baselines / left edges of group members (page tilt removed); per group 1 - worst deviation / (3 x tolerance); mean. |
| `unexpected_text` | Confident words outside headings, labels, value zones, seals and the profile's text zones (with 3+ references: outside the places where references have text). |
| `altered_region` | Paper level per 6 mm cell (95th percentile) minus a robust quadratic lighting surface and an 11x11-cell median: pasted slips, correction paper and digital patches keep a different paper tone. Printed tints and seals excluded. |
| `similarity` | Against stored references of the predicted type, value zones whited out: ORB (1000 features, ~85 DPI) matches that agree on one near-identity transform + ink-density layout correlation, 50/50; mean of the best 3. Omitted without references. |

Severity of every indicator = deviation / (2 x profile tolerance), clipped to 0..1: 0.5 means
exactly at the profile's tolerance. Missing headings/labels have fixed severities (0.8/0.7),
a blank or unreadable value 0.3 (blank fields occur on genuine forms).

### Enrolling the client's samples

Real samples contain minors' personal data: keep them and the reference store off the
repository (`.gitignore` covers `screening-refs/`, `.samples/`, `client-samples/`). On the
station set `ADGVS_SCREENING_REFS=/var/lib/adgvs/screening-refs` (default
`~/.adgvs/screening-refs`).

1. Sort the samples into a labelled folder: `samples/<doc_type>/` (`unknown/` for other
   paper), optionally `samples/tampered/<indicator_code>/<doc_type>/` for known alterations.
   Keep about a third aside (not enrolled) to measure on.
2. Enroll the rest per type: `screening-enroll --type <t> --refs DIR samples/<t>/`. The store
   gets a value-masked copy, ORB/layout features, measured positions, text occupancy and
   paper tone per sample; missing seal templates are cut from the first sample (check
   `DIR/<t>/templates/*.png` by eye). From 3 samples per layout, measured positions and
   text areas replace the profile's guesses. Re-enrolling a file replaces it; `--clear`
   empties a type (do this to drop the synthetic bootstrap, `--synthetic N`).
3. `screening-eval --dir samples-held-out/ --refs DIR` lists accuracy and every page with an
   indicator >= 0.5. Fix the profile JSON (keywords, zones, tolerances, a second variant file
   for a different layout, e.g. the report card's inside page) where genuine pages are
   flagged, then `screening-calibrate --dir samples/ --refs DIR` (writes
   `DIR/calibration.json`, used instead of the packaged synthetic fit).

### Measured on synthetic SAMPLE pages

Dev PC (Ryzen 3 PRO 4350G, Windows, Tesseract 5.4), held-out seeds, 251 pages per run:
60 clean pages (15 per type), 84 tampered (7 tampers x 4 types x 3), 80 out-of-scope (receipt,
DepEd memo, certificate of recognition, enrollment form, blank sheet; this system's TOR and
COE), 27 poor captures.

| | profiles only | + 8 synthetic references per type |
| --- | --- | --- |
| type accuracy, clean | 60/60 | 60/60 |
| out-of-scope -> unknown | 80/80 | 80/80 |
| type accuracy, tampered / poor captures | 84/84, 12/12 | 84/84, 12/12 |
| clean pages with an indicator >= 0.5 | 0/60 (max 0.32) | 0/60 (max 0.33) |
| each tamper raises its indicator >= 0.5 | 12/12 each (seal: needs a template) | 12/12 each, all 7 |
| field values read exactly as printed | 97.7 % | 97.0 % |

Lowest correct confidence 0.96; poor captures raise a serious indicator on 5/12 pages
(each a missing heading: small bold headings blur first), which the quality check should
prevent. Time per page,
one process: p50 0.94 s, max 1.7 s (OCR ~70 %); expect ~2-3x on the Pi 5 (about 2-5 s).
`tests/screening/` (81 tests) repeats a smaller version on every run and prints the numbers.

What this does NOT show: the synthetic forms are look-alikes drawn by our own generator
(one font, invented seals, no security-paper pattern, no handwriting, no stamps over text,
no real camera), and the profiles were bootstrapped from the same layouts, so these numbers
prove the measurement code, not accuracy on the client's paper. The client's samples
(about 10 per type by Oct 14, 30 by Nov 1) are the first real measurement. Known gaps:
handwritten values are not read (blank-value indicators, low severity); school-issued
certificates and report cards vary by school, so their tolerances are loose and similarity
favours schools that were enrolled; a white slip pasted on white paper leaves almost no
tone difference; template matching assumes printed (not embossed or rotated) seals.

## Station prerequisites

- Python 3.12 and Tesseract 5 (`tesseract` on PATH, or set `TESSERACT_CMD`). zbar comes bundled with the `pyzbar` wheels on Windows; on Linux, `apt install libzbar0`.
- Scanner driver: TWAIN (Windows) or SANE (Linux). Confirm the exact model works before buying
  (`scanimage -L` lists it; the SANE backend must exist for ARM64 on the Pi).
- `python-sane` has no wheels: on Linux it builds from source and needs `libsane-dev`,
  `python3-dev` and a C compiler (`deploy/station/pi-setup.sh` installs them).
- Camera station: Picamera2 is not pip-installable on Raspberry Pi OS Trixie. It comes from
  apt (`python3-picamera2`) and the venv must see it: `python3 -m venv --system-site-packages`
  (`pi-setup.sh --camera` does both). Never add `picamera2` (or `python-sane`) to
  `constraints.txt`: CI's pip-audit would try to install it. Tests use a fake module.

```bash
pip install -e ".[dev,twain]"   # or [dev,sane] on Linux
```

All pinned dependencies in `constraints.txt` have Python 3.13 manylinux aarch64 wheels (or
are pure Python), so the Pi installs them without compiling; only `python-sane` builds.
