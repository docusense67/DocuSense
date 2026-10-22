"""merge intake screening and approve-first heads

Revision ID: fb5d28bdec60
Revises: e7a3c5d91b42, e7b2c4d9a1f3
Create Date: 2026-10-12 13:40:50.236742

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb5d28bdec60'
down_revision: Union[str, Sequence[str], None] = ('e7a3c5d91b42', 'e7b2c4d9a1f3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
