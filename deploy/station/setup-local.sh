#!/bin/bash
# Install application dependencies in this checkout; no GPIO movement.
set -euo pipefail
station_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$station_dir/../.." && pwd)"
if ! command -v npm >/dev/null || ! command -v node >/dev/null; then
  echo 'Install Node.js matching frontend/package-lock.json (20.19+ or 22.12+), then rerun this script.' >&2
  exit 1
fi
/usr/bin/python3 -m venv --system-site-packages "$repo_dir/backend/.venv"
"$repo_dir/backend/.venv/bin/python" -m pip install -c "$repo_dir/backend/constraints.txt" "$repo_dir/backend"
if [[ ! -x /opt/adgvs/venv/bin/python ]]; then
  /usr/bin/python3 -m venv --system-site-packages "$repo_dir/scan-agent/.venv"
  "$repo_dir/scan-agent/.venv/bin/python" -m pip install -c "$repo_dir/scan-agent/constraints.txt" "$repo_dir/scan-agent"
fi
cd "$repo_dir/frontend"
npm ci
echo 'Dependencies installed. See deploy/station/CAMERA-APP.md for configuration and launch.'
