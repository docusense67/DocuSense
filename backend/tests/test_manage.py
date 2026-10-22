"""Server administration commands used at go-live (app.manage)."""

from contextlib import nullcontext
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import jobs, manage
from app.models import AuditLog, DocumentTemplate, User, UserSession
from app.models.enums import Role
from app.timeutil import utcnow


@pytest.fixture
def console(db, monkeypatch):
    monkeypatch.setattr(manage, "SessionLocal", lambda: nullcontext(db))
    return db


def test_create_admin_prints_one_time_password_that_must_be_changed(console, client, capsys):
    assert manage.main(["create-admin", "IT.Admin@school.edu.ph"]) == 0
    password = capsys.readouterr().out.strip().splitlines()[-1]

    admin = console.scalar(select(User).where(User.email == "it.admin@school.edu.ph"))
    assert admin.role == Role.ADMIN and admin.must_change_password
    log = console.scalar(select(AuditLog).where(AuditLog.action == "user_created"))
    assert log.entity_id == str(admin.id) and log.user_id is None

    res = client.post("/api/auth/login",
                      json={"email": "it.admin@school.edu.ph", "password": password})
    assert res.status_code == 200 and res.json()["must_change_password"] is True


def test_create_admin_refuses_existing_email(console, capsys):
    assert manage.main(["create-admin", "admin@school.edu.ph"]) == 0
    assert manage.main(["create-admin", "admin@school.edu.ph"]) == 1
    assert len(console.scalars(select(User)).all()) == 1


def test_install_templates_is_idempotent(console):
    assert manage.main(["install-templates"]) == 0
    assert manage.main(["install-templates"]) == 0
    templates = console.scalars(select(DocumentTemplate)).all()
    assert sorted(t.document_type for t in templates) == ["COE", "TOR"]


def test_setup_db_roles_rejects_weak_or_unsafe_settings(monkeypatch):
    monkeypatch.setenv("ADGVS_APP_DB_USER", 'app"; DROP TABLE users; --')
    monkeypatch.setenv("ADGVS_APP_DB_PASSWORD", "x" * 32)
    assert manage.main(["setup-db-roles"]) == 2
    monkeypatch.setenv("ADGVS_APP_DB_USER", "adgvs_app")
    monkeypatch.setenv("ADGVS_APP_DB_PASSWORD", "short")
    assert manage.main(["setup-db-roles"]) == 2


def test_unknown_command_prints_usage(capsys):
    assert manage.main(["drop-everything"]) == 2
    assert "create-admin" in capsys.readouterr().out


def test_daily_job_purges_only_idle_sessions(db, make_user):
    user = make_user(Role.STUDENT)
    now = utcnow()
    db.add_all([
        UserSession(token_hash="a" * 64, user_id=user.id, created_at=now, last_seen_at=now),
        UserSession(token_hash="b" * 64, user_id=user.id, created_at=now,
                    last_seen_at=now - timedelta(hours=2)),
    ])
    db.commit()
    assert jobs.purge_sessions(db) == "1 idle login session(s) removed"
    assert [s.token_hash[0] for s in db.scalars(select(UserSession))] == ["a"]


def test_setup_storage_creates_a_private_bucket(monkeypatch, capsys):
    from pydantic import SecretStr

    from app import storage as storage_module
    from app.config import get_settings
    from tests.test_storage import KEY, URL, FakeBucket, supabase

    assert manage.main(["setup-storage"]) == 0  # local backend: nothing to create
    assert "no bucket" in capsys.readouterr().out

    bucket = FakeBucket(exists=False)
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_url", URL)
    monkeypatch.setattr(settings, "supabase_service_key", SecretStr(KEY))
    monkeypatch.setattr(storage_module, "_supabase", lambda *args: supabase(bucket))
    assert manage.main(["setup-storage"]) == 0
    assert bucket.exists and bucket.public is False
    assert "created" in capsys.readouterr().out
    bucket.public = True  # someone flipped it in the dashboard
    assert manage.main(["setup-storage"]) == 1
    assert "public" in capsys.readouterr().err
