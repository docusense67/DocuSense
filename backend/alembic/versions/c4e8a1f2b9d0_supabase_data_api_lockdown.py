"""lock app tables away from the Supabase Data API

Revision ID: c4e8a1f2b9d0
Revises: b3f1c9a2d7e4
Create Date: 2026-10-09 10:00:00.000000

Supabase publishes every table in `public` through its Data API (PostgREST, GraphQL) to the
`anon` and `authenticated` roles (the browser-side keys) and `service_role`. This app never
uses that API: the browser talks only to our FastAPI backend, which connects with its own
least-privilege login. So, only where those roles exist (a Supabase database):

* row level security is enabled on every app table, with no policy for the API roles,
  so even a grant added by mistake later returns no rows;
* every privilege on the app tables, sequences and our functions is revoked from those
  roles and from PUBLIC, and default privileges stop new tables being granted to them.

The app login gets an explicit policy from `python -m app.manage setup-db-roles`, which runs
after every migration and re-applies this lockdown to tables added later. The table owner
(migrations, backups) is not subject to RLS. Plain Postgres (on-prem) and SQLite: no-op.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4e8a1f2b9d0'
down_revision: Union[str, Sequence[str], None] = 'b3f1c9a2d7e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

API_ROLES = ("anon", "authenticated", "service_role")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _supabase_roles(bind) -> list[str]:
    if bind.dialect.name != "postgresql":
        return []
    present = {name for (name,) in bind.execute(
        sa.text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
        {"names": list(API_ROLES)})}
    # Only a Supabase database has the browser-side roles; plain Postgres is left alone.
    if not present & {"anon", "authenticated"}:
        return []
    return [r for r in API_ROLES if r in present]


def _app_tables(bind) -> list[str]:
    return [name for (name,) in bind.execute(sa.text(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') "
        "AND pg_get_userbyid(c.relowner) = current_user ORDER BY c.relname"))]


def upgrade() -> None:
    bind = op.get_bind()
    roles = _supabase_roles(bind)
    if not roles:
        return
    grantees = ", ".join(_quote(r) for r in roles)
    schema = _quote(bind.execute(sa.text("SELECT current_schema()")).scalar_one())
    for table in _app_tables(bind):
        op.execute(f"ALTER TABLE {schema}.{_quote(table)} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {schema}.{_quote(table)} FROM {grantees}, PUBLIC")
    sequences = [name for (name,) in bind.execute(sa.text(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relkind = 'S' "
        "AND pg_get_userbyid(c.relowner) = current_user"))]
    for sequence in sequences:
        op.execute(f"REVOKE ALL ON SEQUENCE {schema}.{_quote(sequence)} FROM {grantees}, PUBLIC")
    functions = [signature for (signature,) in bind.execute(sa.text(
        "SELECT p.oid::regprocedure::text FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = current_schema() AND pg_get_userbyid(p.proowner) = current_user"))]
    for signature in functions:  # the audit_logs trigger function; triggers still fire
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM {grantees}, PUBLIC")
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                   f"REVOKE ALL ON {kind} FROM {grantees}")


def downgrade() -> None:
    # Turns RLS back off. Privileges stay revoked: nothing in this app needs the API roles.
    bind = op.get_bind()
    if not _supabase_roles(bind):
        return
    schema = _quote(bind.execute(sa.text("SELECT current_schema()")).scalar_one())
    for table in _app_tables(bind):
        op.execute(f"ALTER TABLE {schema}.{_quote(table)} DISABLE ROW LEVEL SECURITY")
