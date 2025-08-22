"""add events table

Revision ID: add_events_20250820
Revises: merge_perf_and_semester_20250816
Create Date: 2025-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_events_20250820'
down_revision: Union[str, None] = 'merge_perf_and_semester_20250816'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('start_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('end_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('link', sa.String(length=512), nullable=True),
        sa.Column('image_file_id', sa.String(length=512), nullable=True),
        sa.Column('is_published', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_events_start', 'events', ['start_at'])
    op.create_index('idx_events_published', 'events', ['is_published'])


def downgrade() -> None:
    op.drop_index('idx_events_published', table_name='events')
    op.drop_index('idx_events_start', table_name='events')
    op.drop_table('events')


