#!/bin/bash
# Local ADGVS login app + API + Pi camera/mechanism bridge.
set -euo pipefail
station_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$station_dir/../.." && pwd)"
state_dir="$HOME/.local/state/adgvs"
mkdir -p "$state_dir"
exec 9>"$state_dir/local-app.lock"
if ! flock -n 9; then
  echo 'ADGVS is already running. Open http://localhost:5173/scan' >&2
  exit 1
fi
if [[ -f "$station_dir/local.env" ]]; then
  set -a
  source "$station_dir/local.env"
  set +a
fi
if [[ "${1:-}" != '' && "${1:-}" != '--demo' ]]; then
  echo "Usage: $0 [--demo]" >&2
  exit 1
fi
backend_python="$repo_dir/backend/.venv/bin/python"
if [[ ! -x "$backend_python" || ! -d "$repo_dir/frontend/node_modules" ]] || ! command -v node >/dev/null; then
  echo "Missing dependencies. Run: bash $station_dir/setup-local.sh" >&2
  exit 1
fi
# The browser/API/bridge all run on the same Pi. No cloud API can reach Pi loopback.
export ADGVS_CAMERA_API_URL=http://127.0.0.1:8000
export ADGVS_MECHANISM_URL=http://127.0.0.1:8765
export ADGVS_COOKIE_SECURE=false
export ADGVS_CORS_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
export ADGVS_MECHANISM_KEY="${ADGVS_MECHANISM_KEY:-$(/usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(48))')}"
export ADGVS_CAMERA_PYTHON="${ADGVS_CAMERA_PYTHON:-/opt/adgvs/venv/bin/python}"
if [[ ! -x "$ADGVS_CAMERA_PYTHON" ]]; then export ADGVS_CAMERA_PYTHON="$repo_dir/scan-agent/.venv/bin/python"; fi
if [[ ! -x "$ADGVS_CAMERA_PYTHON" ]]; then echo 'Camera Python environment is missing.' >&2; exit 1; fi
if [[ "${1:-}" == '--demo' ]]; then
  echo 'DEMO: fictional database/accounts; results compare against demo records only.'
  # Local document generation must print the requested record, never a fixed sample.
  export ADGVS_DEMO_FIXTURE_PDFS=false
  export ADGVS_DATABASE_URL="sqlite:///$repo_dir/backend/dev.db"
  export ADGVS_STORAGE_DIR="$repo_dir/backend/storage-dev"
  export ADGVS_CAMERA_STATION_KEY=dev-station-key
  # Prepare without mechanism config: the actual station ID comes from the seeded DB.
  (cd "$repo_dir/backend"; env ADGVS_MECHANISM_URL= "$backend_python" -c 'from scripts.dev_server import prepare; prepare()')
  export ADGVS_MECHANISM_STATION_ID
  ADGVS_MECHANISM_STATION_ID="$(cd "$repo_dir/backend"; env ADGVS_MECHANISM_URL= "$backend_python" -c 'from app.db import SessionLocal; from app.models import ScanStation; from sqlalchemy import select; db=SessionLocal(); print(db.scalar(select(ScanStation.id).where(ScanStation.name == "Dev Station"))); db.close()')"
else
  : "${ADGVS_DATABASE_URL:?Set ADGVS_DATABASE_URL in deploy/station/local.env}"
  : "${ADGVS_SECRET_KEY:?Set ADGVS_SECRET_KEY in deploy/station/local.env}"
  : "${ADGVS_CAMERA_STATION_KEY:?Set the registered camera station API key in local.env}"
  : "${ADGVS_MECHANISM_STATION_ID:?Set the corresponding station ID in local.env}"
fi
processes=()
cleanup() {
  trap - EXIT INT TERM
  for process_id in "${processes[@]}"; do kill -TERM "$process_id" 2>/dev/null || true; done
  for process_id in "${processes[@]}"; do wait "$process_id" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM
if [[ "${1:-}" == '--demo' ]]; then
  (cd "$repo_dir/backend"; exec "$backend_python" scripts/dev_server.py) &
else
  (cd "$repo_dir/backend"; exec "$backend_python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
fi
processes+=("$!")
bash "$station_dir/mechanism/start.sh" &
processes+=("$!")
(cd "$repo_dir/frontend"; exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort) &
processes+=("$!")
echo 'Open http://localhost:5173 → sign in as a validation operator → Scan station.'
echo 'Ctrl+C stops the app and mechanism. Close older GPIO controllers before feeding.'
wait -n "${processes[@]}"
