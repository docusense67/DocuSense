#!/bin/bash
# Restore check for a backup made by deploy/cloud/backup.sh, into a throwaway database (same
# checks as deploy/backup/verify-backup.sh): checksums, full restore, row counts compared with
# the live database, file count of the Storage archive. Never touches the live data.
#
#   bash deploy/cloud/verify-backup.sh BACKUP_FOLDER
#
# Environment:
#   BACKUP_PASSPHRASE      the passphrase the backup was made with
#   RESTORE_URL            an EMPTY scratch database on a server of the same major version, as a
#                          superuser (its public schema is replaced), e.g. the postgres:17
#                          service container in .github/workflows/backup.yml
#   SUPABASE_DB_OWNER_URL  optional: live database, for the row count comparison
# Needs pg_restore and psql of the backup's major version on PATH, gpg and sha256sum.
set -euo pipefail
umask 077

dir="${1:?usage: verify-backup.sh BACKUP_FOLDER}"
scratch="${RESTORE_URL:?RESTORE_URL is not set}"
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE is not set}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
pass="$work/passphrase"
export GNUPGHOME="$work/gnupg"
mkdir -m 700 "$GNUPGHOME"
printf '%s' "$BACKUP_PASSPHRASE" > "$pass"

decrypt() {
    gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$pass" --decrypt "$1"
}

echo "Checking $dir"
(cd "$dir" && sha256sum -c --quiet SHA256SUMS)

# The dump holds the whole public schema (CREATE SCHEMA public included), so start empty.
PGOPTIONS='-c client_min_messages=warning' \
    psql -X -q -v ON_ERROR_STOP=1 -d "$scratch" -c 'DROP SCHEMA IF EXISTS public CASCADE'
# On Supabase the row-level-security policies name the API login (setup-db-roles), and
# pg_restore --no-acl still restores policies: the role must exist in the scratch server.
app_role="${ADGVS_APP_DB_USER:-adgvs_app}"
[[ "$app_role" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "Bad ADGVS_APP_DB_USER: $app_role" >&2; exit 1; }
psql -X -q -v ON_ERROR_STOP=1 -d "$scratch" -c "DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$app_role') THEN
        CREATE ROLE $app_role NOLOGIN;
    END IF;
END \$\$;"
decrypt "$dir/db.dump.gpg" > "$work/db.dump"
pg_restore --dbname="$scratch" --no-owner --no-acl --exit-on-error "$work/db.dump"
echo "schema version: $(psql -X -At -d "$scratch" -c 'SELECT version_num FROM alembic_version')"

tables=(users students grades document_requests generated_documents scanned_documents
        validation_records audit_logs)
query=""
for table in "${tables[@]}"; do
    query+="${query:+ UNION ALL }SELECT '$table', count(*) FROM $table"
done
counts() {  # prints "table count" lines
    psql -X -At -F ' ' -v ON_ERROR_STOP=1 -d "$1" -c "$query" | tr -d '\r'
}

declare -A restored live
counts "$scratch" > "$work/restored"
while read -r table n; do restored[$table]=$n; done < "$work/restored"
if [ -n "${SUPABASE_DB_OWNER_URL:-}" ]; then
    counts "$SUPABASE_DB_OWNER_URL" > "$work/live"
    while read -r table n; do live[$table]=$n; done < "$work/live"
fi

status=0
printf '%-22s %10s %10s\n' table backup live
for table in "${tables[@]}"; do
    backup="${restored[$table]:-}"
    now="${live[$table]:-}"
    printf '%-22s %10s %10s\n' "$table" "${backup:-?}" "${now:--}"
    if [[ ! "$backup" =~ ^[0-9]+$ ]]; then
        status=1
    # Live data only grows between the backup and now (audit log is append-only).
    elif [[ "$now" =~ ^[0-9]+$ ]] && [ "$backup" -gt "$now" ]; then
        status=1
    fi
done

if [ -f "$dir/storage.tar.gz.gpg" ]; then
    files=$(decrypt "$dir/storage.tar.gz.gpg" | tar -tz | grep -vc '/$' || true)
    echo "files in archive: $files"
else
    echo "files in archive: none (backup made without SUPABASE_SERVICE_KEY)"
fi

name=$(basename "$dir")
if [ "$status" -eq 0 ]; then
    echo "RESTORE CHECK PASSED: $name"
else
    echo "RESTORE CHECK FAILED: $name has rows the live database does not" >&2
fi
exit "$status"
