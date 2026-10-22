"""Machine-to-machine routes. Not for user sessions or scan stations.

GET /api/internal/cron/daily runs the daily jobs (app/jobs.py) for the cloud deployment,
where Vercel Cron calls it with `Authorization: Bearer <CRON_SECRET>`. On-prem the `jobs`
container runs them instead and ADGVS_CRON_SECRET stays unset, which turns the route off.
"""

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app import jobs
from app.config import get_settings
from app.db import get_db

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger(__name__)


def require_cron_secret(authorization: str | None = Header(None)) -> None:
    secret = get_settings().cron_secret.get_secret_value()
    if not secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")  # route is off
    expected = f"Bearer {secret}".encode()
    # Constant-time: the comparison takes as long for a near miss as for garbage.
    if not hmac.compare_digest((authorization or "").encode(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid cron credentials",
                            headers={"WWW-Authenticate": "Bearer"})


@router.get("/cron/daily", dependencies=[Depends(require_cron_secret)])
def cron_daily(db: Session = Depends(get_db)):
    """Expire closed download windows and purge idle sessions. Each expired request is
    audited by the job itself, in the same transaction (Section 17)."""
    results = jobs.run(db, "daily")
    log.info("daily jobs: %s", "; ".join(results))
    return {"ok": True, "results": results}
