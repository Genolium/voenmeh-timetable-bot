"""Add schedule_diff_notify toggle for students.

Revision ID: 20261024_schedule_diff
Revises: 20261021_reminder_20
Create Date: 2026-10-24 16:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_schedule_diff"
down_revision: Union[str, None] = "20261021_reminder_20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "schedule_diff_notify",
            sa.Boolean(),
            server_default="t",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "schedule_diff_notify")
