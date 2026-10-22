"""reminder default 20 minutes

Revision ID: 20261021_reminder_20
Revises: 20261021_remove_events
Create Date: 2026-10-21 00:10:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "20261021_reminder_20"
down_revision: Union[str, None] = "20261021_remove_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ранее значение 60 фактически означало 20 из-за приведения в коде.
    op.execute("UPDATE users SET reminder_time_minutes = 20 WHERE reminder_time_minutes = 60")
    op.execute("ALTER TABLE users ALTER COLUMN reminder_time_minutes SET DEFAULT 20")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN reminder_time_minutes SET DEFAULT 60")
