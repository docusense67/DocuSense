#!/bin/bash
# Open the local station after its user service is ready.
set -euo pipefail
station_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
state_dir="$HOME/.local/state/adgvs"
mkdir -p "$state_dir"
exec >>"$state_dir/browser-startup.log" 2>&1
printf '\n%s Opening local ADGVS station\n' "$(date --iso-8601=seconds)"
systemctl --user daemon-reload
if ! systemctl --user start adgvs-local.service; then
  command -v notify-send >/dev/null && notify-send 'ADGVS startup failed' 'Check: journalctl --user -u adgvs-local.service' || true
  exit 1
fi
# Browser launch follows both API and frontend readiness, including after a cold boot.
if ! /usr/bin/python3 - <<'PY'
import time
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    try:
        for url in ('http://127.0.0.1:8000/api/health', 'http://127.0.0.1:5173/scan'):
            with opener.open(url, timeout=2) as response:
                if response.status != 200:
                    raise RuntimeError('Not ready')
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit('Local app did not become ready within 120 seconds. Check the service journal.')
PY
then
  command -v notify-send >/dev/null && notify-send 'ADGVS is not ready' 'Check: journalctl --user -u adgvs-local.service' || true
  exit 1
fi
browser_mode=--start-maximized  # was --kiosk (fullscreen); pass --kiosk to restore
if [[ "${1:-}" == '--kiosk' ]]; then browser_mode=--kiosk; fi
if [[ "${1:-}" == '--window' ]]; then browser_mode=--start-maximized; fi
mkdir -p "$HOME/.local/share/adgvs-station/browser"
exec /usr/bin/chromium "$browser_mode" --no-first-run --no-default-browser-check \
  --user-data-dir="$HOME/.local/share/adgvs-station/browser" \
  http://localhost:5173/scan
