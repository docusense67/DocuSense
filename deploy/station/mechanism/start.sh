#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export GPIOZERO_PIN_FACTORY=lgpio
export PYTHONPATH="$(cd ../../.. && pwd)/scan-agent${PYTHONPATH:+:$PYTHONPATH}"
station_python="${ADGVS_CAMERA_PYTHON:-/opt/adgvs/venv/bin/python}"
if [[ ! -x "$station_python" ]]; then station_python=/usr/bin/python3; fi
exec "$station_python" -u server.py "$@"
