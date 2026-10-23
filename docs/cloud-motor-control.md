# Cloud motor control (camera station relay)

Operators on the cloud site (Vercel web app, Render API, Supabase database) can drive the
Raspberry Pi camera station: feed one sheet and validate, stop, connect / disconnect the
controller, the motor and servo tests, the tray angle and the live camera preview. This page
describes how that works, the safety rules, how to run it on the Pi and the settings on
Render.

On-prem behaviour does not change. `ADGVS_MECHANISM_MODE` defaults to `loopback`: the whole
app runs on the Pi (`deploy/station/run-local.sh`) and the API calls the bridge on
`127.0.0.1:8765` directly, as before.

## Design

Render cannot reach the Pi (it sits behind the school's NAT). Nothing listens on the Pi for
the internet. Instead, the Pi connects out:

```
browser ──HTTPS──> Vercel /api ──> Render API ──> Supabase (mechanism_commands,
                                       ^                      mechanism_station_states)
                                       │ HTTPS, station key (outbound from the Pi only)
                         Pi: relay.py ─┘
                              │ http://127.0.0.1:8765, bridge key (loopback only)
                         Pi: server.py (GPIO bridge, unchanged) ──> motor, servos, camera
```

- **Operator routes** (unchanged contract, validator-only, audited):
  `GET /api/mechanism/status`, `GET /api/mechanism/camera/frame`, `POST /api/mechanism/command`.
  In relay mode a command is checked exactly as before (open single-document session of the
  operator for `scan`, fixed VALID 180° / INVALID 90° angles, active station, tray angle
  0–180), audited (`mechanism.requested`) and then stored in `mechanism_commands`. The route
  waits up to about 7 seconds for the relay's result and answers like the loopback bridge did:
  the new status, or the station's refusal as a 409, or a 503 when it was not confirmed.
  Status and preview come from what the relay last reported.
- **Station routes** (station key `X-Station-Key`, only the station configured as
  `ADGVS_MECHANISM_STATION_ID`; any other station gets 403 and never sees a command):
  - `GET /api/station/mechanism/commands?wait=20` long-polls this station's commands.
    The answer also carries `operator_fresh_for` (seconds) and `viewer_active`.
  - `POST /api/station/mechanism/commands/{id}/result` reports `{ok, error, status}`.
  - `POST /api/station/mechanism/state` reports the bridge status and the local opt-in.
  - `POST /api/station/mechanism/frame` sends one JPEG preview frame (raw body, at most
    200 KB, JPEG magic checked).
  A command carries only an action name and the backend's fixed parameters (session id,
  angles). The station never receives student data.
- **VALID / INVALID** are still decided by the backend: the camera workflow in the bridge
  uploads the captured page through the normal station routes, and the tray follows the
  backend's result. The relay only forwards actions.
- **Database.** `mechanism_commands` (one row per command; pruned after a day) and
  `mechanism_station_states` (one row per station: last report, local opt-in, controller,
  operator heartbeat time, viewer time). Migration `b6f2d8e4c1a7` locks both down on Supabase
  like the other tables (RLS on, Data API roles revoked).
- **Connection pool.** Render runs one API worker with a small pool (3 + 2). Long-polls and
  the command route's wait never hold a database connection while waiting: each check is a
  short transaction, then the request waits on an in-process signal (woken as soon as a
  command or result arrives) with a 1-second fallback re-check. Waits are capped (25 s poll,
  about 7 s for a result).
- **Preview frames** are kept in the API's memory only (the newest frame per station), not in
  the database: about one frame per second while someone watches would churn the free
  Supabase tier. This assumes one API worker (Render's setting); with several workers the
  preview may show "waiting" now and then. The relay sends frames only while a viewer polled
  the preview in the last 5 seconds, at most one per second.

## Safety rules

These move real motors. The rules are pure, unit-tested functions:
`backend/app/services/mechanism_relay.py` (`backend/tests/test_mechanism_relay.py`) and
`deploy/station/mechanism/relay.py` (`deploy/station/mechanism/tests/test_relay.py`).

1. **Command expiry.** Every movement command expires 5 seconds after it was requested. The
   API never delivers an expired command, and tells the relay how many seconds are left and
   how long it held the long poll (`held_for`). The relay counts the seconds left from when it
   sent its poll plus `held_for`, on its own clock and never from later than the answer
   arrived, so a slow network or a clock difference can only shorten a command's life (a
   command that arrived late in a long poll still gets its full life). An expired command is never run, and
   the operator gets "did not confirm in time".
2. **Stop first.** `stop` and `disconnect` never expire and are delivered and run first. A
   movement command queued before a newer stop is dropped ("superseded"), on the API and
   again on the relay. Any validation operator may send stop or disconnect, even while
   another operator controls the station or the relay is offline (it then runs as soon as the
   relay reconnects).
3. **Dead-man.** The bridge stops its outputs when it gets no heartbeat for 4 seconds
   (unchanged `server.py` behaviour). In relay mode the browser's heartbeat (every second
   while the mechanism moves) only updates `heartbeat_at` in the database. The relay forwards
   a heartbeat to the bridge only while the API says the controlling operator's heartbeat is
   younger than 3 seconds. It never invents one: if the operator closes the page, loses the
   network, or the Pi loses its connection to Render, the heartbeats stop and the bridge stops
   by itself within about 4 to 7 seconds. Leaving the page also sends a stop, as before.
4. **One controller.** While a scan session is open on the camera station, only its operator
   may move the station, send heartbeats or acknowledge a held sheet. Without an open session,
   the operator who last sent a command keeps control for 30 seconds after their last command
   or heartbeat; others get "Another operator is controlling this camera station".
5. **Local opt-in.** The relay moves nothing unless it was started with `--allow-remote`
   (`ADGVS_MECHANISM_REMOTE=1` in `cloud.env`). Without it, it reports state and runs only
   stop and disconnect; the API also refuses movement up front ("Remote control is switched
   off on the station").
6. **Bridge checks stay.** The bridge still refuses overlapping movement, movement while a
   sheet is held, and out-of-range angles; the relay reports its refusal to the operator.
7. **Audit.** Every command except heartbeats is audited before it is queued
   (`mechanism.requested`, with the action, session id and angle only; no personal data).
   An audit entry records a request, not proof that anything moved.

## Running it on the Pi

Prerequisites: the camera station is registered (admin, Stations) and you have its API key;
the GPIO bridge works locally (`deploy/station/mechanism/README.md`); `setup-local.sh` was run
(or the station Python at `/opt/adgvs/venv` exists). Stop the local app first; both use the
GPIO pins and port 8765.

1. Create `~/adgvs-src/deploy/station/cloud.env` (`chmod 600`; it is ignored by Git):

   ```bash
   ADGVS_SERVER=https://adgvs-api.onrender.com     # Render URL, not the Vercel site
   ADGVS_STATION_KEY='API key of the registered camera station'
   ADGVS_MECHANISM_REMOTE=1                        # allow cloud operators to move it
   # ADGVS_MECHANISM_KEY='32+ random characters'   # optional; else random per start
   ```

   The launcher points the bridge's camera workflow at the same API and key
   (`ADGVS_CAMERA_API_URL`, `ADGVS_CAMERA_STATION_KEY`), so the station shows as online and
   scans are uploaded as this station. Like the scan station, the relay talks to Render
   directly: page uploads are too large for the Vercel proxy.

2. Try it in a terminal: `bash ~/adgvs-src/deploy/station/run-cloud-mechanism.sh`.
   It starts the bridge (`mechanism/start.sh`) and the relay; Ctrl+C stops both, and the
   relay sends a final stop to the bridge.

3. Run it as a systemd user unit, next to `adgvs-local.service`:

   ```bash
   cp ~/adgvs-src/deploy/station/adgvs-cloud-mechanism.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user disable --now adgvs-local.service      # if installed
   systemctl --user enable --now adgvs-cloud-mechanism.service
   journalctl --user -u adgvs-cloud-mechanism -f
   ```

   The unit conflicts with `adgvs-local.service`. To go back to the on-Pi app, disable this
   unit and enable `adgvs-local.service` again.

The relay logs each command it runs or refuses, and warns when the API or the bridge is
unreachable. It never logs keys.

## Render settings

In the Render Dashboard, adgvs-api, Environment:

| Variable | Value |
| --- | --- |
| `ADGVS_MECHANISM_MODE` | `relay` |
| `ADGVS_MECHANISM_STATION_ID` | the camera station's id (admin, Stations) |

Leave `ADGVS_MECHANISM_URL` and `ADGVS_MECHANISM_KEY` unset on Render. Apply the migration
(`b6f2d8e4c1a7`) with the usual deploy workflow before switching the mode. Nothing changes
on Vercel: the browser keeps calling `/api/mechanism/*` through the existing rewrite.

On-prem Docker and the on-Pi app keep `ADGVS_MECHANISM_MODE` unset (`loopback`).

## Remaining risks

- **The operator may not be at the station.** Remote control means someone can start the
  motor, servos or a feed while nobody stands next to the mechanism: a hand in the paper path,
  a jam nobody sees, paper pulled in crooked. The camera preview helps but does not show the
  whole mechanism. Mitigations: the local opt-in (switch it off when the station is
  unattended: remove `ADGVS_MECHANISM_REMOTE=1` and restart the unit), the short command
  life, the dead-man, the fixed timings of every test, and the stop button. The school should
  decide who may use remote control and when; a person near the station is still recommended.
- **No physical feedback.** As on the Pi, a successful command means the bridge accepted it,
  not that anything moved: there is no paper sensor. The bridge's reported state can be up to
  a few seconds old in the browser.
- **Stopping depends on the network.** A remote stop reaches the Pi only through Render.
  If the Pi or Render is offline, the dead-man still stops movement after a few seconds, but a
  stop pressed in that moment arrives late or not at all. Keep the physical power switch of
  the motor supply within reach.
- **A delivered command may still run after the operator saw "not confirmed".** The result
  wait (about 7 s) is longer than a command's life (5 s), so this needs a command delivered
  just before it expired and a result that took more than 2 more seconds to come back.
- **Station key.** Whoever has the camera station's key can receive its commands and report
  fake state or frames (not move anything elsewhere). Keep `cloud.env` at mode 600; deactivate
  the station in the admin screen if the key leaks.
- **Several API workers.** Wake-ups are in-process; with more than one worker, commands and
  results are still found by the 1-second re-check, and the preview may be choppy (see
  Design).
