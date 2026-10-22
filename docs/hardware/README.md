# Scan station hardware

**Status:** Raspberry Pi 5 build; the
scanner model and the status display are still to be confirmed (rows marked TBD).

The station is an off-the-shelf ADF (sheet-fed) scanner on USB, a Raspberry Pi 5 running the
scan agent as a service, and an enclosure with the two labelled output trays (Single, Bulk).
The operator works on the web dashboard (any PC or tablet browser); the Pi has no desktop.

```
[ADF scanner] --USB--> [Raspberry Pi 5: SANE -> scan agent] --HTTPS--> ADGVS (/api)
                         \-- (later) small TFT LCD: station status
```

Hardware deliverables still to add here (Section 26): suppliers and prices, wiring diagram
(power, USB, network), photos of the assembled station, the enclosure with its two trays.

## Deviation from the spec (Section 9): Raspberry Pi instead of a mini PC

Section 9 recommends a mini PC because OCR is slower on a Pi and many scanners have no ARM
driver. We build on a Raspberry Pi 5 **on two conditions**, both checked before the scanner
is bought and again on the finished station:

1. **Driver:** the scanner works through a SANE backend on ARM64 (Linux). Look the model up
   in the [SANE supported devices list](http://www.sane-project.org/sane-mfgs.html); a
   vendor driver that only ships for x86 does not count. Proof: `scanimage -L` lists it on
   the Pi and a 300 DPI gray ADF batch scans (see [Scanner bring-up](#scanner-bring-up)).
2. **Speed:** a document's result within **15 s** (Section 21). `check-station.sh` times
   the agent's OCR on the bundled TOR/COE fixtures; the budget also covers scanning and the
   upload, so OCR alone should stay well under it. Re-check with real scans during the
   Epic 8 OCR accuracy run.

**Fallback:** if either condition fails, the same scan agent runs on a mini PC or laptop:
Linux with SANE uses the same service and scripts (any Debian 13 / Python 3.12+ machine),
or Windows with the scanner's own software saving into a folder and the agent in `--watch`
mode ([admin installation guide](../admin-installation-guide.md), section 10). Nothing in
the backend or dashboard changes.

## Parts list

| Item | Specification | Qty | Supplier | Price (PHP) | Status |
| --- | --- | --- | --- | --- | --- |
| Single-board computer | Raspberry Pi 5, 8 GB | 1 | TBD | TBD | chosen |
| Power supply | Official Raspberry Pi 27 W USB-C (5.1 V / 5 A) | 1 | TBD | TBD | chosen |
| Cooling | Raspberry Pi 5 Active Cooler (OCR runs the CPU hard) | 1 | TBD | TBD | chosen |
| Storage | microSD 32 GB+ A2 class (or NVMe SSD on an M.2 HAT+) | 1 | TBD | TBD | chosen |
| Case | Pi 5 case with room for the active cooler | 1 | TBD | TBD | chosen |
| Network | Ethernet cable to the office switch (preferred over Wi-Fi) | 1 | TBD | TBD | chosen |
| **ADF scanner** | Sheet-fed, ≥ 25 ppm, 300 DPI, 50-sheet feeder, double-feed detection, **SANE backend on ARM64** | 1 | TBD | TBD | **TBD** |
| **Status display** | Small TFT LCD (size, interface and driver TBD) | 1 | TBD | TBD | **TBD** |
| Enclosure and trays | Enclosure with two labelled output trays (Single, Bulk) | 1 | TBD | TBD | TBD |
| UPS (optional) | Small UPS for the Pi and scanner | 1 | TBD | TBD | optional |

## 1. Flash the SD card (Raspberry Pi Imager)

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on the Windows PC.
2. Create an SSH key once if you have none (PowerShell or Git Bash):
   `ssh-keygen -t ed25519 -C "adgvs-station"`. The public key is
   `C:\Users\<you>\.ssh\id_ed25519.pub`.
3. In Imager choose **Raspberry Pi 5**, then **Raspberry Pi OS (other) → Raspberry Pi OS
   Lite (64-bit)** (Debian 13 Trixie; Bookworm's Python 3.11 is too old), then the card.
4. OS customisation:
   - Hostname: `adgvs-station`
   - Username `adgvs`, with a strong password (console only; SSH will use the key)
   - Wi-Fi (only if no Ethernet): SSID, password, **Wireless LAN country: PH**
   - Locale: time zone **Asia/Manila**, keyboard `us`
   - Services: **Enable SSH → Allow public-key authentication only**, paste the contents of
     `id_ed25519.pub`
5. Write, put the card in the Pi, connect Ethernet and the scanner, and power on.

## 2. First SSH from Windows

Add this to `C:\Users\<you>\.ssh\config` (create the file if needed):

```
Host adgvs-station
    HostName adgvs-station.local
    User adgvs
```

Then `ssh adgvs-station`. If `adgvs-station.local` does not resolve on the school network,
use the Pi's IP address from the router or switch as `HostName`.

## 3. Install the station software (`pi-setup.sh`)

`deploy/station/pi-setup.sh` provisions the Pi and can be run again at any time (updates,
repairs). It installs Tesseract, zbar and SANE; creates the service user `adgvs-agent` (in
the `scanner` group), `/opt/adgvs` (agent, venv), the spool folder `/var/lib/adgvs/scans`,
and `/etc/adgvs/station.env` (from a template, mode 600, **never overwritten**); installs
the `adgvs-station` service; enables unattended security upgrades, a firewall that allows
only SSH, time zone Asia/Manila with NTP, and key-only SSH (once a key is installed). The
first run compiles `python-sane`, which takes a few minutes.

**A. From a checkout on the Windows PC** (Git Bash, in the repository folder):

```bash
tar -czf - --exclude=.venv --exclude=__pycache__ scan-agent backend/app/documents/defaults \
    deploy/station | ssh adgvs-station 'rm -rf ~/adgvs-src && mkdir ~/adgvs-src && tar -xzf - -C ~/adgvs-src'
ssh -t adgvs-station 'sudo bash ~/adgvs-src/deploy/station/pi-setup.sh --from-dir ~/adgvs-src'
```

(`scp -r` or `rsync -a --exclude .venv` from Linux/WSL work too: copy at least `scan-agent/`,
`backend/app/documents/defaults/` and `deploy/station/`.)

**B. From GitHub** (on the Pi; a private repository needs a deploy key or read-only token):

```bash
sudo apt-get install -y git
git clone --depth 1 https://github.com/docusense67/DocuSense.git ~/adgvs-src
sudo bash ~/adgvs-src/deploy/station/pi-setup.sh --git https://github.com/docusense67/DocuSense.git --ref main
```

The `--git` run keeps its own checkout in `/opt/adgvs/src`; to update later run
`sudo bash /opt/adgvs/src/deploy/station/pi-setup.sh --git <url> --ref main`.

**Camera station:** add `--camera` to any of the commands above (see
[Camera station bring-up](#camera-station-bring-up)). It stays on for later runs;
`--no-camera` goes back to the scanner-only setup. The SANE scanner path is unchanged.

## 4. Register the station and fill `/etc/adgvs/station.env`

1. In ADGVS, as admin: **Accounts & stations → Scan stations → register** the station.
   Copy the API key: it is shown only once.
2. On the Pi: `sudo nano /etc/adgvs/station.env` and set

   | Setting | Value |
   | --- | --- |
   | `ADGVS_SERVER` | The API's own address, `https://adgvs-api.onrender.com` in the cloud setup (on-prem: the school's ADGVS address). Not the Vercel site: scan uploads are several MB and must not pass through the Vercel proxy's request-size limits |
   | `ADGVS_STATION_KEY` | The key from step 1 |
   | `ADGVS_CA_CERT` | Empty for a public certificate; else a PEM file such as `/etc/adgvs/ca.pem` (mode 644) |
   | `ADGVS_SCAN_FOLDER` | `/var/lib/adgvs/scans` (default) |
   | `TESSERACT_CMD` | `/usr/bin/tesseract` |
   | `ADGVS_SANE_DEVICE` | Optional: device name from `scanimage -L` (default: first sheet-fed scanner) |
   | `ADGVS_SANE_OPTIONS` | Optional: extra scanner options, e.g. `"df-action=Stop"` |

3. `sudo systemctl restart adgvs-station`. The operator's **Scan station** screen shows
   **● Ready**. The API on Render's free tier sleeps when idle; the station's 15 s status
   reports keep it awake while the station runs, but the first call after a pause can take
   up to a minute.

If the Pi is lost or replaced, deactivate the station in **Scan stations** (the key stops
working) and register the new one.

## 5. Check the station (`check-station.sh`)

```bash
sudo /opt/adgvs/station/check-station.sh          # ~1 min, includes the OCR timing run
sudo /opt/adgvs/station/check-station.sh --quick  # without the OCR timing
```

It checks the OS and clock (NTP), Python, Tesseract (+ English data), zbar, python-sane,
`scanimage -L` as the service user, the spool folder, `ADGVS_SERVER/api/health`, that the
key is set (never printed), the service state and its last log lines, and times the OCR of
the bundled TOR/COE fixtures against the 15 s budget. It also prints the CPU temperature and
throttling flags: anything but `throttled=0x0` means under-voltage or heat (use the 27 W
supply and the active cooler). Logs: `journalctl -u adgvs-station -f`.

## Scanner bring-up

Once the scanner model is known:

1. `sane-find-scanner` (USB seen?) and `scanimage -L` (SANE backend found?).
2. `scanimage -d '<device>' -A` lists its options: the feeder source name (e.g. `ADF`,
   `ADF Front`), gray mode, 300 DPI, and any double-feed detection options.
3. Test a batch: `scanimage -d '<device>' --source 'ADF Front' --mode Gray --resolution 300
   --batch=/tmp/page%03d.png --format=png` with a few sheets in the feeder.
4. The agent picks a single-sided feeder source, gray, 8-bit, 300 DPI and the A4 area by
   itself. Turn on the scanner's double-feed detection with `ADGVS_SANE_OPTIONS` if its
   backend has an option for it, then restart the service.
5. Feed two stuck sheets on purpose (Section 24 demo): the dashboard must show
   "Double feed" or "Paper jam" with the sheet to re-feed, and the stack must complete
   after re-feeding.

If the dashboard says Offline while `scanimage -L` works, check
`journalctl -u adgvs-station`: the service only allows USB devices (`DeviceAllow` in
`/etc/systemd/system/adgvs-station.service`).

## Camera station bring-up

The client's revised design (pre-approval, Annex A) replaces the ADF scanner with a
**camera scanning compartment**: an automatic feeder drops one sheet into the compartment,
a Raspberry Pi camera photographs it, and a two-tray gate routes it after the backend's
screening decision. Software: `scan-agent/scan_agent/capture/camera.py` (camera),
`capture/page.py` (find + flatten the sheet), `quality.py` (image-quality check). Check the
camera alone with `camera-test` (below); the full feeder -> camera -> gate cycle is
[Intake station: feeder, gate and trays](#intake-station-feeder-gate-and-trays).

| Camera | Sensor | Focus | A4 across the frame | A4 upright |
| --- | --- | --- | --- | --- |
| Camera Module 3 (target) | imx708, 4608 x 2592 (16:9) | autofocus | ~280-390 DPI | ~200 DPI |
| Camera Module V2 (on hand, tests) | imx219, 3280 x 2464 (4:3) | fixed (turn the lens for 20-30 cm) | ~250 DPI | ~195 DPI (flagged) |

OCR is tuned for 300 DPI and the quality check flags pages under 200 DPI, so mount the
camera with **the sheet lying across the frame** (long side along the sensor's width) and
set `ADGVS_CAMERA_ROTATE` (90 or 270) so the page comes out upright.

1. **Connect** the camera to a Pi 5 camera port. The Pi 5 ports are the small 22-pin type:
   Camera Module 3 and V2 need a 22-to-15-pin camera cable. `camera_auto_detect=1` in
   `/boot/firmware/config.txt` (the default) finds both modules.
2. **Install:** `sudo bash ~/adgvs-src/deploy/station/pi-setup.sh --from-dir ~/adgvs-src --camera`.
   This adds `python3-picamera2` and `rpicam-apps-lite` from the Raspberry Pi archive,
   adds the service user to the `video` group, installs the systemd drop-in
   `/etc/systemd/system/adgvs-station.service.d/camera.conf` (the service otherwise only
   allows USB devices) and recreates the venv with `--system-site-packages`.
   *Why:* Picamera2 and libcamera's Python bindings are built for the OS and are not
   installable with pip on Trixie; a venv only sees them with system site-packages. The
   pinned packages from `constraints.txt` are still installed in the venv and take
   precedence over the apt ones. `pi-setup.sh` checks that `from picamera2 import
   Picamera2` works inside the venv.
3. **See the camera:** `rpicam-hello --list-cameras` lists `imx708` (Module 3) or `imx219`
   (V2). `sudo /opt/adgvs/station/check-station.sh` shows a **Camera** section.
4. **Test capture** with a sample page (marked SAMPLE) in the compartment:

   ```bash
   sudo -u adgvs-agent /opt/adgvs/venv/bin/python -m scan_agent camera-test \
       --source picamera2 --rotate 90 --out /tmp/camtest
   scp adgvs-station:/tmp/camtest/{page.png,frame.jpg,result.json} .   # look at them on the PC
   ```

   It waits for a still page (10 s), focuses, takes the full-resolution still, flattens
   the page and prints the timings and each quality value next to its threshold. Exit
   code 0 = quality ok, 1 = captured with issues, 2 = camera unavailable (the reason is
   printed). `page.png` must be upright, flat, with no background at the edges; if it is
   upside down use the other rotation (90 <-> 270).
5. **Tune** the compartment until `camera-test` reports `Verdict OK` on several pages:

   | Reading | Aim | If not |
   | --- | --- | --- |
   | `effective_dpi` | >= 280 (min 200) | camera closer / page across the frame |
   | `coverage` | 0.6-0.9 | page fills the frame, with a margin on every side |
   | `brightness` | 0.75-0.88 | more / less light, or a fixed exposure |
   | `glare_fraction` | 0.000 | move or diffuse the LEDs: no reflection into the lens |
   | `blur_score` | well above 40 | focus (V2: turn the lens), vibration, `ADGVS_CAMERA_FOCUS` |
   | `skew_deg` | within +-3 | feeder guides |
   | `page_cut_off` / `page_not_found` | never on a fed sheet | framing; a **matte dark background** under the sheet |

   - **Background:** plain, matte and darker than paper (black or dark grey felt/card).
     The page is found as the bright quadrilateral on it.
   - **Light:** two diffuse LED strips from the sides at a low angle; glare (clipped
     highlights) wipes out print and is flagged.
   - **Exposure:** `ADGVS_CAMERA_EXPOSURE=lock` meters once when the camera starts and
     then keeps it, so every page looks the same (or give a fixed time in microseconds,
     plus `ADGVS_CAMERA_GAIN`). `ADGVS_CAMERA_AWB=lock` does the same for colour.
   - **Focus (Module 3):** an autofocus cycle runs before each still (~0.3-1 s). With a
     fixed compartment height set the lens position instead, in dioptres (1 / distance in
     metres, e.g. `ADGVS_CAMERA_FOCUS=3.3` for 30 cm): faster and repeatable.
6. **Thresholds** are defaults from synthetic tests (`scan_agent/config.py`,
   `QualityThresholds`); recalibrate them on real captures and set overrides in
   `/etc/adgvs/station.env`, e.g. `ADGVS_QUALITY_BLUR_MIN=60`. Capture QA only: an
   unusable frame is recaptured up to 3 times (`ADGVS_CAPTURE_MAX_ATTEMPTS`), then sent
   with `quality.ok=false` and the backend decides (manual review).

Time budget: the whole screening of a page within 15 s. Capture plus quality is about 1-2 s
on the Pi (wait for stillness ~0.3 s, autofocus up to ~1 s, still + page + quality
~0.5 s); `camera-test` prints each part.

## Intake station: feeder, gate and trays

The full cycle runs as `adgvs-intake.service` (`python -m scan_agent intake`,
`scan-agent/scan_agent/intake.py`; software reference in
[scan-agent/README.md](../../scan-agent/README.md#intake-station-feeder--camera--two-tray-gate)):
the feeder drops **one sheet** into the compartment, the camera captures it, the agent
screens it and uploads it, the **backend** decides, and the gate sends the sheet to
**Tray 1 (passed screening)** or **Tray 2 (needs manual review)**. Without a backend
decision the gate does not move: the sheet waits in the compartment.

```
[stack] -feed motor-> [compartment: sheet sensor, camera above] -gate servo-> Tray 1 / Tray 2
   ^ stack sensor (optional)                    \-> eject motor (optional) -> exit sensor (optional)
```

### Wiring (to be filled in)

Nothing below is fixed in code: every pin, angle and timeout is a setting in
`/etc/adgvs/station.env` (`ADGVS_FEEDER_*`, `ADGVS_GATE_*`, defaults in
`scan-agent/scan_agent/config.py`), checked at start-up. Pins are **BCM numbers** (GPIO17 ->
`17`), or `BOARD<n>` for the physical header pin.

| Function | Part (model) | Pi pin (BCM / header) | Setting | Electrical notes | Status |
| --- | --- | --- | --- | --- | --- |
| Feed motor | TBD (DC gear motor + driver) | TBD | `ADGVS_FEEDER_MOTOR_PIN` | via relay/MOSFET or H-bridge, never directly from a GPIO; flyback diode on a bare motor | **TBD** |
| Feed motor reverse (H-bridge only) | TBD | TBD | `ADGVS_FEEDER_MOTOR_BACKWARD_PIN` | omit for a one-direction relay/MOSFET | TBD |
| Feed motor speed (H-bridge enable, PWM) | TBD | TBD | `ADGVS_FEEDER_MOTOR_ENABLE_PIN`, `_SPEED` | omit if the enable is tied high | TBD |
| Relay polarity | TBD | - | `ADGVS_FEEDER_MOTOR_ACTIVE_HIGH` | `0` for active-low relay boards | TBD |
| Sheet-in-compartment sensor (**required**) | TBD (IR reflective / break-beam / microswitch) | TBD | `ADGVS_FEEDER_SHEET_PIN` | 3.3 V logic only (Pi GPIO is not 5 V tolerant); open collector to GND with the internal pull-up = `ADGVS_FEEDER_SENSOR_PULL_UP=1` | **TBD** |
| Paper-on-stack sensor (recommended) | TBD | TBD | `ADGVS_FEEDER_STACK_PIN` | without it an empty feeder and a misfeed look the same | TBD |
| Gate servo signal (**required**) | TBD (servo model, torque) | TBD | `ADGVS_GATE_SERVO_PIN` | servo on its own 5-6 V supply, grounds joined with the Pi; signal 3.3 V | **TBD** |
| Servo pulse range | from the servo datasheet | - | `ADGVS_GATE_MIN_PULSE_MS` / `_MAX_PULSE_MS` (1.0 / 2.0) | some servos 0.5-2.5 ms | TBD |
| Servo travel | from the datasheet | - | `ADGVS_GATE_MIN_ANGLE` / `_MAX_ANGLE` (-90 / 90) | | TBD |
| Gate angle: Tray 1 | measured at bring-up | - | `ADGVS_GATE_TRAY1_ANGLE` (-45) | | TBD |
| Gate angle: Tray 2 | measured at bring-up | - | `ADGVS_GATE_TRAY2_ANGLE` (45) | | TBD |
| Gate angle: home (holds the sheet) | measured at bring-up | - | `ADGVS_GATE_HOME_ANGLE` (0) | | TBD |
| Eject motor / solenoid (optional) | TBD | TBD | `ADGVS_GATE_EJECT_PIN`, `_EJECT_ACTIVE_HIGH`, `_EJECT_TIME` | only if gravity does not empty the compartment | TBD |
| Exit sensor (optional) | TBD | TBD | `ADGVS_GATE_EXIT_PIN` | on the path to the trays: confirms the sheet left | TBD |
| Timings | measured at bring-up | - | `ADGVS_FEEDER_FEED_TIMEOUT` (5 s), `_OVERRUN` (0.15 s), `ADGVS_GATE_MOVE_TIME` (0.6 s), `_CLEAR_TIMEOUT` (5 s) | | TBD |

Also needed: the power budget (motor + servo supply, current), whether the
compartment empties by gravity when the gate turns (else the eject motor), which way each
sensor reads (active low/high), and photos of the wiring for the deliverables.

### Software setup

```bash
sudo bash ~/adgvs-src/deploy/station/pi-setup.sh --from-dir ~/adgvs-src --intake
```

`--intake` implies `--camera`, installs `python3-gpiozero` + `python3-lgpio` from apt (never
pip), adds the service user to the `gpio` group, installs `adgvs-intake.service` and
**disables `adgvs-station.service`** (one station mode at a time; `--no-intake` switches
back). The service is started only once `ADGVS_SERVER`, `ADGVS_STATION_KEY` and the
feeder/gate pins validate. `ADGVS_SERVER` is the **Render URL** (not the Vercel site).
An existing `/etc/adgvs/station.env` is never overwritten: copy the `ADGVS_INTAKE_*`,
`ADGVS_FEEDER_*` and `ADGVS_GATE_*` lines from the template in `pi-setup.sh` into it.
`sudo /opt/adgvs/station/check-station.sh` then shows an **Intake** section (gpiozero, GPIO
devices, group, the validated settings; it moves nothing).

### Bring-up, in three steps

`intake-run.sh` stops the service and runs the intake by hand in the SSH terminal as the
service user with the station settings. Use SAMPLE documents only.

1. **Manual mode** (no feeder, no gate: checks camera, screening, upload, decisions).

   ```bash
   sudo /opt/adgvs/station/intake-run.sh --hardware manual --once
   ```

   - [ ] `camera-test` already reports `Verdict OK` (camera bring-up above).
   - [ ] Station registered; `ADGVS_SERVER` (Render URL) and `ADGVS_STATION_KEY` set.
   - [ ] For each prompt, place one sheet face up in the compartment and press Enter. The
         terminal prints `>>> Sheet N: put it in TRAY 1 (PASSED SCREENING) <<<` (or TRAY 2).
         Put it there, press Enter. Type `done` to end the batch.
   - [ ] The registrar's intake queue shows the batch: one document per sheet, with the
         tray and reasons; the batch summary has 0 unrouted.
   - [ ] Pull the network cable mid-sheet: the terminal says the sheet is held and nothing
         is routed (the dashboard cannot be told until the network is back); plug it back
         within 2 minutes (`ADGVS_INTAKE_HOLD_TIMEOUT`) and the sheet continues.
   - [ ] The journal / terminal shows per-step timings and no names or other field values.
2. **Gate only** (operator feeds, the servo routes):

   ```bash
   sudo /opt/adgvs/station/intake-run.sh --check --feeder manual --gate gpio
   sudo /opt/adgvs/station/intake-run.sh --feeder manual --gate gpio --once
   ```

   - [ ] The compartment sensor confirms each sheet (else: "does not see the sheet").
   - [ ] The servo rests at the home angle (sheet held), turns to the Tray 1 / Tray 2 angle,
         the sheet lands in the right tray, the servo returns home. Adjust
         `ADGVS_GATE_*_ANGLE` / pulse widths and repeat until every sheet lands cleanly.
   - [ ] Block the path once: the station reports a gate jam (dashboard Jam, document goes
         to manual review) and stops.
3. **Feeder + gate** (the production cycle):

   ```bash
   sudo /opt/adgvs/station/intake-run.sh --hardware gpio --once   # 3-5 SAMPLE sheets
   sudo systemctl start adgvs-intake && journalctl -u adgvs-intake -f
   ```

   - [ ] One sheet per feed, the motor stops when the sheet sensor trips (tune
         `ADGVS_FEEDER_OVERRUN` / `_FEED_TIMEOUT`); an empty stack ends the batch.
   - [ ] A misfeed with paper still on the stack is a feeder jam (needs the stack sensor).
   - [ ] After a jam: clear it, then `sudo systemctl restart adgvs-intake`.

## Status display (TFT LCD): placeholder

To be added when the display is chosen: model, wiring (SPI/HDMI/DSI), driver overlay in
`/boot/firmware/config.txt`, and a small client showing Ready / Scanning / Jam / Offline.
`pi-setup.sh` has a marked `TODO(display)` step for it.

## Operator hardware notes (to be completed with the scanner)

- **Loading the feeder:** TBD (paper guides, max stack height, face up/down, top edge first).
- **Clearing a jam / double feed:** the dashboard says which sheet to re-feed. Open the
  feeder (TBD for the model), remove the sheets, close it, and put the stack back starting
  from that sheet. The pages before it are kept; the stack continues.
- **Cleaning the rollers:** TBD (interval in sheets, cleaning sheet or cloth, per the
  scanner manual). Dirty rollers cause skew and double feeds.
- **Restarting:** `sudo systemctl restart adgvs-station` (or power-cycle the Pi). A stack
  that was stopped mid-way is then checked as it is; re-scan the missing pages.

The operator's screen guide is in [manuals/operator-guide.md](../manuals/operator-guide.md).
Software reference: [scan-agent/README.md](../../scan-agent/README.md).
