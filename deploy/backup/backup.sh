#!/bin/bash
# Encrypted backup of the database and stored files (Section 18 Backup).
# Writes /backups/<YYYYmmdd-HHMMSS>/ with db.dump.gpg, storage.tar.gz.gpg and SHA256SUMS,
# then deletes backups older than BACKUP_RETENTION_DAYS (default 30).
set -euo pipefail
umask 077

stamp=$(date +%Y%m%d-%H%M%S)
work="/backups/.$stamp.partial"
pass=$(mktemp)
trap 'rm -rf "$pass" "$work"' EXIT
printf '%s' "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is not set}" > "$pass"
export PGPASSWORD="$POSTGRES_PASSWORD" GNUPGHOME="$(mktemp -d)"

encrypt() {
    gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$pass" \
        --symmetric --cipher-algo AES256 --output -
}

mkdir "$work"
pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom | encrypt > "$work/db.dump.gpg"
tar -C /data -cz storage | encrypt > "$work/storage.tar.gz.gpg"
(cd "$work" && sha256sum ./*.gpg > SHA256SUMS)
mv "$work" "/backups/$stamp"

find /backups -mindepth 1 -maxdepth 1 -type d -name '20*' \
    -mtime +"${BACKUP_RETENTION_DAYS:-30}" -exec rm -rf {} +
echo "$(date -Iseconds) backup /backups/$stamp done ($(du -sh "/backups/$stamp" | cut -f1))"
