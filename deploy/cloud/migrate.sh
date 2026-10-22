#!/bin/bash
# Database migrations + the API's least-privilege login on Supabase, as the database owner.
# Same two commands as the `migrate` service in docker-compose.yml, plus Supabase-only
# hardening (supabase-hardening.sql). Render Free has no pre-deploy command, so
# .github/workflows/deploy.yml runs this before every API deploy. Safe to run repeatedly.
#
# Environment:
#   SUPABASE_DB_OWNER_URL  owner login through the Supavisor SESSION pooler (port 5432), e.g.
#       postgresql://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres?sslmode=require
#   ADGVS_APP_DB_PASSWORD  password of the API login (16+ characters, URL-safe)
#   ADGVS_APP_DB_USER      optional, default adgvs_app. The plain role name: the pooler user
#                          in the API's ADGVS_DATABASE_URL is <name>.<project-ref>.
#
# Run it where the backend code is installed, from the backend directory:
#   docker run --rm -e SUPABASE_DB_OWNER_URL -e ADGVS_APP_DB_PASSWORD \
#       -v "$PWD/deploy/cloud:/cloud:ro" adgvs-api bash /cloud/migrate.sh      (API image)
#   cd backend && bash ../deploy/cloud/migrate.sh                              (backend venv)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
owner="${SUPABASE_DB_OWNER_URL:?SUPABASE_DB_OWNER_URL is not set}"
: "${ADGVS_APP_DB_PASSWORD:?ADGVS_APP_DB_PASSWORD is not set}"

if [ ! -f alembic.ini ]; then
    echo "Run this from the backend directory (alembic.ini not found in $PWD)." >&2
    exit 2
fi
case "$owner" in
    postgresql://* | postgres://*) ;;
    *) echo "SUPABASE_DB_OWNER_URL must start with postgresql://" >&2; exit 2 ;;
esac
case "$owner" in
    *:6543/*)
        echo "SUPABASE_DB_OWNER_URL uses the transaction pooler (port 6543); use the session" \
             "pooler (port 5432) for migrations." >&2
        exit 2 ;;
esac
case "$owner" in
    *sslmode=*) ;;
    *\?*) owner="$owner&sslmode=require" ;;
    *) owner="$owner?sslmode=require" ;;
esac

# Alembic and app.manage read the URL through the app settings (SQLAlchemy + psycopg 3).
export ADGVS_DATABASE_URL="postgresql+psycopg://${owner#*://}"
export ADGVS_APP_DB_USER="${ADGVS_APP_DB_USER:-adgvs_app}"

alembic upgrade head
python -m app.manage setup-db-roles

HARDENING_SQL=$(cat "$here/supabase-hardening.sql") OWNER_URL="$owner" python - <<'PY'
import os

import psycopg

with psycopg.connect(os.environ["OWNER_URL"], autocommit=True) as conn:
    conn.execute(os.environ["HARDENING_SQL"])
print("Supabase hardening applied: Data API roles revoked, private bucket present.")
PY
