#!/usr/bin/env bash
# Quick health check of the ADGVS scan station (Raspberry Pi). Run it after pi-setup.sh,
# after changing the scanner, or when the dashboard shows the station Offline:
#
#   sudo /opt/adgvs/station/check-station.sh            # all checks (OCR timing ~30-60 s)
#   sudo /opt/adgvs/station/check-station.sh --quick    # skip the OCR timing run
#
# On a camera station (pi-setup.sh --camera) it also checks Picamera2 and the camera and,
# without --quick, takes one test capture (camera-test) and prints its quality. On an intake
# station (pi-setup.sh --intake) it checks gpiozero/lgpio, GPIO access and the feeder/gate
# settings (intake --check); it never moves the feeder or the gate.
#
# Exit code 0 when every check passed. Secrets (the station key) are never printed.
set -uo pipefail

PREFIX=${ADGVS_HOME:-/opt/adgvs}
VENV="$PREFIX/venv"
PY="$VENV/bin/python"
ENV_FILE=${ADGVS_ENV_FILE:-/etc/adgvs/station.env}
SERVICE_USER=${ADGVS_SERVICE_USER:-adgvs-agent}
UNIT=adgvs-station.service
INTAKE=0
if systemctl is-enabled --quiet adgvs-intake.service 2>/dev/null; then
    UNIT=adgvs-intake.service
    INTAKE=1
fi
QUICK=0
[ "${1:-}" = --quick ] && QUICK=1
CAMERA=0
grep -qs '^include-system-site-packages *= *true' "$VENV/pyvenv.cfg" && CAMERA=1
[ "$INTAKE" = 1 ] && CAMERA=1

FAILED=0
pass() { printf '  [ OK ] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAILED=$((FAILED + 1)); }
info() { printf '  [info] %s\n' "$*"; }
section() { printf '\n%s\n' "$*"; }

# Settings from the station env file (needs root: the file is mode 600).
if [ -r "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$ENV_FILE"
    set +a
else
    echo "Cannot read $ENV_FILE: run with sudo." >&2
fi
SERVER=${ADGVS_SERVER:-}
CA=${ADGVS_CA_CERT:-}
SCANS=${ADGVS_SCAN_FOLDER:-/var/lib/adgvs/scans}
export TESSERACT_CMD=${TESSERACT_CMD:-/usr/bin/tesseract}

as_service_user() {
    if [ "$(id -u)" -eq 0 ] && id "$SERVICE_USER" >/dev/null 2>&1; then
        runuser -u "$SERVICE_USER" -- "$@"
    else
        "$@"
    fi
}

section "System"
# shellcheck source=/dev/null
. /etc/os-release
info "$PRETTY_NAME, $(uname -m), $(hostname)"
if command -v vcgencmd >/dev/null 2>&1; then
    # 0x0 = never throttled; anything else means under-voltage or heat (slower OCR).
    info "$(vcgencmd measure_temp), throttled=$(vcgencmd get_throttled | cut -d= -f2)"
fi
if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; then
    pass "clock synchronised (NTP), time zone $(timedatectl show -p Timezone --value)"
else
    fail "clock not synchronised: check network access to NTP (timedatectl timesync-status)"
fi

section "Software"
if [ -x "$PY" ] && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    pass "Python $("$PY" -c 'import platform; print(platform.python_version())') ($VENV)"
else
    fail "agent venv missing or Python < 3.12: $PY (re-run pi-setup.sh)"
fi
if version=$("$TESSERACT_CMD" --version 2>&1 | head -n1) && [ -n "$version" ]; then
    pass "$version ($TESSERACT_CMD)"
    if "$TESSERACT_CMD" --list-langs 2>/dev/null | grep -qx eng; then
        pass "Tesseract English data"
    else
        fail "tesseract-ocr-eng missing"
    fi
else
    fail "Tesseract not found at $TESSERACT_CMD (apt install tesseract-ocr)"
fi
if "$PY" -c 'from pyzbar import pyzbar' 2>/dev/null; then
    pass "zbar (pyzbar) loads"
else
    fail "zbar does not load (apt install libzbar0t64)"
fi
if "$PY" -c 'import sane; sane.init()' 2>/dev/null; then
    pass "python-sane loads"
else
    fail "python-sane does not load (re-run pi-setup.sh)"
fi

section "Scanner"
devices=$(as_service_user scanimage -L 2>&1)
if printf '%s\n' "$devices" | grep -q "^device \`"; then
    pass "SANE sees the scanner as $SERVICE_USER:"
    printf '%s\n' "$devices" | grep "^device \`" | sed 's/^/         /'
elif [ "$CAMERA" = 1 ]; then
    info "no SANE scanner (camera station: the camera is checked below)"
else
    fail "no scanner found by SANE as $SERVICE_USER: check USB/power, the SANE backend for"
    printf '         this model (sane-find-scanner), and the scanner group. Output: %s\n' \
        "$(printf '%s' "$devices" | head -n2 | tr '\n' ' ')"
fi
if [ -d "$SCANS" ]; then
    count=$(find "$SCANS" -maxdepth 1 -type f | wc -l)
    pass "spool folder $SCANS ($count file(s) waiting)"
else
    fail "spool folder $SCANS missing (re-run pi-setup.sh)"
fi

if [ "$CAMERA" = 1 ]; then
    section "Camera"
    if as_service_user "$PY" -c 'from picamera2 import Picamera2' 2>/dev/null; then
        pass "Picamera2 loads in the agent venv"
    else
        fail "Picamera2 does not load (re-run pi-setup.sh --camera)"
    fi
    cameras=$(as_service_user "$PY" -c 'from picamera2 import Picamera2
for c in Picamera2.global_camera_info(): print(c.get("Num"), c.get("Model"))' 2>/dev/null)
    if [ -n "$cameras" ]; then
        pass "camera(s) seen by libcamera as $SERVICE_USER:"
        printf '%s\n' "$cameras" | sed 's/^/         /'
    else
        fail "no camera found: check the ribbon cable (Pi 5: 22-pin, contacts the right way)"
        printf '         and `rpicam-hello --list-cameras`; the service user needs group video\n'
    fi
    if [ "$QUICK" -eq 0 ] && [ -n "$cameras" ]; then
        out=$(mktemp -d)
        chown "$SERVICE_USER" "$out" 2>/dev/null || true
        # One capture as the station would take it (needs a page in the compartment for a
        # meaningful verdict; an empty compartment reports page_not_found).
        as_service_user "$PY" -m scan_agent camera-test --out "$out" --timeout 5 --no-frame \
            2>/dev/null | sed 's/^/         /'
        code=${PIPESTATUS[0]}
        if [ "$code" -le 1 ]; then
            pass "camera-test captured a frame (exit $code; 1 = quality issues above)"
        else
            fail "camera-test could not use the camera (exit $code)"
        fi
        rm -rf "$out"
    fi
fi

if [ "$INTAKE" = 1 ]; then
    section "Intake feeder + gate (GPIO; nothing is moved)"
    if as_service_user "$PY" -c 'import gpiozero, lgpio' 2>/dev/null; then
        pass "gpiozero + lgpio load in the agent venv"
    else
        fail "gpiozero/lgpio do not load (re-run pi-setup.sh --intake)"
    fi
    if ls /dev/gpiochip* >/dev/null 2>&1; then
        pass "GPIO character devices: $(ls /dev/gpiochip* | tr '\n' ' ')"
    else
        fail "no /dev/gpiochip*: not a Raspberry Pi, or the GPIO driver is missing"
    fi
    if id -nG "$SERVICE_USER" 2>/dev/null | grep -qw gpio; then
        pass "$SERVICE_USER is in the gpio group"
    else
        fail "$SERVICE_USER is not in the gpio group (re-run pi-setup.sh --intake)"
    fi
    # Pins, angles and timeouts from $ENV_FILE (validated as the service does at start).
    check=$(as_service_user "$PY" -m scan_agent intake --check \
        --hardware "${ADGVS_INTAKE_HARDWARE:-gpio}" 2>&1)
    code=$?
    printf '%s\n' "$check" | grep -v 'STATION_KEY' | sed 's/^/         /'
    if [ "$code" -eq 0 ]; then
        pass "feeder/gate settings valid (wiring table: docs/hardware/README.md)"
    else
        fail "feeder/gate settings invalid (above): fix ADGVS_FEEDER_* / ADGVS_GATE_* in $ENV_FILE"
    fi
fi

section "Server"
if [ -z "$SERVER" ] || [ "$SERVER" = https://adgvs.example.edu.ph ]; then
    fail "ADGVS_SERVER not set in $ENV_FILE"
else
    curl_args=(-fsS --max-time 15 -o /dev/null -w '%{http_code} in %{time_total}s')
    [ -n "$CA" ] && curl_args+=(--cacert "$CA")
    # A free-tier API may sleep when idle: the first call can take up to a minute.
    if result=$(curl "${curl_args[@]}" "$SERVER/api/health" 2>&1) \
            || result=$(curl "${curl_args[@]}" --max-time 90 "$SERVER/api/health" 2>&1); then
        pass "$SERVER/api/health -> HTTP $result"
    else
        fail "$SERVER/api/health unreachable: $result"
    fi
fi
if [ -n "${ADGVS_STATION_KEY:-}" ]; then
    pass "station key is set (not shown)"
else
    fail "ADGVS_STATION_KEY empty in $ENV_FILE (Accounts & stations -> Scan stations)"
fi

section "Service"
if systemctl is-enabled --quiet "$UNIT"; then pass "$UNIT enabled"; else fail "$UNIT not enabled"; fi
if systemctl is-active --quiet "$UNIT"; then
    pass "$UNIT running since $(systemctl show -p ActiveEnterTimestamp --value "$UNIT")"
else
    fail "$UNIT not running: journalctl -u ${UNIT%.service} -n 50"
fi
journalctl -u "$UNIT" -n 3 --no-pager -o cat 2>/dev/null | sed 's/^/         /'

if [ "$QUICK" -eq 0 ]; then
    section "OCR speed (bundled TOR/COE fixtures, spec: < 15 s per document)"
    if [ -f "$PREFIX/station/ocr_timing.py" ]; then
        if as_service_user env TESSERACT_CMD="$TESSERACT_CMD" \
                "$PY" "$PREFIX/station/ocr_timing.py" --root "$PREFIX" | sed 's/^/  /'; then
            pass "OCR timing within budget"
        else
            fail "OCR too slow or misread (see above; check throttling and cooling)"
        fi
    else
        fail "$PREFIX/station/ocr_timing.py missing (re-run pi-setup.sh)"
    fi
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "All checks passed."
else
    echo "$FAILED check(s) failed."
fi
exit $((FAILED > 0))
