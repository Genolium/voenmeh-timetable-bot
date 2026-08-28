"""Add schedule_change_logs and failed_messages tables

Revision ID: 20260829_audit_and_dlq
Revises: b344ca4372f8
Create Date: 2026-08-29 00:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260829_audit_and_dlq"
down_revision: Union[str, tuple[str, ...], None] = "b344ca4372f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. schedule_change_logs
    op.create_table(
        "schedule_change_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("target_name", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=20), server_default="group", nullable=False),
        sa.Column("schedule_date", sa.Date(), nullable=False),
        sa.Column("change_type", sa.String(length=50), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("lesson_time", sa.String(length=50), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_schedule_change_target", "schedule_change_logs", ["target_name", "target_type"])
    op.create_index("idx_schedule_change_date", "schedule_change_logs", ["schedule_date"])
    op.create_index("idx_schedule_change_created", "schedule_change_logs", ["created_at"])

    # 2. failed_messages (DLQ)
    op.create_table(
        "failed_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_type", sa.String(length=50), server_default="text", nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="failed", nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("idx_failed_msg_user", "failed_messages", ["user_id"])
    op.create_index("idx_failed_msg_status", "failed_messages", ["status"])
    op.create_index("idx_failed_msg_created", "failed_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_failed_msg_created", table_name="failed_messages")
    op.drop_index("idx_failed_msg_status", table_name="failed_messages")
    op.drop_index("idx_failed_msg_user", table_name="failed_messages")
    op.drop_table("failed_messages")

    op.drop_index("idx_schedule_change_created", table_name="schedule_change_logs")
    op.drop_index("idx_schedule_change_date", table_name="schedule_change_logs")
    op.drop_index("idx_schedule_change_target", table_name="schedule_change_logs")
    op.drop_table("schedule_change_logs")
