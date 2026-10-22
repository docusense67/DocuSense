"""incoming-document screening: intake batches, documents, settings

Revision ID: e7a3c5d91b42
Revises: c4e8a1f2b9d0
Create Date: 2026-10-12 14:00:00.000000

Part I (docs/intake/screening-contract.md). On a Supabase database the three new tables get the
same Data API lockdown as migration c4e8a1f2b9d0 (RLS on, API roles revoked) right away, so
they are never open between this migration and the `setup-db-roles` run that follows it and
adds the app login's policy. Plain Postgres and SQLite: tables only.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e7a3c5d91b42'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1f2b9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ("intake_batches", "intake_documents", "intake_settings")
API_ROLES = ("anon", "authenticated", "service_role")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lock_down(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    present = {name for (name,) in bind.execute(
        sa.text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
        {"names": list(API_ROLES)})}
    if not present & {"anon", "authenticated"}:
        return
    grantees = ", ".join(_quote(r) for r in API_ROLES if r in present)
    schema = _quote(bind.execute(sa.text("SELECT current_schema()")).scalar_one())
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {schema}.{_quote(table)} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {schema}.{_quote(table)} FROM {grantees}, PUBLIC")
        op.execute(f"REVOKE ALL ON SEQUENCE {schema}.{_quote(table + '_id_seq')} "
                   f"FROM {grantees}, PUBLIC")


def upgrade() -> None:
    op.create_table(
        'intake_batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('station_id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_docs', sa.Integer(), nullable=False),
        sa.Column('passed_count', sa.Integer(), nullable=False),
        sa.Column('manual_review_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['station_id'], ['scan_stations.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_intake_batches_station_id'), 'intake_batches', ['station_id'], unique=False)
    op.create_table(
        'intake_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=True),
        sa.Column('station_id', sa.Integer(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=True),
        sa.Column('doc_type', sa.Enum('PSA_BIRTH_CERTIFICATE', 'JHS_REPORT_CARD', 'GOOD_MORAL_CERTIFICATE', 'JHS_COMPLETION_CERTIFICATE', 'UNKNOWN', name='intakedoctype', native_enum=False, length=40), nullable=False),
        sa.Column('doc_type_confidence', sa.Float(), nullable=True),
        sa.Column('screening_result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('decision', sa.Enum('PASSED_SCREENING', 'MANUAL_REVIEW', name='intakedecision', native_enum=False, length=20), nullable=False),
        sa.Column('reasons', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('assigned_tray', sa.Integer(), nullable=False),
        sa.Column('routed_tray', sa.Integer(), nullable=True),
        sa.Column('routed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('routing_jam', sa.Boolean(), nullable=False),
        sa.Column('status', sa.Enum('SCREENED', 'ACCEPTED', 'REJECTED', 'RESUBMISSION_REQUESTED', name='intakestatus', native_enum=False, length=30), nullable=False),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_reason', sa.Text(), nullable=True),
        sa.Column('image_key', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['intake_batches.id'], ),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['station_id'], ['scan_stations.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_intake_documents_batch_id'), 'intake_documents', ['batch_id'], unique=False)
    op.create_index(op.f('ix_intake_documents_decision'), 'intake_documents', ['decision'], unique=False)
    op.create_index(op.f('ix_intake_documents_doc_type'), 'intake_documents', ['doc_type'], unique=False)
    op.create_index(op.f('ix_intake_documents_station_id'), 'intake_documents', ['station_id'], unique=False)
    op.create_index(op.f('ix_intake_documents_status'), 'intake_documents', ['status'], unique=False)
    settings = op.create_table(
        'intake_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('min_doc_type_confidence', sa.Float(), nullable=False),
        sa.Column('max_indicator_severity', sa.Float(), nullable=False),
        sa.Column('min_layout_score', sa.Float(), nullable=False),
        sa.Column('min_similarity_score', sa.Float(), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    # The single settings row, with the contract defaults.
    op.bulk_insert(settings, [{"id": 1, "min_doc_type_confidence": 0.75,
                               "max_indicator_severity": 0.5, "min_layout_score": 0.70,
                               "min_similarity_score": 0.60}])
    _lock_down(op.get_bind())


def downgrade() -> None:
    op.drop_table('intake_settings')
    op.drop_index(op.f('ix_intake_documents_status'), table_name='intake_documents')
    op.drop_index(op.f('ix_intake_documents_station_id'), table_name='intake_documents')
    op.drop_index(op.f('ix_intake_documents_doc_type'), table_name='intake_documents')
    op.drop_index(op.f('ix_intake_documents_decision'), table_name='intake_documents')
    op.drop_index(op.f('ix_intake_documents_batch_id'), table_name='intake_documents')
    op.drop_table('intake_documents')
    op.drop_index(op.f('ix_intake_batches_station_id'), table_name='intake_batches')
    op.drop_table('intake_batches')
