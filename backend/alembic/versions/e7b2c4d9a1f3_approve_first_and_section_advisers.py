"""approve-first request workflow (S-11) and class advisers (S-08)

Revision ID: e7b2c4d9a1f3
Revises: c4e8a1f2b9d0
Create Date: 2026-10-12 10:00:00.000000

S-11: the registrar now approves the REQUEST before records are retrieved and the document is
generated, then verifies the generated document before release. RequestStatus (stored by
member NAME, varchar(20), no CHECK constraint) loses PROCESSING and FOR_APPROVAL and gains
FOR_VERIFICATION and VERIFIED; APPROVED changes meaning. In-flight requests are mapped:

    old            new               why
    APPROVED    -> VERIFIED          document already approved, waiting for manual release
    FOR_APPROVAL-> FOR_VERIFICATION  document generated, waiting for the registrar's check
    PROCESSING  -> REQUESTED         generation blocked and never approved by the registrar:
                                     back to Pending (processing note cleared); generation
                                     runs again when the registrar approves

Each moved request gets an audit entry (`request_status_migrated`). Downgrade maps back
(APPROVED -> PROCESSING before VERIFIED -> APPROVED, so the two meanings never mix).

S-08: `section_advisers`, one class adviser per block section (term + section code).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e7b2c4d9a1f3'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1f2b9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE = [("APPROVED", "VERIFIED"), ("FOR_APPROVAL", "FOR_VERIFICATION"),
           ("PROCESSING", "REQUESTED")]
DOWNGRADE = [("APPROVED", "PROCESSING"), ("VERIFIED", "APPROVED"),
             ("FOR_VERIFICATION", "FOR_APPROVAL")]

requests = sa.table("document_requests", sa.column("id", sa.Integer),
                    sa.column("reference_no", sa.String), sa.column("status", sa.String),
                    sa.column("processing_note", sa.Text))
json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
audit_logs = sa.table("audit_logs", sa.column("action", sa.String),
                      sa.column("entity_type", sa.String), sa.column("entity_id", sa.String),
                      sa.column("old_value", json), sa.column("new_value", json))


def _remap(pairs: list[tuple[str, str]], note: str) -> None:
    bind = op.get_bind()
    entries = []
    for old, new in pairs:
        rows = bind.execute(sa.select(requests.c.id, requests.c.reference_no)
                            .where(requests.c.status == old)).all()
        if not rows:
            continue
        values: dict = {"status": new}
        if old == "PROCESSING" and new == "REQUESTED":
            values["processing_note"] = None
        bind.execute(sa.update(requests).where(requests.c.id.in_([r.id for r in rows]))
                     .values(**values))
        entries += [{"action": "request_status_migrated", "entity_type": "document_request",
                     "entity_id": str(r.id), "old_value": {"status": old.lower()},
                     "new_value": {"reference_no": r.reference_no, "status": new.lower(),
                                   "reason": note}} for r in rows]
    if entries:
        op.bulk_insert(audit_logs, entries)


def _lock_from_data_api(table: str) -> None:
    """Supabase only, as migration c4e8a1f2b9d0 does for the tables before it: RLS on (no
    policy for the API roles) and no privileges for them. setup-db-roles then adds the app
    login's policy. Plain Postgres and SQLite: no-op."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    present = [name for (name,) in bind.execute(
        sa.text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
        {"names": ["anon", "authenticated", "service_role"]})]
    if not {"anon", "authenticated"} & set(present):
        return
    roles = ", ".join(f'"{r}"' for r in present)
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON TABLE "{table}" FROM PUBLIC, {roles}')
    op.execute(f'REVOKE ALL ON SEQUENCE "{table}_id_seq" FROM PUBLIC, {roles}')


def upgrade() -> None:
    op.create_table(
        'section_advisers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('term_id', sa.Integer(), nullable=False),
        sa.Column('section_code', sa.String(length=20), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('assigned_by', sa.Integer(), nullable=True),
        sa.Column('assigned_at', sa.DateTime(timezone=True),
                  server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id']),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id']),
        sa.ForeignKeyConstraint(['term_id'], ['terms.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('term_id', 'section_code'),
    )
    op.create_index(op.f('ix_section_advisers_teacher_id'), 'section_advisers', ['teacher_id'],
                    unique=False)
    _lock_from_data_api('section_advisers')
    # Each status is moved once; no upgrade target is also a source, so order is free here.
    _remap(UPGRADE, "Approve-first workflow (S-11)")


def downgrade() -> None:
    _remap(DOWNGRADE, "Approve-first workflow (S-11) rolled back")
    op.drop_index(op.f('ix_section_advisers_teacher_id'), table_name='section_advisers')
    op.drop_table('section_advisers')
