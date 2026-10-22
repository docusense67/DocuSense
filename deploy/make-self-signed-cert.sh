#!/bin/bash
# Self-signed certificate for a campus-only server (no public DNS name), or for testing.
# Prefer a certificate from the school's IT or a public CA when one is available.
#   deploy/make-self-signed-cert.sh adgvs.school.local [192.168.1.20]
# Then install deploy/certs/fullchain.pem as trusted on staff PCs and the scan station
# (scan agent: --ca-cert deploy/certs/fullchain.pem).
set -euo pipefail
name="${1:?usage: make-self-signed-cert.sh <server name> [server IP]}"
san="DNS:$name"
if [ -n "${2:-}" ]; then san="$san,IP:$2"; fi
dir="$(dirname "$0")/certs"
mkdir -p "$dir"
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
    -subj "/CN=$name" -addext "subjectAltName=$san" \
    -keyout "$dir/privkey.pem" -out "$dir/fullchain.pem"
chmod 600 "$dir/privkey.pem"
echo "Wrote $dir/fullchain.pem and privkey.pem for $san (valid 825 days)."
