#!/bin/bash
# Restore test that leaves production untouched: restores a backup into a scratch database,
# compares row counts with the live database, checks the file archive, then drops the copy.
#   docker compose exec backup verify-backup.sh [backup folder name]   (default: newest)
set -euo pipefail

name="${1:-$(ls -1 /backups | grep '^20' | sort | tail -n 1)}"
dir="/backups/${name:?no backups found}"
scratch="adgvs_restore_check"
pass=$(mktemp)
trap 'rm -f "$pass"; dropdb -h db -U "$POSTGRES_USER" --if-exists "$scratch"' EXIT
printf '%s' "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is not set}" > "$pass"
export PGPASSWORD="$POSTGRES_PASSWORD" GNUPGHOME="$(mktemp -d)"

decrypt() {
    gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$pass" --decrypt "$1"
}

echo "Checking $dir"
(cd "$dir" && sha256sum -c --quiet SHA256SUMS)

dropdb -h db -U "$POSTGRES_USER" --if-exists "$scratch"
createdb -h db -U "$POSTGRES_USER" "$scratch"
decrypt "$dir/db.dump.gpg" | pg_restore -h db -U "$POSTGRES_USER" -d "$scratch" \
    --no-owner --no-acl --exit-on-error

count() {
    psql -h db -U "$POSTGRES_USER" -d "$1" -tAc "SELECT count(*) FROM $2"
}
status=0
printf '%-22s %10s %10s\n' table backup live
for table in users students grades document_requests generated_documents \
             scanned_documents validation_records audit_logs; do
    restored=$(count "$scratch" "$table")
    live=$(count "$POSTGRES_DB" "$table")
    printf '%-22s %10s %10s\n' "$table" "$restored" "$live"
    # Live data only grows between the backup and now (audit log is append-only).
    if [ "$restored" -gt "$live" ]; then status=1; fi
done

files=$(decrypt "$dir/storage.tar.gz.gpg" | tar -tz | grep -vc '/$' || true)
echo "files in archive: $files"

if [ "$status" -eq 0 ]; then
    echo "RESTORE CHECK PASSED: $name"
else
    echo "RESTORE CHECK FAILED: $name has rows the live database does not" >&2
fi
exit "$status"
