"""applicant intake: online TOR/COE uploads read by the station's OCR worker

Revision ID: a9d4e2c7b1f5
Revises: f3c14d582906
Create Date: 2026-10-23 10:00:00.000000

docs/applicant-intake.md. An applicant uploads the pages of a document from another school; the
OCR worker reads them and the backend creates an intake document (source "online") that goes
through the existing staff review, field review and student link.

- applicant_submissions, applicant_pages (new tables, locked down like migration e7a3c5d91b42:
  RLS on, Data API roles revoked, so the Supabase test sees no table without RLS).
- intake_documents: source ("station" for every existing row) and submission_id.
- Role gains APPLICANT; IntakeDocType gains TRANSCRIPT_OF_RECORDS and
  CERTIFICATE_OF_ENROLLMENT. Enums are varchar without a CHECK constraint (native_enum=False),
  and the existing columns are long enough, so no DDL is needed for them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9d4e2c7b1f5'
down_revision: Union[str, Sequence[str], None] = 'f3c14d582906'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ("applicant_submissions", "applicant_pages")
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
        'applicant_submissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('applicant_id', sa.Integer(), nullable=False),
        sa.Column('declared_type', sa.Enum(
            'PSA_BIRTH_CERTIFICATE', 'JHS_REPORT_CARD', 'GOOD_MORAL_CERTIFICATE',
            'JHS_COMPLETION_CERTIFICATE', 'TRANSCRIPT_OF_RECORDS', 'CERTIFICATE_OF_ENROLLMENT',
            'UNKNOWN', name='intakedoctype', native_enum=False, length=40), nullable=False),
        sa.Column('issuing_school', sa.String(length=200), nullable=False),
        sa.Column('status', sa.Enum(
            'DRAFT', 'QUEUED', 'PROCESSING', 'SCREENED', 'FAILED', 'WITHDRAWN',
            name='applicantsubmissionstatus', native_enum=False, length=20), nullable=False),
        sa.Column('consent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('lease_token_hash', sa.String(length=64), nullable=True),
        sa.Column('lease_station_id', sa.Integer(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('intake_document_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                  nullable=False),
        sa.ForeignKeyConstraint(['applicant_id'], ['users.id']),
        sa.ForeignKeyConstraint(['intake_document_id'], ['intake_documents.id']),
        sa.ForeignKeyConstraint(['lease_station_id'], ['scan_stations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_applicant_submissions_applicant_id', 'applicant_submissions',
                    ['applicant_id'])
    op.create_index('ix_applicant_submissions_status', 'applicant_submissions', ['status'])
    op.create_table(
        'applicant_pages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                  nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['applicant_submissions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submission_id', 'sequence'),
    )
    op.create_index('ix_applicant_pages_submission_id', 'applicant_pages', ['submission_id'])
    with op.batch_alter_table('intake_documents') as batch:
        batch.add_column(sa.Column(
            'source', sa.Enum('STATION', 'ONLINE', name='intakesource', native_enum=False,
                              length=20), server_default='STATION', nullable=False))
        batch.add_column(sa.Column('submission_id', sa.Integer(), nullable=True))
        batch.create_index('ix_intake_documents_source', ['source'])
        batch.create_foreign_key('fk_intake_documents_submission_id', 'applicant_submissions',
                                 ['submission_id'], ['id'])
    _lock_down(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table('intake_documents') as batch:
        batch.drop_constraint('fk_intake_documents_submission_id', type_='foreignkey')
        batch.drop_index('ix_intake_documents_source')
        batch.drop_column('submission_id')
        batch.drop_column('source')
    op.drop_index('ix_applicant_pages_submission_id', table_name='applicant_pages')
    op.drop_table('applicant_pages')
    op.drop_index('ix_applicant_submissions_status', table_name='applicant_submissions')
    op.drop_index('ix_applicant_submissions_applicant_id', table_name='applicant_submissions')
    op.drop_table('applicant_submissions')
