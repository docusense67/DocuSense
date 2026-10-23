"""applicant intake, phase 2: reference samples, second OCR pass, per-student advisers

Revision ID: d3f8a6c2e9b4
Revises: b6f2d8e4c1a7
Create Date: 2026-10-23 18:00:00.000000

docs/applicant-intake.md.

- applicant_references: labelled reference samples per issuing school and type (TOR/COE, or
  "other"), read by the OCR worker (same lease columns as a submission) and approved by staff.
- applicant_second_passes: the second OCR pass of an accepted online document, stored apart
  from the first pass.
- student_advisers: one teacher-adviser per student per term. Distinct from section_advisers
  (S-08, the class adviser of a block section), which stays as it is.
- applicant_submissions.uploaded_by: the staff member who created a submission on an
  applicant's behalf.

The new tables are locked down like migration a9d4e2c7b1f5: RLS on, Data API roles revoked
(sequences too), so the Supabase test sees no table without RLS. Enums are varchar without a
CHECK constraint (native_enum=False) and are stored by name.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd3f8a6c2e9b4'
down_revision: Union[str, Sequence[str], None] = 'b6f2d8e4c1a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ("applicant_references", "applicant_second_passes", "student_advisers")
NEW_SEQUENCES = tuple(f"{table}_id_seq" for table in NEW_TABLES)
API_ROLES = ("anon", "authenticated", "service_role")

DOC_TYPES = ('PSA_BIRTH_CERTIFICATE', 'JHS_REPORT_CARD', 'GOOD_MORAL_CERTIFICATE',
             'JHS_COMPLETION_CERTIFICATE', 'TRANSCRIPT_OF_RECORDS', 'CERTIFICATE_OF_ENROLLMENT',
             'UNKNOWN')


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def _now() -> sa.TextClause:
    return sa.text('(CURRENT_TIMESTAMP)')


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
    for sequence in NEW_SEQUENCES:
        op.execute(f"REVOKE ALL ON SEQUENCE {schema}.{_quote(sequence)} FROM {grantees}, PUBLIC")


def upgrade() -> None:
    op.create_table(
        'applicant_references',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('issuing_school', sa.String(length=200), nullable=False),
        sa.Column('school_key', sa.String(length=200), nullable=False),
        sa.Column('doc_type', sa.Enum(*DOC_TYPES, name='intakedoctype', native_enum=False,
                                      length=40), nullable=False),
        sa.Column('label', sa.Enum('KNOWN_GOOD_SAMPLE', 'ALTERED_SAMPLE', 'OTHER',
                                   name='referencelabel', native_enum=False, length=20),
                  nullable=False),
        sa.Column('use', sa.Enum('TRAINING', 'HELD_OUT', name='referenceuse', native_enum=False,
                                 length=20), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('replaces_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.Enum('QUEUED', 'PROCESSING', 'PENDING', 'APPROVED', 'RETIRED',
                                    'FAILED', name='referencestatus', native_enum=False,
                                    length=20), nullable=False),
        sa.Column('status_reason', sa.Text(), nullable=True),
        sa.Column('rules', _json(), nullable=False),
        sa.Column('page_keys', _json(), nullable=False),
        sa.Column('reading', _json(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('lease_token_hash', sa.String(length=64), nullable=True),
        sa.Column('lease_station_id', sa.Integer(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('decided_by', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=_now(),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=_now(),
                  nullable=False),
        sa.ForeignKeyConstraint(['replaces_id'], ['applicant_references.id']),
        sa.ForeignKeyConstraint(['lease_station_id'], ['scan_stations.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_applicant_references_school_key', 'applicant_references',
                    ['school_key'])
    op.create_index('ix_applicant_references_status', 'applicant_references', ['status'])

    op.create_table(
        'applicant_second_passes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('QUEUED', 'PROCESSING', 'DONE', 'FAILED',
                                    name='secondpassstatus', native_enum=False, length=20),
                  nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('lease_token_hash', sa.String(length=64), nullable=True),
        sa.Column('lease_station_id', sa.Integer(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('reading', _json(), nullable=True),
        sa.Column('disagreements', _json(), nullable=True),
        sa.Column('queued_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=_now(),
                  nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['submission_id'], ['applicant_submissions.id']),
        sa.ForeignKeyConstraint(['lease_station_id'], ['scan_stations.id']),
        sa.ForeignKeyConstraint(['queued_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submission_id'),
    )
    op.create_index('ix_applicant_second_passes_status', 'applicant_second_passes', ['status'])

    op.create_table(
        'student_advisers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('term_id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('assigned_by', sa.Integer(), nullable=True),
        sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=_now(),
                  nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['students.id']),
        sa.ForeignKeyConstraint(['term_id'], ['terms.id']),
        sa.ForeignKeyConstraint(['teacher_id'], ['teachers.id']),
        sa.ForeignKeyConstraint(['assigned_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('student_id', 'term_id'),
    )
    op.create_index('ix_student_advisers_student_id', 'student_advisers', ['student_id'])
    op.create_index('ix_student_advisers_teacher_id', 'student_advisers', ['teacher_id'])

    with op.batch_alter_table('applicant_submissions') as batch:
        batch.add_column(sa.Column('uploaded_by', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_applicant_submissions_uploaded_by', 'users',
                                 ['uploaded_by'], ['id'])
    _lock_down(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table('applicant_submissions') as batch:
        batch.drop_constraint('fk_applicant_submissions_uploaded_by', type_='foreignkey')
        batch.drop_column('uploaded_by')
    op.drop_index('ix_student_advisers_teacher_id', table_name='student_advisers')
    op.drop_index('ix_student_advisers_student_id', table_name='student_advisers')
    op.drop_table('student_advisers')
    op.drop_index('ix_applicant_second_passes_status', table_name='applicant_second_passes')
    op.drop_table('applicant_second_passes')
    op.drop_index('ix_applicant_references_status', table_name='applicant_references')
    op.drop_index('ix_applicant_references_school_key', table_name='applicant_references')
    op.drop_table('applicant_references')
