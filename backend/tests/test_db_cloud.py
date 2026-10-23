"""Database connection options for the Supabase pooler, and the Data API lockdown migration.

The Postgres test runs against a local server where it may create scratch databases and
roles: ADGVS_TEST_POSTGRES_URL, else ADGVS_DATABASE_URL (CI's service container). It needs a
superuser on localhost and skips otherwise, so it can never touch a real deployment.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app.config import get_settings
from app.db import engine_options, make_engine, normalize_url

BACKEND = Path(__file__).resolve().parents[1]


# ---- engine options -------------------------------------------------------------------------

@pytest.mark.parametrize(("url", "expected"), [
    ("postgres://u:p@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
     "postgresql+psycopg://u:p@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"),
    ("postgresql://u:p@db:5432/adgvs", "postgresql+psycopg://u:p@db:5432/adgvs"),
    ("postgresql+psycopg://u:p@db:5432/adgvs", "postgresql+psycopg://u:p@db:5432/adgvs"),
    ("sqlite://", "sqlite://"),
])
def test_urls_always_use_psycopg3(url, expected):
    assert normalize_url(url) == expected


def test_sqlite_gets_no_postgres_options():
    assert engine_options("sqlite://") == {"pool_pre_ping": True}


def test_postgres_options_suit_a_transaction_pooler(monkeypatch):
    monkeypatch.setattr(get_settings(), "db_pool_size", 3)
    monkeypatch.setattr(get_settings(), "db_max_overflow", 2)
    options = engine_options("postgresql+psycopg://u:p@pooler:6543/postgres?sslmode=require")
    assert options["pool_pre_ping"] is True
    assert options["connect_args"]["prepare_threshold"] is None  # no server-side prepares
    assert (options["pool_size"], options["max_overflow"]) == (3, 2)
    engine = make_engine("postgres://u:p@pooler:6543/postgres?sslmode=require")  # no connect
    assert engine.dialect.driver == "psycopg" and engine.pool.size() == 3
    assert engine.url.query["sslmode"] == "require"


# ---- migrations -----------------------------------------------------------------------------

def run(*args: str, url: str, **env: str) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, "-m", *args], cwd=BACKEND, capture_output=True,
                            text=True, env={**os.environ, "ADGVS_DATABASE_URL": url, **env},
                            timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_migrations_are_a_no_op_lockdown_on_sqlite(tmp_path):
    url = f"sqlite:///{(tmp_path / 'adgvs.db').as_posix()}"
    run("alembic", "upgrade", "head", url=url)
    with create_engine(url).connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == script_head()
        # intake screening thresholds: one row with the contract defaults
        assert conn.execute(text(
            "SELECT id, min_doc_type_confidence, max_indicator_severity, min_layout_score, "
            "min_similarity_score FROM intake_settings")).all() == [(1, 0.75, 0.5, 0.7, 0.6)]
    run("alembic", "downgrade", "base", url=url)


def script_head() -> str:
    """The single head revision (any new migration becomes it; two heads fail here)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    [head] = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_heads()
    return head


def _local_superuser_url() -> str:
    url = os.environ.get("ADGVS_TEST_POSTGRES_URL") or os.environ.get("ADGVS_DATABASE_URL", "")
    if not url.startswith(("postgres", "postgresql")):
        pytest.skip("no local Postgres configured")
    parsed = make_url(normalize_url(url))
    if parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("Postgres lockdown test only runs against a local server")
    try:
        with create_engine(parsed, connect_args={"connect_timeout": 3}).connect() as conn:
            if not conn.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")):
                pytest.skip("needs a superuser to create scratch roles")
            if conn.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname IN "
                                "('anon', 'authenticated', 'service_role')")):
                pytest.skip("Supabase roles already exist on this server")
    except OperationalError:
        pytest.skip("local Postgres not reachable")
    return parsed.render_as_string(hide_password=False)


OWNER, APP = "lockdown_owner", "lockdown_app"
OWNER_PW, APP_PW = "owner-password-0123456789", "app-password-0123456789"


def test_supabase_data_api_lockdown_keeps_the_app_login_working():
    admin_url = _local_superuser_url()
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    base = make_url(admin_url)

    def url(database: str, user: str = OWNER, password: str = OWNER_PW) -> str:
        return base.set(database=database, username=user, password=password).render_as_string(
            hide_password=False)

    def setup_roles(database: str) -> str:
        return run("app.manage", "setup-db-roles", url=url(database), ADGVS_APP_DB_USER=APP,
                   ADGVS_APP_DB_PASSWORD=APP_PW).stdout

    def rls_off(database: str) -> list[str]:
        with create_engine(url(database)).connect() as conn:
            return list(conn.scalars(text(
                "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                "AND relkind = 'r' AND NOT relrowsecurity ORDER BY relname")))

    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE {OWNER} LOGIN CREATEROLE PASSWORD '{OWNER_PW}'"))
        conn.execute(text(f"CREATE DATABASE lockdown_plain OWNER {OWNER}"))
        conn.execute(text(f"CREATE DATABASE lockdown_supa OWNER {OWNER}"))
    try:
        # 1. Plain Postgres (on-prem): the migration changes nothing, the app login works.
        run("alembic", "upgrade", "head", url=url("lockdown_plain"))
        assert "Data API" not in setup_roles("lockdown_plain")
        assert "users" in rls_off("lockdown_plain")
        with create_engine(url("lockdown_plain", APP, APP_PW)).connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM users")) == 0
            assert conn.scalar(text("SELECT count(*) FROM intake_settings")) == 1

        # 2. A Supabase-like database: API roles, and default grants to them on new tables.
        with admin.connect() as conn:
            for role in ("anon", "authenticated", "service_role"):
                conn.execute(text(f"CREATE ROLE {role} NOLOGIN"))
        supa_admin = create_engine(base.set(database="lockdown_supa"), isolation_level="AUTOCOMMIT")
        with supa_admin.connect() as conn:
            for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
                conn.execute(text(f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER} IN SCHEMA public "
                                  f"GRANT ALL ON {kind} TO anon, authenticated, service_role"))
        run("alembic", "upgrade", "head", url=url("lockdown_supa"))
        # Tables added after c4e8a1f2b9d0 (intake_*) are locked by their own migration too.
        assert rls_off("lockdown_supa") == []
        assert "Data API locked out" in setup_roles("lockdown_supa")

        with supa_admin.connect() as conn:
            open_grants = conn.scalar(text(
                "SELECT count(*) FROM pg_class c, unnest(ARRAY['anon', 'authenticated', "
                "'service_role']) AS r(role) WHERE c.relnamespace = 'public'::regnamespace "
                "AND c.relkind IN ('r', 'S') AND (has_table_privilege(r.role, c.oid, "
                "'SELECT,INSERT,UPDATE,DELETE') OR (c.relkind = 'S' AND "
                "has_sequence_privilege(r.role, c.oid, 'USAGE')))"))
            assert open_grants == 0

        # The app login reads and writes through RLS (its policy); grants still apply.
        app_engine = make_engine(url("lockdown_supa", APP, APP_PW))
        with app_engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (email, password_hash, role, is_active, can_validate, "
                "must_change_password, failed_logins, is_locked) VALUES "
                "('rls@school.edu.ph', 'x', 'STUDENT', true, false, false, 0, false)"))
            conn.execute(text("INSERT INTO audit_logs (action) VALUES ('rls_check')"))
            conn.execute(text("INSERT INTO scan_stations (name, api_key_hash, is_active) "
                              "VALUES ('rls', 'h', true)"))
            conn.execute(text(
                "INSERT INTO intake_batches (station_id, total_docs, passed_count, "
                "manual_review_count) SELECT id, 0, 0, 0 FROM scan_stations"))
            conn.execute(text("UPDATE intake_settings SET min_layout_score = 0.7"))
            # cloud motor control (b6f2d8e4c1a7): the relay's queue and station state
            conn.execute(text(
                "INSERT INTO mechanism_commands (station_id, action, params) "
                "SELECT id, 'STOP', '{}' FROM scan_stations"))
            conn.execute(text(
                "INSERT INTO mechanism_station_states (station_id, remote_allowed) "
                "SELECT id, false FROM scan_stations"))
        with app_engine.connect() as conn:
            for _ in range(3):  # repeated statements: nothing prepared server-side
                assert conn.scalar(text("SELECT count(*) FROM users")) == 1
            assert conn.scalar(text("SELECT count(*) FROM audit_logs")) == 1
            assert conn.scalar(text("SELECT count(*) FROM intake_batches")) == 1
            assert conn.scalar(text("SELECT count(*) FROM intake_settings")) == 1
            assert conn.scalar(text("SELECT count(*) FROM mechanism_commands")) == 1
            assert conn.scalar(text("SELECT count(*) FROM mechanism_station_states")) == 1
        for forbidden in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs",
                          "SELECT * FROM alembic_version", "CREATE TABLE app_ddl (id int)"):
            with app_engine.connect() as conn, pytest.raises(ProgrammingError):
                conn.execute(text(forbidden))
        app_engine.dispose()

        with supa_admin.connect() as conn:
            # Even a grant added by mistake shows the API roles nothing: RLS has no policy.
            conn.execute(text("GRANT SELECT ON users, intake_settings TO anon"))
            conn.execute(text("SET ROLE anon"))
            assert conn.scalar(text("SELECT count(*) FROM users")) == 0
            assert conn.scalar(text("SELECT count(*) FROM intake_settings")) == 0
            conn.execute(text("RESET ROLE"))
        with create_engine(url("lockdown_supa")).begin() as conn:
            # The owner (migrations, backups) is not subject to RLS ...
            assert conn.scalar(text("SELECT count(*) FROM users")) == 1
            # ... and a later migration's table is not granted to the API roles.
            conn.execute(text("CREATE TABLE later_table (id int)"))
            assert not conn.scalar(text("SELECT has_table_privilege('anon', 'later_table', "
                                        "'SELECT')"))
        with create_engine(url("lockdown_supa")).connect() as conn, pytest.raises(DBAPIError):
            conn.execute(text("UPDATE audit_logs SET action = 'x'"))  # trigger: append-only
        assert rls_off("lockdown_supa") == ["later_table"]
        setup_roles("lockdown_supa")  # runs after every migration
        assert rls_off("lockdown_supa") == []

        # Back to just before the lockdown migration (later migrations are undone with it).
        run("alembic", "downgrade", "b3f1c9a2d7e4", url=url("lockdown_supa"))
        assert "users" in rls_off("lockdown_supa")
        run("alembic", "upgrade", "head", url=url("lockdown_supa"))
        assert "users" not in rls_off("lockdown_supa")
        supa_admin.dispose()
    finally:
        with admin.connect() as conn:
            for database in ("lockdown_plain", "lockdown_supa"):
                conn.execute(text(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)"))
            for role in (APP, "anon", "authenticated", "service_role", OWNER):
                conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
