"""add_user_theme_field

Revision ID: d607420aa776
Revises: merge_perf_indexes_and_semester
Create Date: 2025-01-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd607420aa776'
down_revision: Union[str, None] = 'merge_perf_and_semester_20250816'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляем поле theme для пользовательских тем оформления."""
    # Добавляем поле theme с дефолтным значением 'standard'
    op.add_column('users', sa.Column('theme', sa.String(), server_default='standard', nullable=False))

    # Добавляем индекс для поиска по теме
    op.create_index('idx_user_theme', 'users', ['theme'])


def downgrade() -> None:
    """Удаляем поле theme и индекс."""
    op.drop_index('idx_user_theme', table_name='users')
    op.drop_column('users', 'theme')