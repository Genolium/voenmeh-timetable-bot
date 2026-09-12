"""Add subscription filter settings and required channels tables

Revision ID: 20260912_sub_filter
Revises: 20260912_add_blocked_fields
Create Date: 2026-09-12 22:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260912_sub_filter"
down_revision: Union[str, tuple[str, ...], None] = "20260912_add_blocked_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "subscription_filter_settings" not in tables:
        op.create_table(
            "subscription_filter_settings",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("is_enabled", sa.Boolean(), server_default="f", nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "required_channels" not in tables:
        op.create_table(
            "required_channels",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("channel_id", sa.String(length=100), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("invite_link", sa.String(length=500), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("channel_id"),
        )
        op.create_index("idx_required_channels_channel_id", "required_channels", ["channel_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_required_channels_channel_id", table_name="required_channels")
    op.drop_table("required_channels")
    op.drop_table("subscription_filter_settings")
