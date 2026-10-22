from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import AuditLog, UserSession
from app.models.enums import Role
from app.timeutil import as_utc
from tests.conftest import PASSWORD


def actions(db) -> list[str]:
    return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_login_sets_httponly_cookie_and_me_works(client, make_user, login, db):
    make_user(Role.REGISTRAR)
    res = login("registrar@school.edu.ph")
    assert res.status_code == 200
    assert "httponly" in res.headers["set-cookie"].lower()

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "registrar"
    assert "login_success" in actions(db)


def test_email_is_case_insensitive(make_user, login):
    make_user(Role.STUDENT, "maria@school.edu.ph")
    assert login("MARIA@School.edu.ph").status_code == 200


def test_unknown_email_and_wrong_password_give_same_error(make_user, login):
    make_user(Role.STUDENT)
    a = login("nobody@school.edu.ph")
    b = login("student@school.edu.ph", "wrong-password-123")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_five_failures(make_user, login, db):
    user = make_user(Role.TEACHER)
    for _ in range(5):
        assert login("teacher@school.edu.ph", "wrong-password-123").status_code == 401
    db.refresh(user)
    assert user.is_locked and user.locked_at is not None
    # Even the correct password is refused once locked, with the same answer as a wrong
    # password, so a locked account does not confirm that the email exists.
    locked = login("teacher@school.edu.ph")
    assert locked.status_code == 401
    assert locked.json() == login("nobody@school.edu.ph").json()
    assert "account_locked" in actions(db)


def test_lockout_ends_after_the_lockout_period(make_user, login, db):
    user = make_user(Role.REGISTRAR)
    for _ in range(5):
        login("registrar@school.edu.ph", "wrong-password-123")
    db.refresh(user)
    user.locked_at = datetime.now(UTC) - timedelta(minutes=16)
    db.commit()
    assert login("registrar@school.edu.ph").status_code == 200
    db.refresh(user)
    assert (user.is_locked, user.locked_at, user.failed_logins) == (False, None, 0)


def test_failed_attempt_after_lockout_expiry_counts_from_zero(make_user, login, db):
    user = make_user(Role.REGISTRAR)
    for _ in range(5):
        login("registrar@school.edu.ph", "wrong-password-123")
    db.refresh(user)
    user.locked_at = datetime.now(UTC) - timedelta(minutes=16)
    db.commit()
    assert login("registrar@school.edu.ph", "wrong-password-123").status_code == 401
    db.refresh(user)
    assert (user.is_locked, user.failed_logins) == (False, 1)


def test_deactivated_account_gets_the_generic_answer(make_user, login):
    make_user(Role.TEACHER, is_active=False)
    res = login("teacher@school.edu.ph")
    assert res.status_code == 401 and "Invalid email or password" in res.json()["detail"]


def test_session_cookie_has_no_fixed_lifetime(make_user, login):
    make_user(Role.STUDENT)
    cookie = login("student@school.edu.ph").headers["set-cookie"].lower()
    assert "max-age" not in cookie and "expires" not in cookie


def test_successful_login_resets_failure_counter(make_user, login, db):
    user = make_user(Role.TEACHER)
    for _ in range(4):
        login("teacher@school.edu.ph", "wrong-password-123")
    assert login("teacher@school.edu.ph").status_code == 200
    db.refresh(user)
    assert user.failed_logins == 0


def test_idle_timeout_expires_session(client, make_user, login, db):
    make_user(Role.REGISTRAR)
    login("registrar@school.edu.ph")
    session = db.scalar(select(UserSession))
    session.last_seen_at = datetime.now(UTC) - timedelta(minutes=31)
    db.commit()

    assert client.get("/api/auth/me").status_code == 401
    assert db.scalar(select(UserSession)) is None


def test_activity_is_saved_at_most_once_a_minute(client, make_user, login, db):
    make_user(Role.REGISTRAR)
    login("registrar@school.edu.ph")
    session = db.scalar(select(UserSession))
    recent = datetime.now(UTC) - timedelta(seconds=20)
    session.last_seen_at = recent
    db.commit()
    assert client.get("/api/auth/me").status_code == 200
    db.refresh(session)
    assert as_utc(session.last_seen_at) == recent  # a read stays a read

    session.last_seen_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    assert client.get("/api/auth/me").status_code == 200
    db.refresh(session)
    assert datetime.now(UTC) - as_utc(session.last_seen_at) < timedelta(seconds=5)


def test_logout_invalidates_session(client, make_user, login):
    make_user(Role.REGISTRAR)
    login("registrar@school.edu.ph")
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_forced_password_change_blocks_other_routes(client, make_user, login):
    make_user(Role.REGISTRAR, must_change=True)
    login("registrar@school.edu.ph")
    assert client.get("/api/users").status_code == 403

    res = client.post("/api/auth/change-password",
                      json={"current_password": PASSWORD, "new_password": "a-new-strong-pass"})
    assert res.status_code == 204
    assert client.get("/api/users").status_code == 200


def test_password_policy_enforces_minimum_length(client, make_user, login):
    make_user(Role.STUDENT, must_change=True)
    login("student@school.edu.ph")
    res = client.post("/api/auth/change-password",
                      json={"current_password": PASSWORD, "new_password": "short"})
    assert res.status_code == 422
