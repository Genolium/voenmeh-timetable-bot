"""Add is_blocked and blocked_date to users table

Revision ID: 20260912_add_blocked_fields
Revises: 20260829_audit_and_dlq
Create Date: 2026-09-12 22:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260912_add_blocked_fields"
down_revision: Union[str, tuple[str, ...], None] = "20260829_audit_and_dlq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_blocked", sa.Boolean(), server_default="f", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("blocked_date", sa.TIMESTAMP(), nullable=True),
    )
    op.create_index("idx_user_blocked", "users", ["is_blocked"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_user_blocked", table_name="users")
    op.drop_column("users", "blocked_date")
    op.drop_column("users", "is_blocked")
