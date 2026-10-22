"""intake documents -> student profiles: reviewed fields, student link, new student columns

Revision ID: d5a8c3e1f2b7
Revises: fb5d28bdec60
Create Date: 2026-10-12 18:00:00.000000

S-06/S-07 (docs/intake/screening-contract.md, "Student data"): staff review the fields the
station read and link the document to a student, or create the student from them.

- students: lrn (unique), birth_place, mother_name, father_name (all nullable).
- intake_documents: reviewed_fields (JSON), fields_reviewed_by/at, student_id (FK, indexed),
  linked_by/at.
- IntakeStatus gains DATA_REVIEWED and LINKED. Enums are varchar without a CHECK constraint
  (native_enum=False), so no DDL is needed for them.

No new tables, so no Supabase lockdown step (RLS stays on for both tables). Batch mode keeps
the foreign keys working on SQLite (table copy); on Postgres it emits plain ALTER TABLEs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd5a8c3e1f2b7'
down_revision: Union[str, Sequence[str], None] = 'fb5d28bdec60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    with op.batch_alter_table('students') as batch:
        batch.add_column(sa.Column('lrn', sa.String(length=12), nullable=True))
        batch.add_column(sa.Column('birth_place', sa.String(length=200), nullable=True))
        batch.add_column(sa.Column('mother_name', sa.String(length=200), nullable=True))
        batch.add_column(sa.Column('father_name', sa.String(length=200), nullable=True))
        batch.create_index('ix_students_lrn', ['lrn'], unique=True)

    with op.batch_alter_table('intake_documents') as batch:
        batch.add_column(sa.Column('reviewed_fields', JSON, nullable=True))
        batch.add_column(sa.Column('fields_reviewed_by', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('fields_reviewed_at', sa.DateTime(timezone=True),
                                   nullable=True))
        batch.add_column(sa.Column('student_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('linked_by', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('linked_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key('intake_documents_fields_reviewed_by_fkey', 'users',
                                 ['fields_reviewed_by'], ['id'])
        batch.create_foreign_key('intake_documents_student_id_fkey', 'students',
                                 ['student_id'], ['id'])
        batch.create_foreign_key('intake_documents_linked_by_fkey', 'users',
                                 ['linked_by'], ['id'])
        batch.create_index('ix_intake_documents_student_id', ['student_id'], unique=False)


def downgrade() -> None:
    # Linked/data-reviewed documents fall back to "accepted": the states no longer exist.
    op.execute("UPDATE intake_documents SET status = 'ACCEPTED' "
               "WHERE status IN ('DATA_REVIEWED', 'LINKED')")
    with op.batch_alter_table('intake_documents') as batch:
        batch.drop_index('ix_intake_documents_student_id')
        batch.drop_constraint('intake_documents_linked_by_fkey', type_='foreignkey')
        batch.drop_constraint('intake_documents_student_id_fkey', type_='foreignkey')
        batch.drop_constraint('intake_documents_fields_reviewed_by_fkey', type_='foreignkey')
        for column in ('linked_at', 'linked_by', 'student_id', 'fields_reviewed_at',
                       'fields_reviewed_by', 'reviewed_fields'):
            batch.drop_column(column)

    with op.batch_alter_table('students') as batch:
        batch.drop_index('ix_students_lrn')
        for column in ('father_name', 'mother_name', 'birth_place', 'lrn'):
            batch.drop_column(column)
