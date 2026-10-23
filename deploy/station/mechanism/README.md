# Local mechanism controls

For the **ADGVS login app → paper feed → camera → OCR → VALID/INVALID tray** workflow,
use [CAMERA-APP.md](../CAMERA-APP.md) and `../run-local.sh`.

The page on port 8765 is a standalone manual diagnostics panel. Its manual cycle does
not capture or validate a document. The authenticated ADGVS page on port 5173 uses the
bridge's separate keyed integration endpoints for the full workflow.

## Run on the Pi

Close the old `machine_gui.py`, `motor_buttons.py`, and standalone servo tests first.
They must not control the same GPIOs at the same time.

```bash
bash /home/adgvs/adgvs-src/deploy/station/mechanism/start.sh
```

Open **http://localhost:8765** in the Pi browser. Click **Connect Hardware**, then
**Run One Cycle**. The server starts disconnected and does not move anything on startup.
GPIO connection failures appear in the page, rather than silently switching to simulation.

The launcher uses the installed station Python (or `/usr/bin/python3` fallback) and the native `lgpio` driver. Raspberry Pi system
packages `python3-gpiozero` and `python3-lgpio` must be installed; they are already present
on this Pi. The user running the server needs access to `/dev/gpiochip*` (normally via the
`gpio` group). Do not run a second copy or use `sudo` to work around a busy GPIO.

**Ctrl+C** in the server Terminal stops movement and closes the service. **Stop All**
stops the motor immediately and sends the second servo's neutral command before releasing
its signal. Closing/leaving the control page sends Stop All; if the browser loses its
connection, a four-second heartbeat timeout stops outputs. Keep the page open while moving.

For remote access, keep the service on loopback and use SSH forwarding:

```bash
ssh -L 8765:127.0.0.1:8765 adgvs@adgvs-station.local
```

Then open `http://localhost:8765` on that computer. The server does not bind to the LAN.

## Sequence and controls

One click performs one cycle:

1. First SG90: **90 → 180 → 90**, 0.5 seconds at each requested position.
2. DC motor: **backward for 5 seconds at 100% PWM**.
3. Second SG90: **8-second maximum command**, then neutral and signal release.
4. Third SG90: **UP → DOWN → UP**, with editable angles and hold time.
5. Two-second pause, then ready for the next click.

By default, the up/down movement follows the second servo. The page can move it immediately
after the DC motor instead. Defaults are **UP 180°, DOWN 90°, DOWN hold 1 second**. Calibrate
the angles with the horn detached from the mechanism. Position labels use the same 1–2 ms
pulse range as the existing tests and are not measured angles.

**Continuous Motor** toggles the DC motor alone. **Test Motor** and **Test Servo 2** each
run one device for five seconds. **Test UP/DOWN** command only the third servo. New movement
commands are blocked while another test/cycle is active; Stop All remains available.

The second servo uses the user's last selected maximum command: `1.0`, meaning a **2 ms
pulse at 50 Hz / 10% duty**, not constant HIGH. Neutral is 1.5 ms. Removing a positional
servo's mechanical stopper alone does not guarantee continuous rotation; verify its real
movement and stop behaviour. Disconnect servo power if it stalls or keeps spinning after
the stop command.

## Wiring

| Connection | BCM GPIO | Physical header pin |
| --- | --- | --- |
| L298N IN1 | 17 | 11 |
| L298N IN2 | 27 | 13 |
| L298N ENA, jumper removed | 18 | 12 |
| First servo signal | 12 | 32 |
| Second servo signal | 13 | 33 |
| Third servo / up-down signal | 19 | 35 |

Servo red wires use an external regulated **5 V** supply sized for the combined load.
Grounds of the Pi, servo supply, L298N, and motor supply must be common. The motor uses
L298N OUT1/OUT2 and its separate supply; do not connect servo power to 12 V or motor outputs.
GPIO13 is **physical pin 33**; it is different from physical pin 13.

## Simulation and diagnostics

```bash
# Explicitly simulated browser controls: no GPIO is claimed.
bash /home/adgvs/adgvs-src/deploy/station/mechanism/start.sh --simulate

# A different loopback port, if 8765 is occupied.
bash /home/adgvs/adgvs-src/deploy/station/mechanism/start.sh --port 8766

# Hardware-free checks; no network listener or real GPIO is needed.
cd /home/adgvs/adgvs-src/deploy/station/mechanism
/usr/bin/python3 -m unittest discover -s tests -v
```

Logs are in `server.log` beside `server.py`; runtime logs and the GPIO lock are ignored by
Git. `GET /api/health` reports server availability and mode. Real motion is only requested
through same-origin JSON POST commands with a per-process token. Status and command logs
describe requested outputs; they do not measure voltages, angles, paper presence, or torque.

If startup reports `Operation not permitted` when binding a socket, run `start.sh` in the
Pi's own Terminal. A restricted agent/container session may not expose GPIO or permit a
localhost listener. Nothing in this tool changes device permissions or desktop settings.

## Motor and servo controls inside the login app

Open `http://localhost:5173/scan` as an authorized validation operator and select
this Pi's camera station. **Connect controller** claims the configured outputs
without starting a movement. The page provides these individual commands:

- **Test motor:** existing backward direction at 100% PWM for five seconds, then stop.
- **Test feeder:** servo 1 requests 90°, 180°, 90° at 0.5-second intervals, then detaches.
- **Test eject servo:** neutral, maximum command for five seconds, neutral, then detach.
- **Test sorting servo:** the entered 0–180° command for 0.7 seconds, then detach.
- **Stop mechanism:** interrupts movement; **Disconnect** stops and releases GPIO ownership.

Keep the paper path clear for manual tests. A test does not capture or validate a
document. **Feed one sheet & validate** still performs the complete automatic feed,
camera, OCR, verification and sorting sequence with fixed VALID → UP 180° / INVALID → DOWN 90° positions.

Tests cannot overlap or start while the scan workflow is active or paper is held.
Manual movement, like scanning, sends browser heartbeats. Losing those heartbeats
stops the mechanism after four seconds. Completed tests release their motion lease,
so normal completion is not reported as a lost browser connection. Stop and leaving
the page also cancel an in-flight start request when its response arrives.

GPIO system libraries are loaded independently of camera preview, so connecting the
motor does not rely on a camera request having imported them first. The bridge starts
disconnected and never actuates on startup. Restart the local app after an upgrade:

```bash
systemctl --user restart adgvs-local.service
```

These controls do not restore the removed GPIO status checks or hardware alert bell.
Command failures appear inline. A successful command is not proof of physical movement.

## Cloud operators (relay)

When the web app runs in the cloud (Vercel + Render), the API cannot reach this bridge.
`relay.py` connects out to the Render API with the station key, long-polls operator commands,
runs them through the bridge's keyed integration endpoints and reports results, state and
(only while someone watches) preview frames. The bridge itself is unchanged and still listens
on loopback only.

```bash
bash /home/adgvs/adgvs-src/deploy/station/run-cloud-mechanism.sh   # bridge + relay
```

Settings go in `deploy/station/cloud.env`; the user unit is
`deploy/station/adgvs-cloud-mechanism.service`. The relay moves nothing unless remote control
is enabled on this Pi (`ADGVS_MECHANISM_REMOTE=1` / `--allow-remote`), never runs an expired
command, runs stop first, and forwards heartbeats only while the operator's own heartbeat is
fresh, so the four-second dead-man still stops the outputs if the operator or the network goes
away. Design, safety rules, Render settings and remaining risks:
[docs/cloud-motor-control.md](../../../docs/cloud-motor-control.md). `tests/test_relay.py`
checks the relay with a fake bridge and API.
