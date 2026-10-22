"""GET /api/internal/cron/daily: the cloud deployment's daily jobs, called by Vercel Cron."""

from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog, DocumentRequest, GeneratedDocument, UserSession
from app.models.enums import RequestStatus
from app.timeutil import utcnow
from tests.test_requests import as_user, isolated, seeded, submit_and_release  # noqa: F401

URL = "/api/internal/cron/daily"
SECRET = "cron-secret-for-tests-0123456789abcdef"


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setattr(get_settings(), "cron_secret", SecretStr(SECRET))


def test_route_is_off_when_no_secret_is_configured(client):
    assert get_settings().cron_secret.get_secret_value() == ""  # on-prem default
    assert client.get(URL).status_code == 404
    assert client.get(URL, headers={"Authorization": "Bearer "}).status_code == 404


@pytest.mark.parametrize("authorization", [
    None, "", "Bearer", "Bearer ", "Bearer wrong", f"Bearer {SECRET[:-1]}", f"Bearer {SECRET}x",
    f"bearer{SECRET}", f"Basic {SECRET}", SECRET, f"Bearer  {SECRET}",
    "Bearer \u00e9".encode("latin-1"),  # non-ASCII bytes
])
def test_wrong_or_missing_secret_is_401(armed, client, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    res = client.get(URL, headers=headers)
    assert res.status_code == 401
    assert res.headers["WWW-Authenticate"] == "Bearer"


def test_a_user_session_or_station_key_does_not_open_it(armed, as_user):
    for handle in ("registrar", "admin"):
        client = as_user(handle)
        assert client.get(URL).status_code == 401
        assert client.get(URL, headers={"X-Station-Key": "anything"}).status_code == 401


def test_daily_jobs_run_and_are_audited(armed, as_user, seeded, client):
    submit_and_release(as_user)
    client.post("/api/auth/logout")
    doc = seeded.scalar(select(GeneratedDocument))
    doc.download_expires_at = utcnow() - timedelta(minutes=1)
    user_id = doc.request.student.user_id
    seeded.add(UserSession(token_hash="c" * 64, user_id=user_id, created_at=utcnow(),
                           last_seen_at=utcnow() - timedelta(hours=3)))
    seeded.commit()

    res = client.get(URL, headers={"Authorization": f"Bearer {SECRET}"})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "results": ["1 request(s) moved to Expired",
                                                  "1 idle login session(s) removed"]}
    assert res.headers["Cache-Control"] == "no-store"
    assert res.headers["X-Content-Type-Options"] == "nosniff"

    assert seeded.scalar(select(DocumentRequest)).status == RequestStatus.EXPIRED
    assert seeded.scalar(select(UserSession).where(UserSession.token_hash == "c" * 64)) is None
    assert seeded.scalar(select(AuditLog).where(AuditLog.action == "request_expired"))

    again = client.get(URL, headers={"Authorization": f"Bearer {SECRET}"})  # idempotent
    assert again.json()["results"][0] == "0 request(s) moved to Expired"


def test_short_secret_is_rejected_at_startup():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(cron_secret="short")
