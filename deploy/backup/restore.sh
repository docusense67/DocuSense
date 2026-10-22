#!/bin/bash
# DISASTER RECOVERY: replace the live database and stored files with a backup.
# Everything written after that backup is lost. Steps (docs/admin-installation-guide.md):
#   docker compose stop api jobs web
#   docker compose exec backup restore.sh <backup folder name> --yes
#   docker compose run --rm migrate && docker compose start api jobs web
set -euo pipefail

name="${1:?usage: restore.sh <backup folder name> --yes}"
dir="/backups/$name"
if [ "${2:-}" != "--yes" ]; then
    echo "This overwrites the live database and files with $name. Re-run with --yes." >&2
    exit 2
fi
pass=$(mktemp)
trap 'rm -f "$pass"' EXIT
printf '%s' "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is not set}" > "$pass"
export PGPASSWORD="$POSTGRES_PASSWORD" GNUPGHOME="$(mktemp -d)"

decrypt() {
    gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$pass" --decrypt "$1"
}

(cd "$dir" && sha256sum -c --quiet SHA256SUMS)
decrypt "$dir/db.dump.gpg" | pg_restore -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --clean --if-exists --single-transaction --exit-on-error

find /data/storage -mindepth 1 -delete
decrypt "$dir/storage.tar.gz.gpg" | tar -C /data -xz
chown -R 10001:10001 /data/storage  # the API's user (backend/Dockerfile)
echo "Restored $name. Now run: docker compose run --rm migrate"
