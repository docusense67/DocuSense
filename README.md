# Academic Document Generation & Validation System

Web system that generates TOR/COE documents from registrar data, plus a scan station that reads printed copies and checks them against the database (VALID / INVALID / NEEDS REVIEW).

- Spec: [docs/MVP-Specification.md](docs/MVP-Specification.md)

## Layout

| Folder | What | Stack |
| --- | --- | --- |
| `backend/` | REST API, document generation, validation rules | Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16 |
| `frontend/` | Student / teacher / registrar / operator dashboards | React + Vite + TypeScript + Tailwind |
| `scan-agent/` | Scan station: capture, clean-up, QR + OCR, upload | Python, OpenCV, Tesseract, pyzbar |

## Backend quick start

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest                                           # runs on in-memory SQLite, no DB needed
```

## Production / pilot server (Docker)

Nginx with HTTPS, the API on a least-privilege database login, daily encrypted backups and a
daily maintenance job. Full steps, backups, restore and updates:
[docs/admin-installation-guide.md](docs/admin-installation-guide.md).

```bash
cp .env.example .env                                   # fill in every secret
bash deploy/make-self-signed-cert.sh <server name>     # or install the school's certificate
docker compose up -d --build
docker compose exec api python -m app.manage create-admin <email>
docker compose exec api python -m app.manage install-templates
```

CI builds and tests this exact stack on every push: HTTPS and headers, a TOR generated and
downloaded inside it, database permissions, backup plus restore check, and an OWASP ZAP scan.

## Documentation

- User manuals: [docs/manuals/](docs/manuals/README.md)
- Template authoring: [docs/template-authoring.md](docs/template-authoring.md)
- Data dictionary: [docs/data-dictionary.md](docs/data-dictionary.md)
- UML diagrams (use cases, class diagrams generated from the models): [docs/diagrams/](docs/diagrams/README.md)
- Personal-data inventory for the DPO: [docs/privacy-data-inventory.md](docs/privacy-data-inventory.md)
- Test plan: [docs/test-plan.md](docs/test-plan.md)
- API reference: [docs/api/openapi.json](docs/api/openapi.json) (regenerate with `backend/scripts/export_openapi.py`)

## Demo accounts (development only, after `python -m app.seed`)

`admin@`, `registrar@` (validation operator), `teacher@`, `maria.santos@`, `juan.delacruz@`, `ana.garcia@` at `school.edu.ph`. Initial password is `ChangeMe-2026`, and each account must change it on first login. The data is fictional and for development only.

## Run the whole system locally (no Docker)

For UI work and demos on a machine without WeasyPrint (e.g. Windows):

```bash
cd backend && .venv/Scripts/python scripts/dev_server.py        # API on :8000, throwaway SQLite
cd frontend && npm run dev                                      # UI on :5173
cd scan-agent && .venv/Scripts/python -m scan_agent --server http://localhost:8000 \
    --key dev-station-key --folder <folder of scans or PDFs>   # after starting a session in the UI
```

`dev_server.py` is dev-only. Demo accounts skip the forced password change, PDFs are the CI sample files, and the sample TOR is pre-issued so scanning it returns VALID. Never use it in production.
