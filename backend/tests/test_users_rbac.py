from app.models.enums import Role
from tests.conftest import PASSWORD

NEW_USER = {"email": "new@school.edu.ph", "temporary_password": "temp-password-1"}


def test_student_and_teacher_cannot_manage_users(client, make_user, login):
    for role in (Role.STUDENT, Role.TEACHER):
        make_user(role)
        login(f"{role}@school.edu.ph")
        assert client.get("/api/users").status_code == 403
        client.post("/api/auth/logout")


def test_only_admins_create_logins_through_users_and_only_staff_ones(client, make_user, login):
    make_user(Role.REGISTRAR)
    login("registrar@school.edu.ph")
    assert client.post("/api/users", json={**NEW_USER, "role": "student"}).status_code == 403
    client.post("/api/auth/logout")

    make_user(Role.ADMIN)
    login("admin@school.edu.ph")
    # Student/teacher logins come with their record, never as unlinked accounts.
    for role in ("student", "teacher"):
        res = client.post("/api/users", json={**NEW_USER, "role": role})
        assert res.status_code == 422 and "linked" in res.json()["detail"]
    res = client.post("/api/users", json={**NEW_USER, "role": "admin"})
    assert res.status_code == 201 and res.json()["must_change_password"] is True


def test_registrar_list_hides_staff_accounts(client, make_user, login):
    make_user(Role.REGISTRAR)
    make_user(Role.ADMIN)
    make_user(Role.STUDENT)
    login("registrar@school.edu.ph")
    roles = {u["role"] for u in client.get("/api/users").json()}
    assert roles == {"student"}


def test_admin_can_create_registrar_with_validation_permission(client, make_user, login):
    make_user(Role.ADMIN)
    login("admin@school.edu.ph")
    res = client.post("/api/users",
                      json={**NEW_USER, "role": "registrar", "can_validate": True})
    assert res.status_code == 201
    assert res.json()["can_validate"] is True


def test_validation_permission_only_for_registrars(client, make_user, login):
    make_user(Role.ADMIN)
    login("admin@school.edu.ph")
    res = client.post("/api/users", json={**NEW_USER, "role": "admin", "can_validate": True})
    assert res.status_code == 422


def test_reset_password_unlocks_and_forces_change(client, make_user, login, db):
    make_user(Role.REGISTRAR)
    student = make_user(Role.STUDENT, is_locked=True, failed_logins=5)
    login("registrar@school.edu.ph")

    res = client.post(f"/api/users/{student.id}/reset-password",
                      json={"temporary_password": "temp-password-2", "reason": "Forgot password"})
    assert res.status_code == 204
    db.refresh(student)
    assert not student.is_locked
    assert student.must_change_password


def test_deactivated_user_cannot_log_in(client, make_user, login):
    make_user(Role.REGISTRAR)
    student = make_user(Role.STUDENT)
    login("registrar@school.edu.ph")
    res = client.post(f"/api/users/{student.id}/deactivate", json={"reason": "Graduated"})
    assert res.status_code == 200
    client.post("/api/auth/logout")
    assert login("student@school.edu.ph", PASSWORD).status_code == 401


def test_account_list_shows_expired_lockouts_as_unlocked(client, make_user, login, db):
    from datetime import UTC, datetime, timedelta

    make_user(Role.ADMIN)
    make_user(Role.REGISTRAR, is_locked=True, locked_at=datetime.now(UTC) - timedelta(minutes=20))
    make_user(Role.TEACHER, is_locked=True, locked_at=datetime.now(UTC))
    login("admin@school.edu.ph")
    locked = {u["role"]: u["is_locked"] for u in client.get("/api/users").json()}
    assert locked == {"admin": False, "registrar": False, "teacher": True}
