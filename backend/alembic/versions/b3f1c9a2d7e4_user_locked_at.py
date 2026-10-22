"""user locked_at (time-limited lockout)

Revision ID: b3f1c9a2d7e4
Revises: dc496e55fb14
Create Date: 2026-10-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f1c9a2d7e4'
down_revision: Union[str, Sequence[str], None] = 'dc496e55fb14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True))
    # Accounts locked before this change start their 15 minutes now.
    op.execute("UPDATE users SET locked_at = CURRENT_TIMESTAMP WHERE is_locked")


def downgrade() -> None:
    op.drop_column('users', 'locked_at')
