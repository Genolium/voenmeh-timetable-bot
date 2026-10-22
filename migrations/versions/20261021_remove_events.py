"""remove events feature

Revision ID: 20261021_remove_events
Revises: 20260912_sub_filter
Create Date: 2026-10-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_remove_events"
down_revision: Union[str, None] = "20260912_sub_filter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS events")


def downgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("end_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("link", sa.String(length=512), nullable=True),
        sa.Column("image_file_id", sa.String(length=512), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_events_start", "events", ["start_at"])
    op.create_index("idx_events_published", "events", ["is_published"])
