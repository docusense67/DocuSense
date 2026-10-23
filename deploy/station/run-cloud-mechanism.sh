#!/bin/bash
# Pi camera mechanism for the CLOUD setup (docs/cloud-motor-control.md): the loopback GPIO
# bridge (mechanism/server.py, 127.0.0.1:8765 only) plus the outbound relay
# (mechanism/relay.py) that long-polls the Render API for operator commands. Render never
# connects to the Pi. Use run-local.sh instead when the whole app runs on the Pi; the two
# share a lock and cannot run together.
#
# Settings: deploy/station/cloud.env (chmod 600, not in Git), shell assignments:
#   ADGVS_SERVER=https://adgvs-api.onrender.com   # the Render URL, not the Vercel site
#   ADGVS_STATION_KEY='API key of the registered camera station'
#   ADGVS_MECHANISM_REMOTE=1      # local opt-in: let cloud operators move the mechanism
#   # Optional: a stable 32+ character bridge secret (else a random one per start).
#   # ADGVS_MECHANISM_KEY='...'
set -euo pipefail
station_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
state_dir="$HOME/.local/state/adgvs"
mkdir -p "$state_dir"
exec 9>"$state_dir/local-app.lock"
if ! flock -n 9; then
  echo 'The local ADGVS app or another mechanism launcher is already running.' >&2
  exit 1
fi
env_file="${ADGVS_CLOUD_ENV:-$station_dir/cloud.env}"
if [[ -f "$env_file" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$env_file"
  set +a
fi
: "${ADGVS_SERVER:?Set ADGVS_SERVER (the Render API URL) in deploy/station/cloud.env}"
: "${ADGVS_STATION_KEY:?Set ADGVS_STATION_KEY (the camera station key) in deploy/station/cloud.env}"
# The bridge's camera workflow uploads scans and reports presence to the same API, as this station.
export ADGVS_CAMERA_API_URL="$ADGVS_SERVER"
export ADGVS_CAMERA_STATION_KEY="$ADGVS_STATION_KEY"
export ADGVS_MECHANISM_KEY="${ADGVS_MECHANISM_KEY:-$(/usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(48))')}"
python="${ADGVS_CAMERA_PYTHON:-/opt/adgvs/venv/bin/python}"
if [[ ! -x "$python" ]]; then python=/usr/bin/python3; fi
remote=()
if [[ "${ADGVS_MECHANISM_REMOTE:-0}" == 1 ]]; then remote=(--allow-remote); fi

processes=()
cleanup() {
  trap - EXIT INT TERM
  for process_id in "${processes[@]}"; do kill -TERM "$process_id" 2>/dev/null || true; done
  for process_id in "${processes[@]}"; do wait "$process_id" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM
bash "$station_dir/mechanism/start.sh" &
processes+=("$!")
"$python" -u "$station_dir/mechanism/relay.py" --server "$ADGVS_SERVER" "${remote[@]}" &
processes+=("$!")
echo "Mechanism bridge on 127.0.0.1:8765; relay -> $ADGVS_SERVER (remote control: ${remote[*]:-off})"
wait -n "${processes[@]}"
