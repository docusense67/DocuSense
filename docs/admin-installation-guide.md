# Admin and installation guide

For the school's IT staff who install, run and look after the system. It covers the server
(web app, API, database, backups) and connecting the scan station. Day-to-day use of the
screens is in [docs/manuals/](manuals/README.md). The scanner hardware (wiring, parts, trays)
is documented separately by the hardware team.

## 1. What runs where

| Service | What it does | Reachable from |
| --- | --- | --- |
| `web` (Nginx) | HTTPS, redirects HTTP to HTTPS, serves the web app, forwards `/api/` to the API | Campus network, ports 80 and 443 |
| `api` | The application. Connects to the database with a restricted login | Only `web` |
| `migrate` | Runs once at each start: database updates, then sets the API login's permissions | Nothing (exits) |
| `jobs` | Once a day: closes expired download windows, removes idle login sessions | Nothing |
| `backup` | Once a day: encrypted backup of the database and stored PDFs/scans | Nothing |
| `db` | PostgreSQL 16 | Only the services above; never published on the network |

Everything is defined in `docker-compose.yml` at the top of the repository.

## 2. Server requirements

- Linux server (Ubuntu 24.04 LTS or similar) with Docker Engine and the Docker Compose plugin.
- 4 CPU cores, 8 GB RAM and 100 GB disk is enough for the pilot (5,000 students, 20,000 documents; Section 21).
- **Disk encryption** on the server (for example LUKS at install time). Section 18 requires it; the application cannot do it for you.
- A **second disk or a NAS share** for backups, so one copy survives a server failure.
- A fixed IP address on the campus network and a DNS name for it, for example `adgvs.school.edu.ph`.
- Clock synchronised (NTP). Download links and audit times depend on it.

## 3. First installation

1. **Get the code.** Clone the repository to `/opt/adgvs` (or copy a release archive there).
2. **Create the settings file.**
   ```bash
   cd /opt/adgvs
   cp .env.example .env
   chmod 600 .env
   ```
   Fill in every empty value. Generate each secret with
   `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`:

   | Setting | What to put |
   | --- | --- |
   | `SERVER_NAME` | The DNS name users type, e.g. `adgvs.school.edu.ph` |
   | `POSTGRES_PASSWORD` | A new secret (database owner; used only for updates and backups) |
   | `ADGVS_APP_DB_PASSWORD` | A different secret (the application's restricted login) |
   | `ADGVS_SECRET_KEY` | A different secret (signs download links) |
   | `BACKUP_PASSPHRASE` | A different secret. **Store a copy off the server** (for example in the registrar's safe). Without it no backup can be restored. |
   | `BACKUP_DIR` | The backup disk or NAS mount, e.g. `/mnt/backup/adgvs` |
   | `ADGVS_INSTITUTION_NAME`, `ADGVS_INSTITUTION_ADDRESS`, `ADGVS_REGISTRAR_NAME`, `ADGVS_REGISTRAR_TITLE` | Printed on every document |
   | `ADGVS_PAYMENT_REQUIRED`, `ADGVS_AUTO_RELEASE_ON_APPROVAL`, `ADGVS_DOWNLOAD_WINDOW_DAYS` | The school's request policy (Section 14) |

   Leave `ADGVS_COOKIE_SECURE=true` and `ADGVS_API_DOCS=false`.
3. **Install the HTTPS certificate** as `deploy/certs/fullchain.pem` and `deploy/certs/privkey.pem`.
   - Best: a certificate from the school's IT or a public certificate authority for `SERVER_NAME`.
   - Campus-only server without one: `bash deploy/make-self-signed-cert.sh adgvs.school.edu.ph 192.168.1.20`
     (name, then the server's IP). Then install `deploy/certs/fullchain.pem` as a trusted
     certificate on staff PCs and on the scan station, or browsers will show a warning.
4. **Start everything.**
   ```bash
   docker compose up -d --build
   docker compose ps          # all running; migrate shows "exited (0)"
   ```
5. **Create the first administrator.**
   ```bash
   docker compose exec api python -m app.manage create-admin it.admin@school.edu.ph
   ```
   It prints a one-time password. Sign in at `https://SERVER_NAME` with it; the system asks
   for a new password straight away. Create the registrar accounts from **Accounts & stations**.
6. **Install the document templates.** Until the school's official TOR and COE formats are
   converted, install the stand-in versions:
   ```bash
   docker compose exec api python -m app.manage install-templates
   ```
   The registrar replaces them later from **Templates** (new version, preview, activate).
7. **Do not load the demo data** (`python -m app.seed`) on the production server. It creates
   fictional students with a shared password.
8. **Run the restore check once** (section 6) before go-live. Section 26 requires it.

## 4. Loading the school's data

The registrar does this from the web app, in this order:

1. **Academic setup:** programs, subjects, terms, teachers (with their logins), class sections.
2. **Records → Import students:** CSV or Excel; see [import templates](import-templates/README.md).
   If any row has a problem, nothing is imported and every problem is listed.
3. **Records → Students → Manage → Create login** for students who will use the portal.
4. **Academic setup → Class sections → Enroll students:** paste the class list's student numbers.
5. Teachers enter or upload grades; the registrar locks each term when grades are final.

## 5. Backups

- The `backup` service runs every day at `BACKUP_TIME` (default 02:00, Philippine time).
- Each backup is a folder `YYYYmmdd-HHMMSS` in `BACKUP_DIR` with three files:
  - `db.dump.gpg`: the database, encrypted with AES-256.
  - `storage.tar.gz.gpg`: the issued PDFs and scan images, encrypted.
  - `SHA256SUMS`: checksums used to detect damaged files.
- Backups older than `BACKUP_RETENTION_DAYS` (default 30) are deleted automatically.
- **Recovery point:** at most 24 hours of work can be lost (Section 21).
- **Check it is working** once a week:
  ```bash
  ls -l "$BACKUP_DIR"                    # a folder for every day
  docker compose logs --tail 20 backup   # "backup ... done"
  ```
- **Take one now**, before an update for example:
  ```bash
  docker compose exec backup backup.sh
  ```
- **Off-server copy:** if `BACKUP_DIR` is on the server itself, copy it elsewhere every day
  (for example `rsync` to the school NAS from a cron job). The files are already encrypted.

## 6. Restore check (no effect on the live system)

Run this before go-live and then once a month:

```bash
docker compose exec backup verify-backup.sh             # newest backup
docker compose exec backup verify-backup.sh 20261015-020000
```

It decrypts the backup and restores it into a scratch database, which is deleted afterwards.
It then compares row counts with the live database and lists the archived files. It ends with
`RESTORE CHECK PASSED`. Record the date and result on the UAT sign-off sheet.

## 7. Disaster recovery (replace the live data with a backup)

**Everything entered after that backup is lost.** Use it only when the live database is lost
or damaged. Allow about an hour; Section 21's target is under 4 hours.

1. On a new server, follow section 3 steps 1–4 with the **same** `.env` (above all the same
   `BACKUP_PASSPHRASE`) and certificate. Copy the backup folder into `BACKUP_DIR`.
2. Stop the services that write data:
   ```bash
   docker compose stop api jobs web
   ```
3. Restore (the `--yes` is required on purpose):
   ```bash
   docker compose exec backup restore.sh 20261015-020000 --yes
   ```
4. Re-apply database updates and permissions, then start again:
   ```bash
   docker compose run --rm migrate
   docker compose start api jobs web
   ```
5. Sign in and open a recent request and a recent scan to confirm the data is there.

## 8. Updates

Apply operating-system and Docker image security updates monthly during the pilot (Section 18).

```bash
cd /opt/adgvs
docker compose exec backup backup.sh        # backup first
git pull                                    # or unpack the new release
docker compose up -d --build                # migrate runs database updates automatically
docker compose ps
```

If something goes wrong, go back to the previous release (`git checkout <previous tag>`) and
`docker compose up -d --build`. A database update that already ran is not undone by this; if
the release notes say an update changes stored data, restore the backup taken just before
(section 7).

## 9. Accounts and access

- **Admins** create registrar and admin accounts, reset passwords, deactivate accounts and
  scan stations (**Accounts & stations**).
- **Registrars** create student and teacher logins with their records and reset their passwords.
- **Locked accounts:** after 5 wrong passwords an account is locked for 15 minutes, then
  unlocks by itself. A password reset unlocks it at once.
- If an admin is locked out completely, create another admin from the server:
  `docker compose exec api python -m app.manage create-admin backup.admin@school.edu.ph`.
- Every one of these actions is in the **Audit trail**. Nobody can edit or delete audit
  entries: the application's database login is not allowed to, and the database itself refuses it.

## 10. Connecting the scan station

For the Raspberry Pi station (Linux, SANE scanner, systemd service) follow
[hardware/README.md](hardware/README.md) instead; the steps below are for a Windows station PC.

1. In **Accounts & stations → Scan stations**, register the station. Copy the API key shown;
   it is shown only once. Deactivate the station there if the PC is lost or replaced.
2. On the station PC, install Python 3.12, Tesseract 5 and the scan agent ([scan-agent/README.md](../scan-agent/README.md)).
3. Set the scanner software to save each scan into one folder (for example `D:\scans`) as
   PNG, TIFF or PDF at 300 DPI.
4. Run the agent as a service that starts at sign-in (a Windows scheduled task "At log on",
   restart on failure), with the server address, the key, and (for a self-signed or school
   certificate) the certificate file:
   ```bash
   python -m scan_agent --server https://adgvs.school.edu.ph --key <API key> \
       --ca-cert fullchain.pem --folder D:\scans --watch --delete-after-upload
   ```
   - `--watch` keeps it running: it reports the station's state to the dashboard every 15 s
     and checks each stack the scanner saves in the session the operator opened.
   - `--delete-after-upload` removes the image files once they have been sent, so no scans
     are kept on the station (Section 18).
   - Keep the key in an environment variable (`ADGVS_STATION_KEY`) on a locked-down Windows
     account, not in a shortcut others can read.
5. Check it on the operator's **Scan station** screen: the station shows **● Ready ·
   Waiting for paper**. "No signal from the station since …" means the agent is not running.

## 11. Health checks and logs

```bash
docker compose ps                         # api shows "healthy"
curl -fsS https://SERVER_NAME/api/health  # {"status":"ok"}
docker compose logs --tail 100 api        # application errors
docker compose logs --tail 20 jobs        # daily expiry and session clean-up
```

Logs do not contain passwords or session tokens. Who did what is in the Audit trail screen,
not in these logs.

## 12. Security summary (Section 18)

| Requirement | How it is met |
| --- | --- |
| HTTPS, HTTP redirected | Nginx, TLS 1.2/1.3 only, HSTS |
| Database not exposed | `db` has no published port |
| Least-privilege database access | The API's login cannot change tables; audit log is insert-only (plus a database trigger) |
| Backups encrypted, 30 days, off-server copy | `backup` service (GPG AES-256) and `BACKUP_DIR` on a separate disk or NAS |
| Restore tested | `verify-backup.sh` before go-live and monthly |
| Disk encryption | Server installation (section 2) |
| Security headers | Content-Security-Policy, X-Frame-Options, nosniff, Referrer-Policy on every page |
| Login abuse | 5-attempt lockout for 15 minutes, login rate limit in Nginx, same answer for unknown and wrong accounts |
| Vulnerability scan | OWASP ZAP baseline runs against the full stack in CI on every change; no High findings allowed |
