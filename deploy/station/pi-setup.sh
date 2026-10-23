#!/usr/bin/env bash
# Provision a fresh Raspberry Pi 5 (Raspberry Pi OS 64-bit, Debian 13 Trixie) as the ADGVS
# scan station. Idempotent: safe to run again to update the agent or repair the setup.
#
# Where the code comes from (pick one):
#   sudo ./pi-setup.sh                         # the checkout this script sits in
#   sudo ./pi-setup.sh --from-dir ~/adgvs-src  # a checkout copied onto the Pi (scp/tar/rsync)
#   sudo ./pi-setup.sh --git https://github.com/docusense67/DocuSense.git --ref main
#     (a private repository needs a URL with a read-only token or a deploy key)
#
# Camera station (Raspberry Pi Camera Module 3 / V2 instead of, or next to, a scanner):
#   sudo ./pi-setup.sh --camera ...      # adds Picamera2 (apt) and camera access; stays on
#   sudo ./pi-setup.sh --no-camera ...   # back to the scanner-only setup
#   Picamera2 is not on PyPI for the Pi: it comes from apt (python3-picamera2), so the venv
#   is created with --system-site-packages; pinned pip packages still win inside the venv.
#
# Intake station (feeder + camera compartment + two-tray gate, scan_agent/intake.py):
#   sudo ./pi-setup.sh --intake ...      # implies --camera; adds gpiozero + lgpio (apt), the
#                                        # gpio group, and runs adgvs-intake.service INSTEAD
#                                        # of adgvs-station.service; stays on
#   sudo ./pi-setup.sh --no-intake ...   # back to the SANE validation station service
#
# It installs: OS packages (Tesseract, zbar, SANE), the service user `adgvs-agent` (in the
# `scanner` group), /opt/adgvs (agent + venv), /var/lib/adgvs/scans (spool, 750),
# /etc/adgvs/station.env (600, created from a template only if missing: never overwritten),
# the adgvs-station systemd service, unattended security upgrades, ufw (SSH only),
# time zone Asia/Manila with NTP. See docs/hardware/README.md.
set -euo pipefail

PREFIX=/opt/adgvs
VENV="$PREFIX/venv"
STATE=/var/lib/adgvs
SCANS="$STATE/scans"
ETC=/etc/adgvs
ENV_FILE="$ETC/station.env"
SERVICE_USER=adgvs-agent
UNIT=adgvs-station.service
INTAKE_UNIT=adgvs-intake.service
TIMEZONE=Asia/Manila
PYTHON_SANE_VERSION=2.9.2   # sdist only: built here against libsane-dev

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC=""
GIT_URL=""
GIT_REF=main
CAMERA=""   # 1 = --camera, 0 = --no-camera, empty = keep what the venv already has
CAMERA_DROPIN=/etc/systemd/system/adgvs-station.service.d/camera.conf
INTAKE=""   # 1 = --intake, 0 = --no-intake, empty = keep the service that is enabled now

usage() {
    sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --from-dir) SRC=${2:?--from-dir needs a path}; shift 2 ;;
        --git) GIT_URL=${2:?--git needs a URL}; shift 2 ;;
        --ref) GIT_REF=${2:?--ref needs a branch or tag}; shift 2 ;;
        --camera) CAMERA=1; shift ;;
        --no-camera) CAMERA=0; shift ;;
        --intake) INTAKE=1; shift ;;
        --no-intake) INTAKE=0; shift ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown option: $1" >&2; usage 2 ;;
    esac
done

log() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0 [options]" >&2; exit 1; }

# ---- 0. checks -------------------------------------------------------------------------
# shellcheck source=/dev/null
. /etc/os-release
[ "${VERSION_CODENAME:-}" = trixie ] \
    || warn "expected Debian 13 Trixie (Python 3.13); this is ${PRETTY_NAME:-unknown}"
[ "$(uname -m)" = aarch64 ] || warn "expected a 64-bit OS (aarch64); this is $(uname -m)"
# Intake mode, once enabled, stays on (the intake unit is the one enabled).
if [ -z "$INTAKE" ]; then
    INTAKE=0
    systemctl is-enabled --quiet "$INTAKE_UNIT" 2>/dev/null && INTAKE=1
fi
if [ "$INTAKE" = 1 ]; then
    [ "$CAMERA" = 0 ] && warn "--no-camera ignored: the intake station needs the camera"
    CAMERA=1
fi
# Camera support, once enabled, stays on: the venv then sees the system site-packages.
if [ -z "$CAMERA" ]; then
    CAMERA=0
    grep -qs '^include-system-site-packages *= *true' "$VENV/pyvenv.cfg" && CAMERA=1
fi
[ "$CAMERA" = 1 ] && log "Camera support: on (Picamera2 from apt, venv with system site-packages)"
[ "$INTAKE" = 1 ] && log "Intake station: on ($INTAKE_UNIT, gpiozero + lgpio from apt)"

# ---- 1. OS packages --------------------------------------------------------------------
log "Installing OS packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
# Trixie renamed libzbar0 (64-bit time_t transition); fall back for older releases.
ZBAR=libzbar0t64
apt-cache show "$ZBAR" >/dev/null 2>&1 || ZBAR=libzbar0
apt-get install -y -q --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng "$ZBAR" \
    sane-utils libsane1 libsane-dev acl \
    python3 python3-venv python3-dev build-essential \
    git rsync curl ca-certificates \
    unattended-upgrades apt-listchanges ufw systemd-timesyncd
if [ "$CAMERA" = 1 ]; then
    # Picamera2 + libcamera come from the Raspberry Pi archive (Raspberry Pi OS only).
    # rpicam-apps-lite gives `rpicam-hello --list-cameras` / `rpicam-still` for bring-up.
    apt-cache show python3-picamera2 >/dev/null 2>&1 \
        || { echo "python3-picamera2 not available: use Raspberry Pi OS (archive.raspberrypi.com)" >&2; exit 1; }
    CAMERA_PKGS="python3-picamera2"
    apt-cache show rpicam-apps-lite >/dev/null 2>&1 && CAMERA_PKGS="$CAMERA_PKGS rpicam-apps-lite"
    # shellcheck disable=SC2086
    apt-get install -y -q --no-install-recommends $CAMERA_PKGS
fi
if [ "$INTAKE" = 1 ]; then
    # gpiozero + its Pi 5 pin driver (lgpio): apt only, like Picamera2 (never in constraints).
    apt-get install -y -q --no-install-recommends python3-gpiozero python3-lgpio
fi

# ---- 2. code ---------------------------------------------------------------------------
if [ -n "$GIT_URL" ]; then
    log "Fetching $GIT_URL ($GIT_REF)"
    CHECKOUT="$PREFIX/src"
    if [ -d "$CHECKOUT/.git" ]; then
        git -C "$CHECKOUT" remote set-url origin "$GIT_URL"
        git -C "$CHECKOUT" fetch --depth 1 origin "$GIT_REF"
        git -C "$CHECKOUT" checkout -q --force FETCH_HEAD
    else
        rm -rf "$CHECKOUT"
        git clone -q --depth 1 --branch "$GIT_REF" "$GIT_URL" "$CHECKOUT"
    fi
    SRC="$CHECKOUT"
elif [ -z "$SRC" ]; then
    SRC=$(cd "$SCRIPT_DIR/../.." && pwd)
fi
SRC=$(cd "$SRC" && pwd)
for needed in scan-agent/pyproject.toml backend/app/documents/defaults/__init__.py \
              deploy/station/adgvs-station.service deploy/station/adgvs-intake.service; do
    [ -f "$SRC/$needed" ] || { echo "Not an ADGVS checkout: $SRC/$needed missing" >&2; exit 1; }
done

log "Installing the agent from $SRC into $PREFIX"
install -d -m 755 "$PREFIX" "$PREFIX/station" "$PREFIX/backend/app/documents/defaults"
rsync -a --delete \
    --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
    --exclude '.ruff_cache/' --exclude '*.egg-info/' --exclude 'build/' \
    "$SRC/scan-agent/" "$PREFIX/scan-agent/"
# The default template zones: used by check-station.sh's OCR timing run.
install -m 644 "$SRC/backend/app/documents/defaults/__init__.py" \
    "$PREFIX/backend/app/documents/defaults/__init__.py"
install -m 755 "$SRC/deploy/station/check-station.sh" "$PREFIX/station/check-station.sh"
install -m 755 "$SRC/deploy/station/intake-run.sh" "$PREFIX/station/intake-run.sh"
install -m 644 "$SRC/deploy/station/ocr_timing.py" "$PREFIX/station/ocr_timing.py"
chown -R root:root "$PREFIX"

# ---- 3. service user and folders -------------------------------------------------------
log "Service user $SERVICE_USER and folders"
getent group scanner >/dev/null || groupadd --system scanner
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$STATE" --no-create-home \
        --shell /usr/sbin/nologin --comment "ADGVS scan station agent" "$SERVICE_USER"
fi
usermod -aG scanner "$SERVICE_USER"
# The human login user (the one running sudo) may run scanimage too.
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    usermod -aG scanner "$SUDO_USER"
fi
if [ "$CAMERA" = 1 ]; then
    # libcamera opens /dev/video*, /dev/media* and /dev/dma_heap/*: group `video`.
    usermod -aG video "$SERVICE_USER"
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
        usermod -aG video "$SUDO_USER"
    fi
fi
if [ "$INTAKE" = 1 ]; then
    # /dev/gpiochip* belongs to group gpio on Raspberry Pi OS (udev rule).
    getent group gpio >/dev/null || groupadd --system gpio
    usermod -aG gpio "$SERVICE_USER"
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
        usermod -aG gpio "$SUDO_USER"
    fi
fi
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE" "$SCANS"
# 755: a CA certificate kept here (ADGVS_CA_CERT) must be readable by the service user;
# the env file itself is root-only (systemd reads it before dropping privileges).
install -d -m 755 -o root -g root "$ETC"

if [ ! -e "$ENV_FILE" ]; then
    log "Creating $ENV_FILE (template: fill in the server and key)"
    umask 077
    cat >"$ENV_FILE" <<'EOF'
# ADGVS scan station settings, read by adgvs-station.service. Root-only (chmod 600).
# After editing: sudo systemctl restart adgvs-station

# The ADGVS address operators open in the browser; the agent calls <server>/api/station/...
ADGVS_SERVER=https://adgvs.example.edu.ph
# The station's API key from Accounts & stations -> Scan stations (shown once).
ADGVS_STATION_KEY=
# Optional: PEM file of a self-signed or school CA certificate (e.g. /etc/adgvs/ca.pem,
# mode 644). Empty = public CAs.
ADGVS_CA_CERT=
# Spool folder for scanned pages; files are deleted once uploaded.
ADGVS_SCAN_FOLDER=/var/lib/adgvs/scans
TESSERACT_CMD=/usr/bin/tesseract
# Optional: SANE device from `scanimage -L` (default: the first sheet-fed scanner).
ADGVS_SANE_DEVICE=
# Optional: space-separated scanner options, e.g. "df-action=Stop" (see scanimage -A).
ADGVS_SANE_OPTIONS=
# Camera station (pi-setup.sh --camera); check with: python -m scan_agent camera-test
# ADGVS_CAMERA_SOURCE=picamera2
# ADGVS_CAMERA_ROTATE=90           # clockwise turn that makes the page upright (0/90/180/270)
# ADGVS_CAMERA_FOCUS=auto          # or a fixed lens position in dioptres (1/m), e.g. 3.3
# ADGVS_CAMERA_EXPOSURE=lock       # auto | lock | exposure time in microseconds
# ADGVS_CAMERA_AWB=lock            # auto | lock | "red,blue" colour gains
# Image-quality thresholds (defaults in scan_agent/config.py), e.g.:
# ADGVS_QUALITY_BLUR_MIN=40
# ADGVS_QUALITY_DPI_MIN=200
# Intake station (pi-setup.sh --intake, adgvs-intake.service); check with:
#   /opt/adgvs/venv/bin/python -m scan_agent intake --check
# Pins are BCM numbers (17 = GPIO17) or BOARD<n> (physical pin). Wiring: docs/hardware/README.md
# ADGVS_INTAKE_HARDWARE=gpio        # gpio | manual (terminal, bring-up) | fake
# ADGVS_INTAKE_HOLD_TIMEOUT=120     # server unreachable: seconds a sheet is held, then stop
# ADGVS_INTAKE_RETRY_INTERVAL=10
# ADGVS_INTAKE_CLEAR_TIMEOUT=30     # after routing: seconds for the compartment to be clear
# ADGVS_FEEDER_MOTOR_PIN=           # required: feed motor (relay/MOSFET) or H-bridge forward
# ADGVS_FEEDER_MOTOR_BACKWARD_PIN=  # H-bridge only (then also _ENABLE_PIN, _SPEED 0..1)
# ADGVS_FEEDER_MOTOR_ACTIVE_HIGH=1  # 0 for active-low relay boards
# ADGVS_FEEDER_SHEET_PIN=           # required: sheet-in-compartment sensor
# ADGVS_FEEDER_STACK_PIN=           # recommended: paper-on-feeder sensor (empty vs. misfeed)
# ADGVS_FEEDER_SENSOR_PULL_UP=1     # 1: the sensor pulls the pin LOW when it sees paper
# ADGVS_FEEDER_FEED_TIMEOUT=5
# ADGVS_FEEDER_OVERRUN=0.15
# ADGVS_GATE_SERVO_PIN=             # required: routing servo signal
# ADGVS_GATE_TRAY1_ANGLE=-45        # passed screening
# ADGVS_GATE_TRAY2_ANGLE=45         # needs manual review
# ADGVS_GATE_HOME_ANGLE=0           # holds the sheet in the compartment
# ADGVS_GATE_MIN_PULSE_MS=1.0
# ADGVS_GATE_MAX_PULSE_MS=2.0
# ADGVS_GATE_MOVE_TIME=0.6
# ADGVS_GATE_EJECT_PIN=             # optional motor/solenoid that pushes the sheet out
# ADGVS_GATE_EXIT_PIN=              # optional sensor on the way to the trays
# ADGVS_GATE_CLEAR_TIMEOUT=5
EOF
    umask 022
else
    log "Keeping existing $ENV_FILE"
fi
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ---- 4. Python environment -------------------------------------------------------------
log "Python venv $VENV"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
    || { echo "Python 3.12+ required (Trixie ships 3.13); found $(python3 -V)" >&2; exit 1; }
if [ "$CAMERA" = 1 ]; then
    # Also converts an existing venv (rewrites pyvenv.cfg, keeps the installed packages).
    python3 -m venv --system-site-packages "$VENV"
elif [ ! -x "$VENV/bin/python" ] \
        || grep -qs '^include-system-site-packages *= *true' "$VENV/pyvenv.cfg"; then
    python3 -m venv "$VENV"  # new, or back from camera mode (--no-camera)
fi
"$VENV/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
# Pinned dependencies (constraints.txt) + python-sane built from source.
"$VENV/bin/python" -m pip install -q --disable-pip-version-check \
    -c "$PREFIX/scan-agent/constraints.txt" \
    "$PREFIX/scan-agent[sane,pdf]" "python-sane==$PYTHON_SANE_VERSION"
# Same version number on every update: force the agent itself to be reinstalled.
"$VENV/bin/python" -m pip install -q --disable-pip-version-check \
    --no-deps --force-reinstall "$PREFIX/scan-agent"
rm -rf "$PREFIX/scan-agent/build" "$PREFIX"/scan-agent/*.egg-info
"$VENV/bin/python" -c 'import sane, pyzbar.pyzbar, cv2, pytesseract, scan_agent.station' \
    || { echo "Agent import check failed" >&2; exit 1; }
if [ "$CAMERA" = 1 ]; then
    # Picamera2 (system) must load with the venv's pinned numpy/pillow.
    "$VENV/bin/python" -c 'from picamera2 import Picamera2; import scan_agent.capture.camera' \
        || { echo "Picamera2 import check failed in $VENV" >&2; exit 1; }
fi
if [ "$INTAKE" = 1 ]; then
    "$VENV/bin/python" -c 'import gpiozero, lgpio; import scan_agent.intake' \
        || { echo "gpiozero/lgpio import check failed in $VENV" >&2; exit 1; }
fi

# ---- 5. system settings ----------------------------------------------------------------
log "Time zone $TIMEZONE and NTP"
timedatectl set-timezone "$TIMEZONE"
timedatectl set-ntp true

log "Unattended security upgrades"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
systemctl enable --now unattended-upgrades.service >/dev/null

log "Firewall: SSH in, nothing else"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw limit 22/tcp comment 'SSH' >/dev/null
ufw --force enable >/dev/null

# Key-only SSH, but only once a key is installed (never lock the operator out).
HOME_DIR=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)
if [ -s "$HOME_DIR/.ssh/authorized_keys" ]; then
    log "SSH: keys only, no root login"
    cat >/etc/ssh/sshd_config.d/50-adgvs.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
    sshd -t && systemctl reload ssh
else
    warn "no ~/.ssh/authorized_keys for ${SUDO_USER:-root}: SSH password login left as is"
fi

# ---- 6. display (placeholder) ----------------------------------------------------------
# TODO(display): the small TFT LCD that shows the station status. Model and driver are not
# chosen yet; add its overlay (/boot/firmware/config.txt) and status client here.

# ---- 7. service ------------------------------------------------------------------------
log "Service $UNIT"
install -m 644 "$SRC/deploy/station/$UNIT" "/etc/systemd/system/$UNIT"
if [ "$CAMERA" = 1 ]; then
    # The unit only allows USB devices (DevicePolicy=closed); add the camera's nodes.
    install -d -m 755 "$(dirname "$CAMERA_DROPIN")"
    cat >"$CAMERA_DROPIN" <<'EOF'
# Installed by pi-setup.sh --camera: libcamera (Picamera2) device access.
[Service]
SupplementaryGroups=video
DeviceAllow=char-video4linux rw
DeviceAllow=char-media rw
DeviceAllow=char-dma_heap rw
EOF
    chmod 644 "$CAMERA_DROPIN"
else
    rm -f "$CAMERA_DROPIN"
fi
install -m 644 "$SRC/deploy/station/$INTAKE_UNIT" "/etc/systemd/system/$INTAKE_UNIT"
# Applicant intake OCR worker (docs/applicant-intake.md): installed, enabled by hand.
install -m 644 "$SRC/deploy/station/adgvs-applicant-worker.service" \
    /etc/systemd/system/adgvs-applicant-worker.service
systemctl daemon-reload
# One of the two services runs: the SANE validation station or the intake station.
if [ "$INTAKE" = 1 ]; then
    ACTIVE=$INTAKE_UNIT
    systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
else
    ACTIVE=$UNIT
    systemctl disable --now "$INTAKE_UNIT" >/dev/null 2>&1 || true
fi
systemctl enable "$ACTIVE" >/dev/null
READY=1
grep -Eq '^ADGVS_STATION_KEY=.+' "$ENV_FILE" \
    && ! grep -q '^ADGVS_SERVER=https://adgvs.example.edu.ph' "$ENV_FILE" || READY=0
if [ "$READY" = 1 ] && [ "$INTAKE" = 1 ]; then
    # Pins and angles must be valid before anything moves (prints problems, never secrets).
    if ! ( set -a; . "$ENV_FILE"; set +a
           "$VENV/bin/python" -m scan_agent intake --check \
               --hardware "${ADGVS_INTAKE_HARDWARE:-gpio}" >/dev/null ); then
        READY=0
        echo "Intake settings incomplete (above): set ADGVS_FEEDER_* / ADGVS_GATE_* in $ENV_FILE"
    fi
fi
if [ "$READY" = 1 ]; then
    systemctl restart "$ACTIVE"
    echo "Started $ACTIVE"
else
    echo "Not started yet: fill ADGVS_SERVER and ADGVS_STATION_KEY (and, for the intake"
    echo "station, the ADGVS_FEEDER_* / ADGVS_GATE_* pins) in $ENV_FILE, then"
    echo "  sudo systemctl restart ${ACTIVE%.service}"
fi

log "Done. Check the station with: sudo $PREFIX/station/check-station.sh"
if [ "$CAMERA" = 1 ]; then
    echo "Camera: sudo -u $SERVICE_USER $VENV/bin/python -m scan_agent camera-test --out /tmp/camtest"
fi
if [ "$INTAKE" = 1 ]; then
    echo "Intake bring-up: sudo $PREFIX/station/intake-run.sh --hardware manual --once"
    echo "  (then --feeder manual --gate gpio, then --hardware gpio; docs/hardware/README.md)"
fi
if [ -n "${SUDO_USER:-}" ]; then
    echo "($SUDO_USER was added to the scanner group: log out and in again to use scanimage.)"
fi
