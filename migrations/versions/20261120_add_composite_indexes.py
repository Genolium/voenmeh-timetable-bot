"""Add composite indexes for active notification broadcasts and DLQ polling.

Revision ID: 20261120_perf_indexes
Revises: 20261024_schedule_diff
Create Date: 2026-11-20 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261120_perf_indexes"
down_revision: Union[str, None] = "20261024_schedule_diff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "idx_user_notifications_active",
        "users",
        ["is_blocked", "evening_notify", "morning_summary", "lesson_reminders"],
        unique=False,
    )
    op.create_index(
        "idx_failed_msg_status_created",
        "failed_messages",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_failed_msg_status_created", table_name="failed_messages")
    op.drop_index("idx_user_notifications_active", table_name="users")
