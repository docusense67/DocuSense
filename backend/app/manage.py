"""Server administration commands (installation guide: docs/admin-installation-guide.md).

    python -m app.manage create-admin EMAIL      first admin account; prints a one-time password
    python -m app.manage install-templates       stand-in TOR/COE templates (skips existing types)
    python -m app.manage setup-db-roles          least-privilege DB login for the API (Postgres)
    python -m app.manage setup-storage           private Supabase Storage bucket for files
    python -m app.manage seed-demo [--api URL] [--password-file PATH] [--reset-passwords]
                                                 SAMPLE demo data (app/demo.py), idempotent
    python -m app.manage purge-demo [--dry-run] [--yes] [--include-station NAME]
                                                 remove exactly the SAMPLE demo data

setup-db-roles runs as the database owner after every migration. It creates or updates the
login named by ADGVS_APP_DB_USER (password ADGVS_APP_DB_PASSWORD) with data access only: no
DDL, and insert-only on audit_logs (Section 18).

On Supabase (the `anon`/`authenticated` roles exist) it also keeps the Data API locked out:
row level security on every app table, no privileges for the API roles, and one policy per
table that lets only the app login through (its grants above still apply on top).
"""

import os
import re
import secrets
import sys

from sqlalchemy import select

from app import demo
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import User
from app.models.enums import Role
from app.security import hash_password
from app.seed import install_default_templates
from app.services import audit
from app.storage import StorageError, SupabaseStorage, get_storage


def create_admin(email: str) -> int:
    email = email.strip().lower()
    password = secrets.token_urlsafe(12)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            print(f"{email} already exists; nothing changed.", file=sys.stderr)
            return 1
        user = User(email=email, password_hash=hash_password(password), role=Role.ADMIN,
                    must_change_password=True)
        db.add(user)
        db.flush()
        audit.record(db, "user_created", entity_type="user", entity_id=user.id,
                     new_value={"email": email, "role": Role.ADMIN, "via": "server console"})
        db.commit()
    print(f"Admin {email} created. One-time password (must be changed at first login):")
    print(password)
    return 0


def install_templates() -> int:
    with SessionLocal() as db:
        installed = install_default_templates(db, created_by=None)
        db.commit()
    print(f"Installed: {', '.join(installed)}" if installed else "Templates already present.")
    return 0


def setup_db_roles() -> int:
    from psycopg import sql

    name = os.environ.get("ADGVS_APP_DB_USER", "adgvs_app")
    password = os.environ.get("ADGVS_APP_DB_PASSWORD", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) or len(password) < 16:
        print("Set ADGVS_APP_DB_USER (lowercase identifier) and ADGVS_APP_DB_PASSWORD "
              "(16+ characters).", file=sys.stderr)
        return 2
    if engine.dialect.name != "postgresql":
        print("setup-db-roles only applies to PostgreSQL.", file=sys.stderr)
        return 2

    role = sql.Identifier(name)
    with engine.begin() as conn:
        raw = conn.connection.driver_connection
        exists = raw.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
        if exists:
            # Only the password: on Supabase the owner is not a superuser, and Postgres refuses
            # even a no-op NOSUPERUSER from a non-superuser. Attributes are checked below.
            raw.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}")
                        .format(role, sql.Literal(password)))
        else:
            raw.execute(sql.SQL("CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                "NOBYPASSRLS PASSWORD {}").format(role, sql.Literal(password)))
        powers = raw.execute("SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls "
                             "FROM pg_roles WHERE rolname = %s", (name,)).fetchone()[0]
        if powers:
            print(f"{name!r} has SUPERUSER, CREATEDB, CREATEROLE or BYPASSRLS; remove them "
                  "(as a superuser) before the API uses this login.", file=sys.stderr)
            return 1
        database = raw.execute("SELECT current_database()").fetchone()[0]
        for statement in (
            "GRANT CONNECT ON DATABASE {db} TO {role}",
            "GRANT USAGE ON SCHEMA public TO {role}",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
            # Section 17/18: the app may append to the audit trail, never change it.
            "REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM {role}",
            "REVOKE ALL ON alembic_version FROM {role}",
        ):
            raw.execute(sql.SQL(statement).format(db=sql.Identifier(database), role=role))
        locked = lock_down_data_api(raw, name)
    print(f"Database login {name!r} ready: data access only, audit log insert-only.")
    if locked:
        print(f"Supabase Data API locked out of {locked} table(s); RLS lets only {name!r} in.")
    return 0


API_ROLES = ("anon", "authenticated", "service_role")
# Tables the app login has no grants on get no policy either.
NO_APP_POLICY = {"alembic_version"}


def lock_down_data_api(raw, app_role: str) -> int:
    """Supabase only (no-op on plain Postgres): RLS on, API roles revoked, app-login policy.

    Repeats migration c4e8a1f2b9d0 for tables added since, so a new table is never left open
    to the Data API. Why policies and not BYPASSRLS for the app login: a policy names exactly
    these tables and this login, and granting BYPASSRLS needs privileges the Supabase owner
    role may not have. Returns the number of tables locked."""
    from psycopg import sql

    present = {r for (r,) in raw.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                                         (list(API_ROLES),)).fetchall()}
    if not present & {"anon", "authenticated"}:
        return 0
    grantees = sql.SQL(", ").join(sql.Identifier(r) for r in API_ROLES if r in present)
    owned = ("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
             "WHERE n.nspname = current_schema() AND c.relkind IN ({kinds}) "
             "AND pg_get_userbyid(c.relowner) = current_user ORDER BY c.relname")
    tables = [t for (t,) in raw.execute(owned.format(kinds="'r', 'p'")).fetchall()]
    policy = sql.Identifier(f"{app_role}_access")
    for table in tables:
        target = sql.Identifier(table)
        raw.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(target))
        raw.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}, PUBLIC").format(target, grantees))
        raw.execute(sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(policy, target))
        if table not in NO_APP_POLICY:
            raw.execute(sql.SQL("CREATE POLICY {} ON {} FOR ALL TO {} USING (true) "
                                "WITH CHECK (true)").format(policy, target,
                                                            sql.Identifier(app_role)))
    for (sequence,) in raw.execute(owned.format(kinds="'S'")).fetchall():
        raw.execute(sql.SQL("REVOKE ALL ON SEQUENCE {} FROM {}, PUBLIC")
                    .format(sql.Identifier(sequence), grantees))
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        raw.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} REVOKE ALL ON " + kind
                            + " FROM {}").format(
            sql.Identifier(raw.execute("SELECT current_schema()").fetchone()[0]), grantees))
    return len(tables)


def setup_storage() -> int:
    """Create the private bucket for ADGVS_STORAGE_BACKEND=supabase (idempotent)."""
    if get_settings().storage_backend != "supabase":
        print("ADGVS_STORAGE_BACKEND is local: files go under ADGVS_STORAGE_DIR, no bucket.")
        return 0
    storage = get_storage()
    assert isinstance(storage, SupabaseStorage)
    try:
        outcome = storage.ensure_private_bucket()
    except StorageError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Private bucket {storage.bucket!r} {outcome}.")
    return 0


def main(argv: list[str]) -> int:
    match argv:
        case ["create-admin", email]:
            return create_admin(email)
        case ["install-templates"]:
            return install_templates()
        case ["setup-db-roles"]:
            return setup_db_roles()
        case ["setup-storage"]:
            return setup_storage()
        case ["seed-demo", *options]:
            return demo.seed_main(options, SessionLocal)
        case ["purge-demo", *options]:
            return demo.purge_main(options, SessionLocal)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
