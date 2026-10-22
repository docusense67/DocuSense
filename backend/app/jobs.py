"""Scheduled maintenance. Runs `daily` once a day: on-prem the `jobs` service
(docker-compose.yml); in the cloud Vercel Cron calls GET /api/internal/cron/daily.

    python -m app.jobs daily              # both of the below
    python -m app.jobs expire-downloads   # close download windows (Section 14)
    python -m app.jobs purge-sessions     # delete login sessions past the idle timeout
"""

import sys
from datetime import timedelta

from sqlalchemy import delete

from app.config import get_settings
from app.db import SessionLocal
from app.models import UserSession
from app.services.workflow import expire_overdue
from app.timeutil import utcnow


def expire_downloads(db) -> str:
    return f"{expire_overdue(db)} request(s) moved to Expired"


def purge_sessions(db) -> str:
    cutoff = utcnow() - timedelta(minutes=get_settings().session_idle_minutes)
    deleted = db.execute(delete(UserSession).where(UserSession.last_seen_at < cutoff)).rowcount
    return f"{deleted} idle login session(s) removed"


JOBS = {"expire-downloads": [expire_downloads], "purge-sessions": [purge_sessions],
        "daily": [expire_downloads, purge_sessions]}


def run(db, name: str) -> list[str]:
    """Run the named job list, committing after each job (an expired request and its audit
    entry are saved together, before the next job starts)."""
    results = []
    for job in JOBS[name]:
        results.append(job(db))
        db.commit()
    return results


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in JOBS:
        print(__doc__)
        return 2
    with SessionLocal() as db:
        for line in run(db, argv[0]):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
