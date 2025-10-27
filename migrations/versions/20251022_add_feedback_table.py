"""add feedback table

Revision ID: add_feedback_20251022
Revises: set_db_timezone_20250901
Create Date: 2025-10-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_feedback_20251022"
down_revision: Union[str, None] = "set_db_timezone_20250901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("user_full_name", sa.String(length=255), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=True),
        sa.Column("message_type", sa.String(length=50), server_default=sa.text("'text'"), nullable=False),
        sa.Column("file_id", sa.String(length=512), nullable=True),
        sa.Column("is_answered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("admin_response", sa.Text(), nullable=True),
        sa.Column("admin_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=True),
        sa.Column("answered_at", sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_feedback_user_id", "feedback", ["user_id"])
    op.create_index("idx_feedback_is_answered", "feedback", ["is_answered"])
    op.create_index("idx_feedback_created_at", "feedback", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_feedback_created_at", table_name="feedback")
    op.drop_index("idx_feedback_is_answered", table_name="feedback")
    op.drop_index("idx_feedback_user_id", table_name="feedback")
    op.drop_table("feedback")
