"""Add user_lesson_rules and group_chats tables.

Revision ID: 20261124_rules_groups
Revises: 20261120_perf_indexes
Create Date: 2026-11-24 23:35:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261124_rules_groups"
down_revision: Union[str, None] = "20261120_perf_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_lesson_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("rule_type", sa.String(length=20), nullable=False),
        sa.Column("target_group", sa.String(length=100), nullable=False),
        sa.Column("week_key", sa.String(length=10), nullable=True),
        sa.Column("day_name", sa.String(length=32), nullable=True),
        sa.Column("start_time", sa.String(length=5), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_lesson_rules_user", "user_lesson_rules", ["user_id", "target_group"], unique=False)
    op.create_index("idx_user_lesson_rules_type", "user_lesson_rules", ["user_id", "rule_type"], unique=False)

    op.create_table(
        "group_chats",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("target_group", sa.String(length=100), nullable=True),
        sa.Column("language", sa.String(length=5), server_default="ru", nullable=False),
        sa.Column("morning_summary", sa.Boolean(), server_default="t", nullable=False),
        sa.Column("evening_notify", sa.Boolean(), server_default="t", nullable=False),
        sa.Column("lesson_reminders", sa.Boolean(), server_default="f", nullable=False),
        sa.Column("schedule_diff_notify", sa.Boolean(), server_default="t", nullable=False),
        sa.Column("morning_time", sa.String(length=5), server_default="08:00", nullable=False),
        sa.Column("evening_time", sa.String(length=5), server_default="20:00", nullable=False),
        sa.Column("reminder_time_minutes", sa.Integer(), server_default="20", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="t", nullable=False),
        sa.Column("added_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("chat_id"),
    )
    op.create_index("idx_group_chats_active", "group_chats", ["is_active", "target_group"], unique=False)
    op.create_index(
        "idx_group_chats_notify",
        "group_chats",
        ["is_active", "morning_summary", "evening_notify", "lesson_reminders"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_group_chats_notify", table_name="group_chats")
    op.drop_index("idx_group_chats_active", table_name="group_chats")
    op.drop_table("group_chats")

    op.drop_index("idx_user_lesson_rules_type", table_name="user_lesson_rules")
    op.drop_index("idx_user_lesson_rules_user", table_name="user_lesson_rules")
    op.drop_table("user_lesson_rules")
