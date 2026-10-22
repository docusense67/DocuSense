#!/bin/bash
# User-launched desktop restart; requires the Pi desktop's normal user session.
set -euo pipefail
station_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf 'Restarting ADGVS. The camera and mechanism will stop briefly.\n'
if ! systemctl --user restart adgvs-local.service; then
  printf '\nADGVS could not restart. Run this shortcut from the Pi desktop.\nPress Enter to close.\n'
  read -r || true
  exit 1
fi
printf 'Waiting for ADGVS, then opening the app...\n'
exec bash "$station_dir/open-local.sh" --window
