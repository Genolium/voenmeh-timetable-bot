"""add language column

Revision ID: 8a2512e1862a
Revises: 'b344ca4372f8'
Create Date: 2026-01-04 17:09:26.768816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a2512e1862a'
down_revision: Union[str, None] = 'b344ca4372f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('language', sa.String(), server_default='ru', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'language')
