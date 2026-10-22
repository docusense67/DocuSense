#!/bin/bash
# Encrypted backup of the Supabase database and, when the secret key is given, the files in the
# private Storage bucket. Same layout and encryption as deploy/backup/backup.sh:
#   <output dir>/<YYYYmmdd-HHMMSS>/db.dump.gpg, storage.tar.gz.gpg, SHA256SUMS
# Run nightly by .github/workflows/backup.yml; restore check: deploy/cloud/verify-backup.sh.
#
# Environment:
#   SUPABASE_DB_OWNER_URL  owner login through the session pooler (port 5432)
#   BACKUP_PASSPHRASE      AES-256 passphrase. Keep a copy outside GitHub: without it the
#                          backups cannot be restored.
#   SUPABASE_URL, SUPABASE_SERVICE_KEY  optional: also back up the Storage files
#   STORAGE_BUCKET         default adgvs-private
#   PG_MAJOR               server major version the tools must match (default 17)
# Needs pg_dump and psql of that major version on PATH, gpg, python3 and sha256sum.
#
#   bash deploy/cloud/backup.sh backups
set -euo pipefail
umask 077

out="${1:?usage: backup.sh OUTPUT_DIR}"
url="${SUPABASE_DB_OWNER_URL:?SUPABASE_DB_OWNER_URL is not set}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is not set}"
pg_major="${PG_MAJOR:-17}"
bucket="${STORAGE_BUCKET:-adgvs-private}"
here=$(cd "$(dirname "$0")" && pwd)

stamp=$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$out"
work="$out/.$stamp.partial"
pass=$(mktemp)
files=$(mktemp -d)
GNUPGHOME=$(mktemp -d)
export GNUPGHOME
trap 'rm -rf "$pass" "$files" "$GNUPGHOME" "$work"' EXIT
printf '%s' "$BACKUP_PASSPHRASE" > "$pass"

encrypt() {
    gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$pass" \
        --symmetric --cipher-algo AES256 --output -
}

# pg_dump refuses newer servers and an older server would hide a planned upgrade: insist on
# the agreed major version so a Supabase upgrade shows up here first.
client=$(pg_dump --version | awk '{print $3}' | cut -d. -f1)
server=$(psql -X -At -v ON_ERROR_STOP=1 -d "$url" -c 'SHOW server_version_num')
server=$((server / 10000))
if [ "$client" != "$pg_major" ] || [ "$server" != "$pg_major" ]; then
    echo "Version mismatch: pg_dump $client, server $server, expected $pg_major (PG_MAJOR)." >&2
    exit 1
fi

mkdir "$work"
# Only the application's schema: Supabase's own schemas (auth, storage, ...) belong to the
# platform and cannot be restored into a plain PostgreSQL server.
pg_dump --dbname="$url" --format=custom --schema=public | encrypt > "$work/db.dump.gpg"

if [ -n "${SUPABASE_SERVICE_KEY:-}" ]; then
    : "${SUPABASE_URL:?SUPABASE_URL is not set}"
    psql -X -At -v ON_ERROR_STOP=1 -v bucket="$bucket" -d "$url" > "$files/names" <<'SQL'
SELECT name FROM storage.objects WHERE bucket_id = :'bucket' ORDER BY name;
SQL
    python3 "$here/fetch_storage.py" "$bucket" "$files/storage" < "$files/names"
    mkdir -p "$files/storage"
    tar -C "$files" -cz storage | encrypt > "$work/storage.tar.gz.gpg"
else
    echo "SUPABASE_SERVICE_KEY not set: Storage files are NOT in this backup." >&2
fi

(cd "$work" && sha256sum ./*.gpg > SHA256SUMS)
mv "$work" "$out/$stamp"
echo "$(date -Iseconds) backup $out/$stamp done ($(du -sh "$out/$stamp" | cut -f1))"
