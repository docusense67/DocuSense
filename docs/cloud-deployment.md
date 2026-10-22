# Cloud deployment (Vercel + Render + Supabase)

This guide puts ADGVS on free cloud services. It is the alternative to the on-premise Docker
stack in `admin-installation-guide.md`; the application code is the same. Follow the steps in
order the first time. Every name below (service names, secrets, bucket) is the agreed one: use
it exactly.

## 1. What runs where

| Part | Service | Address |
|---|---|---|
| Web app (React build) | Vercel, Hobby plan | `https://<project>.vercel.app` or the school's domain |
| API (the `backend/Dockerfile` image) | Render, Free web service `adgvs-api`, Singapore | `https://adgvs-api.onrender.com` |
| Database (PostgreSQL 17) and files (private bucket `adgvs-private`) | Supabase project `xbirhmmdctcqniwbbccy`, ap-southeast-1 | `https://xbirhmmdctcqniwbbccy.supabase.co` |
| Migrations, deploys, nightly backups | GitHub Actions | `.github/workflows/deploy.yml`, `backup.yml`, `keep-warm.yml` |

Users only ever open the Vercel address. Vercel forwards every `/api/...` request to Render
(`frontend/vercel.json`), so the browser sees one site and the `SameSite=Strict` login cookie
works unchanged.

```
browser ──HTTPS──> Vercel ──/api/*──> Render (adgvs-api) ──> Supabase Postgres (session pooler)
                     │                       └──────────────> Supabase Storage (adgvs-private)
                     └─ daily 01:00 Manila: GET /api/internal/cron/daily (Vercel Cron)
GitHub Actions: on push to main  -> migrations, Render deploy, smoke test
                nightly 02:00    -> encrypted backup + restore check
```

Files in this repository:

| File | Purpose |
|---|---|
| `frontend/vercel.json` | Build settings, `/api` rewrite, SPA fallback, security headers, daily cron |
| `render.yaml` | Render Blueprint for `adgvs-api` |
| `deploy/cloud/migrate.sh` | `alembic upgrade head` + `python -m app.manage setup-db-roles` + `supabase-hardening.sql` |
| `deploy/cloud/supabase-hardening.sql` | Locks the Data API roles out of ADGVS tables; creates the private bucket |
| `deploy/cloud/backup.sh`, `verify-backup.sh`, `fetch_storage.py` | Nightly backup and restore check |
| `deploy/cloud/wait-for-deploy.sh`, `smoke-test.sh` | Used by the deploy workflow |

## 2. Before you start

- Accounts: GitHub (the repository), Supabase, Render, Vercel. Sign in to Render and Vercel
  **with GitHub** so they can see the repository.
- Generate the secrets you will need. Run this once per secret and keep the values in a
  password manager (never in the repository, never in chat):

  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

  | Name | Used by |
  |---|---|
  | `ADGVS_APP_DB_PASSWORD` | the API's database login `adgvs_app` (GitHub secret; part of Render's `ADGVS_DATABASE_URL`) |
  | `ADGVS_CRON_SECRET` | Render; the **same value** is `CRON_SECRET` in Vercel |
  | `BACKUP_PASSPHRASE` | GitHub secret. Also store it **outside GitHub**: without it no backup can be restored. |

  `token_urlsafe` values only contain letters, digits, `-` and `_`, so they can go straight into
  connection strings.

## 3. Supabase

Supabase Dashboard → project `xbirhmmdctcqniwbbccy`.

1. **Database password (owner).** Project Settings → Database → *Reset database password*.
   Use a generated, URL-safe value (as above). This is the owner (`postgres`) password: it is
   only used by GitHub Actions for migrations and backups, never by the API.
2. **Connection strings.** Click **Connect** (top bar) → *Session pooler*. Copy the URI. It
   looks like this (the host can differ, for example `aws-1-...`; copy yours exactly):

   ```
   postgresql://postgres.xbirhmmdctcqniwbbccy:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
   ```

   - Use the **session** pooler, port **5432**. The direct host is IPv6-only (Render and GitHub
     runners are IPv4), and the transaction pooler (6543) breaks psycopg's prepared statements.
   - Pooler user names are `<role>.<project-ref>`: `postgres.xbirhmmdctcqniwbbccy` for the owner,
     `adgvs_app.xbirhmmdctcqniwbbccy` for the API.

   From it build the two URLs you need (add `?sslmode=require`):

   | Name | Value |
   |---|---|
   | `SUPABASE_DB_OWNER_URL` (GitHub secret) | `postgresql://postgres.xbirhmmdctcqniwbbccy:<owner password>@<pooler host>:5432/postgres?sslmode=require` |
   | `ADGVS_DATABASE_URL` (Render) | `postgresql+psycopg://adgvs_app.xbirhmmdctcqniwbbccy:<ADGVS_APP_DB_PASSWORD>@<pooler host>:5432/postgres?sslmode=require` |

   The API URL starts with `postgresql+psycopg://` (SQLAlchemy driver name); the owner URL is a
   plain `postgresql://` URL (`migrate.sh` adds the driver name itself, `pg_dump` needs it plain).
3. **Secret key.** Project Settings → API Keys → *Secret keys* → create/copy one
   (`sb_secret_...`). It goes into Render as `ADGVS_SUPABASE_SERVICE_KEY` and, for file backups,
   into GitHub as `SUPABASE_SERVICE_KEY`. Never put it in Vercel or in the web app.
4. **Turn the Data API off.** ADGVS never uses Supabase's auto-generated REST API. Project
   Settings → Data API → switch off *Enable Data API* (or remove `public` from *Exposed
   schemas*). `migrate.sh` also revokes the `anon`/`authenticated` roles' access to every ADGVS
   table, so the publishable key cannot read records even if the Data API is switched back on.
   The Security Advisor will still list "RLS disabled in public" for the ADGVS tables: RLS is
   deliberately not used (the API connects as its own login, not through the Data API), and the
   revoked grants plus the disabled Data API are the control.
5. **Private bucket `adgvs-private`.** `migrate.sh` creates it on the first run. To create it
   by hand instead: Storage → *New bucket* → name `adgvs-private`, **Public bucket off** → Save.
   Or in the SQL Editor:

   ```sql
   INSERT INTO storage.buckets (id, name, public)
   VALUES ('adgvs-private', 'adgvs-private', false)
   ON CONFLICT (id) DO UPDATE SET public = false;
   ```

   Do not add Storage policies: only the API (with the secret key) reads and writes files.

## 4. Render (the API)

1. Render Dashboard → **New** → **Blueprint** → choose the repository → branch `main`. Render
   reads `render.yaml` and shows the service `adgvs-api` (Docker, Singapore, Free).
2. It asks for the values marked `sync: false`:

   | Variable | Value |
   |---|---|
   | `ADGVS_DATABASE_URL` | the API URL from step 3.2 |
   | `ADGVS_CRON_SECRET` | the generated cron secret |
   | `ADGVS_SUPABASE_SERVICE_KEY` | the Supabase secret key |

   `ADGVS_SECRET_KEY` is generated by Render. The other settings are fixed in `render.yaml`:
   `ADGVS_STORAGE_BACKEND=supabase`, `ADGVS_STORAGE_BUCKET=adgvs-private`,
   `ADGVS_TRUSTED_PROXY_HOPS=2` (browser → Vercel → Render's proxy → API),
   `ADGVS_COOKIE_SECURE=true`, `ADGVS_API_DOCS=false`, `ADGVS_CORS_ORIGINS=[]`,
   `API_WORKERS=1` (512 MB RAM) and `PORT=8000` (the port the image listens on).
3. Click **Apply**. The first deploy starts. `/api/health` works even before the database is
   ready; the API needs the migrations of step 6 before anyone can sign in.
4. Service `adgvs-api` → Settings → **Deploy Hook** → copy the URL (it is a secret). Automatic
   deploys are off on purpose (`autoDeployTrigger: off`): Render Free has no pre-deploy step, so
   GitHub Actions runs the migrations first and then calls this hook.
5. Optional, school-specific values (Environment tab): `ADGVS_INSTITUTION_NAME`,
   `ADGVS_INSTITUTION_ADDRESS`, `ADGVS_REGISTRAR_NAME`, `ADGVS_REGISTRAR_TITLE`, and the workflow
   settings listed in `.env.example`. Add these in the dashboard. A variable that has a fixed
   `value` in `render.yaml` is set back to that value when the Blueprint syncs, so change those
   in the file instead.

## 5. Vercel (the web app)

1. Vercel Dashboard → **Add New… → Project** → import the repository.
2. **Root Directory: `frontend`**. Framework Preset: **Vite** (also set by `vercel.json`; the
   build command is `npm run build`, output `dist`). Node.js version (Settings → Build and
   Deployment): 22.x, the version CI uses.
3. Environment Variables → add `CRON_SECRET` = the same value as Render's `ADGVS_CRON_SECRET`,
   for the **Production** environment. No other variables are needed: the web app has no
   secrets.
4. Deploy. From now on Vercel's Git integration deploys every push to `main` to production by
   itself; no Vercel token is stored in GitHub.
5. Settings → Deployment Protection: keep **Vercel Authentication** on for preview
   deployments. Previews of other branches also forward `/api` to the production API, so they
   must not be open to the public.
6. Note the production address (`https://<project>.vercel.app`, or add the school's domain under
   Settings → Domains). This is `PUBLIC_URL`.

### What `vercel.json` does

- `/api/:path*` → `https://adgvs-api.onrender.com/api/:path*` (Vercel forwards the visitor's IP in
  `X-Forwarded-For` and the original host in `X-Forwarded-Host`). Responses from the API are never
  cached by Vercel (`x-vercel-enable-rewrite-caching: 0`); the API also sends `no-store`.
- Every other path that is not a file (`/requests/12`, `/records`, ...) serves `index.html`, so
  client-side routes survive a page reload. Missing `/assets/...` files stay 404.
- Security headers for the web app, the same as `deploy/nginx/snippets/security-headers.conf`:
  CSP without `'unsafe-inline'`, HSTS, `X-Content-Type-Options`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: SAMEORIGIN` and `frame-ancestors 'self'` (the registrar's PDF review frame is
  same-origin), `Permissions-Policy`, COOP/CORP. API responses keep the API's own headers.
- `/assets/*` (hashed file names) are cached for a year; `index.html` is `no-cache`.
- `crons`: `GET /api/internal/cron/daily` at `0 17 * * *` UTC = **01:00 Asia/Manila**. Vercel
  sends `Authorization: Bearer <CRON_SECRET>`; the API runs the daily jobs (download-window
  expiry, idle-session cleanup) when it matches `ADGVS_CRON_SECRET`. On Hobby, cron jobs run once
  a day and may fire any time within that hour (01:00–01:59), and a failed run is not retried.

### Changing the API address

`vercel.json` rewrites cannot read environment variables, so the Render address is written in
the file. If the API moves (another Render service name, a custom API domain), edit the
`destination` of the first rewrite in `frontend/vercel.json`, commit and push; Vercel redeploys.
Also set the GitHub variable `API_URL` (section 6). (Vercel's lower-level `routes` syntax can
expand variables, but mixing it with `rewrites`/`headers` makes the file harder to follow; a
one-line edit is simpler for a service that rarely moves.)

## 6. GitHub (migrations, deploys, backups)

Repository → Settings → Secrets and variables → Actions.

**Secrets**

| Secret | Required for | Value |
|---|---|---|
| `SUPABASE_DB_OWNER_URL` | deploy, backup | owner URL from step 3.2 |
| `ADGVS_APP_DB_PASSWORD` | deploy | the API login's password (the one inside Render's `ADGVS_DATABASE_URL`) |
| `RENDER_DEPLOY_HOOK_URL` | deploy | deploy hook from step 4.4 |
| `BACKUP_PASSPHRASE` | backup | backup passphrase |
| `PUBLIC_URL` | deploy (smoke test) | Vercel address; may be a *variable* instead, it is not secret |
| `SUPABASE_SERVICE_KEY` | backup, optional | Supabase secret key: also back up the files in `adgvs-private` |
| `RENDER_API_KEY` | deploy, optional | Render API key (Account Settings → API Keys). Lets the deploy job follow the Render build and fail when it fails. It can manage every service in the Render workspace, so add it only if you accept that. |

**Variables** (optional): `PUBLIC_URL`; `API_URL` (default `https://adgvs-api.onrender.com`);
`SUPABASE_URL` (default the project above); `KEEP_WARM=off` to stop the keep-warm pings.

Without the required secrets the workflows end as *skipped* (forks, or before setup).

### The deploy workflow (`.github/workflows/deploy.yml`)

Runs on every push to `main` (except documentation-only and scan-agent-only changes) and by
hand (Actions → *Deploy (cloud)* → Run workflow):

1. Builds the API image from `backend/Dockerfile` (the one Render builds).
2. Runs `deploy/cloud/migrate.sh` in it with the owner URL: `alembic upgrade head`,
   `python -m app.manage setup-db-roles` (creates/updates `adgvs_app` with
   `ADGVS_APP_DB_PASSWORD`: data access only, audit log insert-only), then
   `supabase-hardening.sql`. This is the cloud copy of the `migrate` service in
   `docker-compose.yml`.
3. Calls the Render deploy hook for this exact commit (`&ref=<sha>`).
4. Waits for the new version (`wait-for-deploy.sh`): with `RENDER_API_KEY`, until Render reports
   the deploy `live`; if `/api/health` reports a `commit`, until it equals the pushed commit;
   otherwise it only waits for a healthy answer and prints a warning.
5. Smoke test through `PUBLIC_URL` (`smoke-test.sh`): health through the rewrite, security
   headers, an SPA route returning `index.html`, the login page's scripts loading and
   `/api/auth/me` answering 401, the cron endpoint closed without the secret, API docs off.

Migrations always run before the new code starts. Write migrations so that the **previous**
version of the API still works against the new schema (add columns before using them, drop them
one release later): the old version keeps serving until Render switches over.

Vercel deploys the web app at the same time. When a change needs both, the new web app may talk
to the old API for a few minutes.

The deploy does not wait for the CI workflow: merge to `main` only after CI is green on the pull
request. (To make it wait, change the trigger to `workflow_run` of `CI` on `main` with
`conclusion == 'success'`; deploys then start 15+ minutes later.)

**First run.** Run the workflow by hand once all secrets are set. It creates the schema, the
`adgvs_app` login and the bucket, and redeploys the API.

Running the migrations from a PC instead (git-bash, in a backend virtual environment; `read -s`
keeps the values out of the shell history):

```bash
cd backend
read -rsp 'Owner URL: ' SUPABASE_DB_OWNER_URL; echo; export SUPABASE_DB_OWNER_URL
read -rsp 'App DB password: ' ADGVS_APP_DB_PASSWORD; echo; export ADGVS_APP_DB_PASSWORD
bash ../deploy/cloud/migrate.sh
```

## 7. Go-live commands (first administrator, templates)

Run once, from a PC with the backend installed (`cd backend`, virtual environment active).
Use the **API's login** (`adgvs_app`), not the owner: these commands only add rows.

```bash
cd backend
read -rsp 'API database URL: ' ADGVS_DATABASE_URL; echo; export ADGVS_DATABASE_URL
python -m app.manage install-templates
python -m app.manage create-admin it@school.edu.ph
unset ADGVS_DATABASE_URL
```

`ADGVS_DATABASE_URL` is the `postgresql+psycopg://adgvs_app.<ref>:...` URL from step 3.2.
`create-admin` prints a one-time password; sign in at `PUBLIC_URL` and change it. Then continue
with section 4 of `admin-installation-guide.md` (loading the school's data) in the web app.

## 8. Daily jobs and keep-warm

- **Daily jobs**: Vercel Cron, see section 5. Check them in Vercel → project → Settings → Cron
  Jobs (*View Logs*; the *Run* button triggers one now). A run that finds the API asleep waits for
  it to wake up (Vercel allows a proxied request 120 seconds; a cold start usually takes under a
  minute). The jobs are safe to run twice.
- **Keep-warm** (`.github/workflows/keep-warm.yml`): Render Free stops the API after 15 minutes
  without requests, and the next request waits about a minute. The workflow calls `/api/health`
  every 10 minutes on weekdays 07:00–18:00 Manila time, which in GitHub's UTC cron is
  `*/10 23 * * 0-4`, `*/10 0-9 * * 1-5` and `0 10 * * 1-5`. GitHub runs schedules on a best-effort
  basis (late or skipped runs happen), so an occasional cold start can still occur.

## 9. Backups and restore check

`.github/workflows/backup.yml` runs every night at **02:00 Manila** (`0 18 * * *` UTC) and by
hand (Actions → *Backup (cloud)* → Run workflow).

1. `deploy/cloud/backup.sh`: `pg_dump` 17 (the same major version as Supabase; it stops with a
   clear message after a Supabase upgrade, then change `PG_MAJOR` and the service image in the
   workflow) of the `public` schema, which holds all ADGVS data. With `SUPABASE_SERVICE_KEY` it
   also downloads every file of `adgvs-private`. Both are encrypted with AES-256 and
   `BACKUP_PASSPHRASE` exactly like the on-premise `deploy/backup/backup.sh`, in the same layout:
   `<YYYYmmdd-HHMMSS>/db.dump.gpg`, `storage.tar.gz.gpg`, `SHA256SUMS`.
2. The folder is uploaded as the artifact `adgvs-backup-<timestamp>`, kept **30 days**.
3. `restore-check` downloads that artifact, restores it into a throwaway PostgreSQL 17 and
   compares row counts with the live database (`deploy/cloud/verify-backup.sh`, the same checks
   as `deploy/backup/verify-backup.sh`). The log ends with `RESTORE CHECK PASSED`.

GitHub e-mails the person who last changed the workflow's schedule when a scheduled run fails.
Check the Actions tab weekly and record a restore check on the UAT sign-off sheet monthly.

Notes:

- Supabase's Free plan has no downloadable backups of its own, so this workflow is the backup.
- Artifacts can be downloaded by anyone who can read the repository (everyone, if it is
  public). They are useless without the passphrase: keep it long and secret.
- Without `SUPABASE_SERVICE_KEY` the issued PDFs and scans are **not** backed up (the run shows a
  warning). Issued PDFs can be regenerated from their snapshots; scans cannot.
- Artifact storage: GitHub's free quota for private repositories is 500 MB in total; 30 nightly
  copies of all scans can exceed it. Watch Settings → Billing, or make the repository public
  (artifacts in public repositories are free; contents stay encrypted).

**Restoring (disaster recovery).** Download the artifact (Actions → the run → Artifacts), unzip
it, then on a PC with PostgreSQL 17 client tools and gpg:

```bash
gpg --decrypt db.dump.gpg > db.dump      # asks for BACKUP_PASSPHRASE
# Into an EMPTY Supabase project (new or reset). Supabase already has the public schema, so
# skip the dump's CREATE SCHEMA public and its comment:
pg_restore -l db.dump | grep -v -E 'SCHEMA - public|COMMENT - SCHEMA public' > restore.list
pg_restore --dbname="$SUPABASE_DB_OWNER_URL" --no-owner --no-acl --exit-on-error \
    -L restore.list db.dump
cd backend && bash ../deploy/cloud/migrate.sh   # API login, grants, hardening, bucket
```

Files: `gpg --decrypt storage.tar.gz.gpg | tar -xz` gives a `storage/` folder; upload its
contents to the `adgvs-private` bucket (Supabase Dashboard → Storage → the bucket → upload the
folders, keeping the same paths). Rehearse this once on a scratch project before go-live; it has
not yet been run against a real Supabase project.

## 10. Free-tier limits (check the providers' pricing pages; they change)

| Service | Limit that matters for ADGVS |
|---|---|
| Render Free | Sleeps after 15 min idle, ~1 min to wake; 750 instance-hours a month per workspace; 512 MB RAM, 0.1 CPU; no disk (files are in Supabase); no pre-deploy command, no shell. Fine for a pilot, slow for 100 users at once (Section 21's load test targets the on-premise server). |
| Supabase Free | 500 MB database, 1 GB file storage; project **paused after a week without activity** (the nightly backup and daily jobs count as activity); no downloadable platform backups. |
| Vercel Hobby | **Personal, non-commercial use only**; for the school's production use, the school should decide whether its use needs Vercel Pro. Cron once a day, within the hour. |
| GitHub Actions | Free and unlimited minutes on public repositories; private: 2,000 minutes and 500 MB artifact storage a month. Keep-warm uses about 1,500 runs a month; set `KEEP_WARM=off` if minutes run short. |

## 11. Handover to school-owned accounts

The Supabase organisation (DocuSense), Vercel team and Render workspace are the development
team's. Before the school takes over:

1. The school creates its own accounts (an IT e-mail address, not a person's) and enables
   two-factor authentication.
2. Move each service:
   - Supabase: Project Settings → General → *Transfer project* to the school's organisation
     (the project ref and URLs stay the same).
   - Vercel: Project → Settings → General → *Transfer project* to the school's team.
   - Render: invite the school's account to the workspace as Admin, then remove the team's
     members (or recreate the service from `render.yaml` in the school's workspace and update the
     deploy hook and, if the name changes, `frontend/vercel.json`).
   - GitHub: transfer the repository or add the school's organisation as owner.
3. **Rotate every secret** after the transfer, then update where each is stored: Supabase
   database password (→ `SUPABASE_DB_OWNER_URL`), Supabase secret key (→ Render, GitHub),
   `ADGVS_APP_DB_PASSWORD` (→ GitHub, then run the deploy workflow so `setup-db-roles` sets it, then
   Render's `ADGVS_DATABASE_URL`), `ADGVS_CRON_SECRET`/`CRON_SECRET`, the Render deploy hook
   (*Regenerate*), `RENDER_API_KEY` if used.
4. `BACKUP_PASSPHRASE`: hand the current one over securely (older backups need it), then set a
   new one; note the date of the change next to both.
5. Remove the development team's access everywhere and record the handover date.

## 12. Troubleshooting

| Symptom | Check |
|---|---|
| Deploy fails at migrations with `password authentication failed` | Owner URL user must be `postgres.<ref>` (session pooler), password URL-safe or URL-encoded. |
| `setup-db-roles` fails with `permission denied to alter role` on the second deploy | The command adds `NOSUPERUSER` when altering an existing login; Supabase's `postgres` is not a superuser and may not change that attribute. Fixed when `setup_db_roles` in `backend/app/manage.py` no longer passes `NOSUPERUSER` to `ALTER ROLE`. |
| Web app loads but every API call is 502/504 | Render service asleep or failed: open `https://adgvs-api.onrender.com/api/health`, check Render logs. |
| API 500 on sign-in, Render log shows database errors | `ADGVS_DATABASE_URL`: `postgresql+psycopg://`, user `adgvs_app.<ref>`, port 5432, `sslmode=require`; did the migrations run? |
| Daily jobs not running | Vercel → Settings → Cron Jobs → logs. 401 means `CRON_SECRET` ≠ `ADGVS_CRON_SECRET`. |
| Backup fails with `Version mismatch` | Supabase upgraded PostgreSQL: set `PG_MAJOR` and the `postgres:` service image in `backup.yml` to the new major version. |
