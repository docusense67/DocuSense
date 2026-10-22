#!/usr/bin/env bash
# Run the intake station by hand on the Pi, as the service would (same user, settings and
# GPIO working directory), for bring-up in an SSH terminal. Stops adgvs-intake first: the
# service and this run must not share the camera and GPIO pins.
#
#   sudo /opt/adgvs/station/intake-run.sh --check                              # settings only
#   sudo /opt/adgvs/station/intake-run.sh --hardware manual --once             # step 1
#   sudo /opt/adgvs/station/intake-run.sh --feeder manual --gate gpio --once   # step 2
#   sudo /opt/adgvs/station/intake-run.sh --hardware gpio --once               # step 3
#
# Arguments go to `python -m scan_agent intake` (docs/hardware/README.md). Secrets from
# /etc/adgvs/station.env are passed in the environment, never printed.
set -euo pipefail

PREFIX=${ADGVS_HOME:-/opt/adgvs}
ENV_FILE=${ADGVS_ENV_FILE:-/etc/adgvs/station.env}
SERVICE_USER=${ADGVS_SERVICE_USER:-adgvs-agent}
STATE=/var/lib/adgvs

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0 $*" >&2; exit 1; }
if systemctl is-active --quiet adgvs-intake.service; then
    echo "Stopping adgvs-intake for this run (start it again: sudo systemctl start adgvs-intake)"
    systemctl stop adgvs-intake.service
fi
set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a
export LG_WD="$STATE" TESSERACT_CMD=${TESSERACT_CMD:-/usr/bin/tesseract} PYTHONUNBUFFERED=1
cd "$STATE"
exec runuser -u "$SERVICE_USER" -- "$PREFIX/venv/bin/python" -m scan_agent intake "$@"
