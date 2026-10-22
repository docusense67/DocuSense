"""Route-by-route access sweep (Section 18: permission checks on every endpoint, server-side).

Every API route is called anonymously and as each role. The routes a role gets past the
permission check on are pinned below, so a new route fails this test until someone decides
who may call it. Passing the check is not the same as seeing data: row-level rules (own
records only, own sections only) are tested per feature.
"""

import re

import pytest
from sqlalchemy import select

from app.main import app
from app.models import User
from app.seed import DEMO_PASSWORD, seed

PUBLIC = {"GET /api/health", "POST /api/auth/login"}

# Called with the station API key, never a user session.
STATION = {
    "GET /api/station/session",
    "POST /api/station/lookup",
    "POST /api/station/sessions/{session_id}/documents",
    "POST /api/station/status",
    # incoming-document screening (docs/intake/screening-contract.md)
    "POST /api/station/intake/batches",
    "POST /api/station/intake/batches/{batch_id}/end",
    "POST /api/station/intake/documents",
    "POST /api/station/intake/documents/{intake_id}/routed",
}

# Called by the scheduler (Vercel Cron) with `Authorization: Bearer <ADGVS_CRON_SECRET>`, never
# with a user session or a station key. The sweep runs with a secret configured, so these
# answer 401 to every caller below and sit in no role's set; without a secret they are 404.
INTERNAL = {
    "GET /api/internal/cron/daily",
}

ANY_USER = {
    "GET /api/notifications",
    "POST /api/notifications/read",
    "GET /api/auth/me",
    "POST /api/auth/change-password",
    "POST /api/auth/logout",
    "POST /api/documents/{document_id}/download-link",  # own released documents only
    "GET /api/files/{token}",
}
CATALOG = {"GET /api/programs", "GET /api/subjects", "GET /api/terms"}

STUDENT = ANY_USER | CATALOG | {
    "GET /api/students/me",
    "GET /api/students/me/records",
    "GET /api/requests",
    "POST /api/requests",
    "GET /api/requests/eligibility",
    "GET /api/requests/{request_id}",
    "POST /api/requests/{request_id}/cancel",
}

GRADES = {
    "GET /api/sections",
    "GET /api/sections/{section_id}/enrollments",
    "GET /api/sections/{section_id}/grades",
    "PUT /api/sections/{section_id}/grades",
    "POST /api/sections/{section_id}/grades/import",
    "POST /api/sections/{section_id}/submit",
    "GET /api/grade-change-requests",
    "POST /api/grade-change-requests",
}
TEACHER = ANY_USER | CATALOG | GRADES | {
    "GET /api/advisory",  # own advisory classes only (S-08)
}

USER_ADMIN = {  # registrars: student/teacher accounts; admins: every account
    "GET /api/users",
    "POST /api/users/{user_id}/reset-password",
    "POST /api/users/{user_id}/deactivate",
}

VALIDATION = {
    "GET /api/mechanism/status",
    "GET /api/mechanism/camera/frame",
    "POST /api/mechanism/command",
    "GET /api/scan-stations",
    "POST /api/scan-sessions",
    "GET /api/scan-sessions/{session_id}",
    "POST /api/scan-sessions/{session_id}/end",
    "GET /api/validations",
    "GET /api/validations/stats",
    "GET /api/validations/export",
    "GET /api/validations/{scanned_id}",
    "GET /api/validations/{scanned_id}/pages/{page}",
    "POST /api/validations/{scanned_id}/review",
}

# Intake station operator console (app/api/intake_operator.py): the validation operator
# watches the incoming-document station (status, batch, tray per page, no field values).
INTAKE_OPERATOR = {
    "GET /api/intake-station/stations",
    "GET /api/intake-station/stations/{station_id}/live",
    "GET /api/intake-station/stations/{station_id}/batches",
    "GET /api/intake-station/batches/{batch_id}",
    "POST /api/intake-station/batches/{batch_id}/end",
}

# Incoming-document review queue (S-09): registrars decide; admins may too. Not tied to the
# validation-operator permission, which is about issued documents.
INTAKE_STAFF = {
    "GET /api/intake/documents",
    "GET /api/intake/documents/{intake_id}",
    "GET /api/intake/documents/{intake_id}/image",
    "POST /api/intake/documents/{intake_id}/review",
    # S-06/S-07: reviewed fields, candidate students, link / create a profile / unlink
    "GET /api/intake/documents/{intake_id}/fields",
    "PUT /api/intake/documents/{intake_id}/fields",
    "GET /api/intake/documents/{intake_id}/candidates",
    "POST /api/intake/documents/{intake_id}/link",
    "POST /api/intake/documents/{intake_id}/create-student",
    "POST /api/intake/documents/{intake_id}/unlink",
}

REGISTRAR = ANY_USER | CATALOG | GRADES | USER_ADMIN | INTAKE_STAFF | {
    "GET /api/dashboard/registrar",
    "GET /api/audit-logs",
    # records
    "GET /api/students",
    "POST /api/students",
    "POST /api/students/import",
    "GET /api/students/{student_id}",
    "PATCH /api/students/{student_id}",
    "GET /api/students/{student_id}/records",
    "POST /api/students/{student_id}/account",
    "POST /api/programs",
    "POST /api/subjects",
    "POST /api/terms",
    "POST /api/terms/{term_id}/set-current",
    "POST /api/terms/{term_id}/lock",
    "GET /api/teachers",
    "POST /api/teachers",
    "POST /api/sections",
    "PATCH /api/sections/{section_id}/teacher",
    "GET /api/section-advisers",
    "PUT /api/section-advisers",
    "POST /api/enrollments",
    "POST /api/sections/{section_id}/enroll",
    "POST /api/enrollments/{enrollment_id}/drop",
    "POST /api/grade-change-requests/{request_id}/approve",
    "POST /api/grade-change-requests/{request_id}/reject",
    # templates
    "GET /api/document-types",
    "GET /api/templates",
    "POST /api/templates",
    "GET /api/templates/{template_id}",
    "PATCH /api/templates/{template_id}",
    "POST /api/templates/{template_id}/preview",
    "POST /api/templates/{template_id}/activate",
    # requests and issued documents
    "GET /api/requests",
    "POST /api/requests",
    "GET /api/requests/{request_id}",
    "POST /api/requests/{request_id}/cancel",
    "POST /api/requests/{request_id}/confirm-payment",
    "POST /api/requests/{request_id}/approve",
    "POST /api/requests/{request_id}/verify",
    "POST /api/requests/{request_id}/release",
    "POST /api/requests/{request_id}/reject",
    "POST /api/requests/{request_id}/regenerate",
    "GET /api/documents/{document_id}",
    "POST /api/documents/{document_id}/revoke",
    "POST /api/documents/{document_id}/reopen-download",
}

ADMIN = ANY_USER | CATALOG | USER_ADMIN | INTAKE_STAFF | {
    "GET /api/intake/settings",  # screening thresholds: admin only
    "PUT /api/intake/settings",
    "POST /api/users",  # staff logins only
    "GET /api/dashboard/registrar",  # read-only overview
    "GET /api/audit-logs",
    "GET /api/stations",
    "POST /api/stations",
    "POST /api/stations/{station_id}/deactivate",
}

CALLERS = {
    "maria.santos": STUDENT,
    "teacher": TEACHER,
    "registrar": REGISTRAR | VALIDATION | INTAKE_OPERATOR,
    "registrar-no-validation": REGISTRAR,
    "admin": ADMIN,
}

DENIED = {401, 403}


def routes() -> list[tuple[str, str]]:
    # The OpenAPI schema is the public, complete route list (no route opts out of it;
    # adding include_in_schema=False would hide a route from this sweep).
    found = [(method.upper(), path) for path, ops in app.openapi()["paths"].items()
             for method in ops if path.startswith("/api/")]
    assert len(found) > 50
    return found


def call(client, method: str, path: str):
    url = re.sub(r"\{[^}]+\}", "999999", path)
    return client.request(method, url, json={} if method in {"POST", "PUT", "PATCH"} else None)


def reached_by(client) -> set[str]:
    reached = set()
    # Logout last: it ends the session the sweep is using.
    for method, path in sorted(routes(), key=lambda r: r[1] == "/api/auth/logout"):
        key = f"{method} {path}"
        if key not in PUBLIC and call(client, method, path).status_code not in DENIED:
            reached.add(key)
    return reached


@pytest.fixture(autouse=True)
def cron_secret_configured(monkeypatch):
    """Sweep the cloud configuration: with no secret the cron route is simply off (404)."""
    from pydantic import SecretStr

    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "cron_secret", SecretStr("sweep-secret-0123456789abcdef"))


@pytest.fixture
def demo(db):
    seed(db)
    for user in db.scalars(select(User)):
        user.must_change_password = False
    db.commit()
    return db


def test_every_route_rejects_anonymous_callers(client):
    assert reached_by(client) == set()


@pytest.mark.parametrize("caller", list(CALLERS))
def test_routes_each_role_can_reach_are_pinned(caller, demo, client, login):
    email = caller.removesuffix("-no-validation") + "@school.edu.ph"
    if caller == "registrar-no-validation":
        demo.scalar(select(User).where(User.email == email)).can_validate = False
        demo.commit()
    assert login(email, DEMO_PASSWORD).status_code == 200
    # Station and internal routes are in no caller's set, so a session that reaches one
    # fails here too.
    assert sorted(reached_by(client)) == sorted(CALLERS[caller])


def test_api_responses_carry_security_headers(client):
    for res in (client.get("/api/health"), client.get("/api/auth/me")):
        assert res.headers["X-Content-Type-Options"] == "nosniff"
        assert res.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert res.headers["Referrer-Policy"] == "no-referrer"
        assert res.headers["Cache-Control"] == "no-store"


def test_station_routes_answer_401_without_a_valid_key(client):
    for key in sorted(STATION):
        method, path = key.split(" ", 1)
        assert call(client, method, path).status_code == 401, key
        client.headers["X-Station-Key"] = "not-a-real-key"
        assert call(client, method, path).status_code == 401, key
        del client.headers["X-Station-Key"]


def test_internal_routes_answer_401_without_the_cron_secret(client, demo, login):
    def check():
        for key in sorted(INTERNAL):
            method, path = key.split(" ", 1)
            assert call(client, method, path).status_code == 401, key
            client.headers["Authorization"] = "Bearer not-the-secret"
            assert call(client, method, path).status_code == 401, key
            del client.headers["Authorization"]

    check()
    assert login("admin@school.edu.ph", DEMO_PASSWORD).status_code == 200
    check()  # an admin session is not the scheduler


def test_every_route_is_classified():
    known = (PUBLIC | STATION | INTERNAL | set().union(*CALLERS.values()))
    assert {f"{m} {p}" for m, p in routes()} <= known


@pytest.mark.parametrize(("headers", "allowed"), [
    ({"Sec-Fetch-Site": "same-origin"}, True),
    ({"Sec-Fetch-Site": "same-site"}, False),  # a sibling subdomain: SameSite lets it through
    ({"Sec-Fetch-Site": "cross-site"}, False),
    ({"Origin": "https://testserver"}, True),
    ({"Origin": "https://evil.school.edu.ph"}, False),
    ({}, True),  # not a browser (scan station, scripts)
])
def test_writes_from_other_sites_are_refused(client, headers, allowed):
    res = client.post("/api/auth/login", json={"email": "x@school.edu.ph", "password": "y" * 10},
                      headers=headers)
    assert (res.status_code != 403) is allowed
    assert client.get("/api/health", headers=headers).status_code == 200  # reads unaffected
