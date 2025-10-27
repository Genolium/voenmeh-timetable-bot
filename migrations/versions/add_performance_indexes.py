"""Add performance indexes to users table

Revision ID: perf_indexes_001
Revises: 3f6d2d644caf
Create Date: 2025-01-15 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "perf_indexes_001"
# Привязываем к последней миграции, чтобы не было разветвления голов
down_revision = "add_semester_settings_table"
branch_labels = None
depends_on = None


def upgrade():
    """Add performance indexes to users table."""
    # Add indexes for frequently queried columns
    op.create_index("idx_user_group", "users", ["group"])
    op.create_index("idx_user_last_active", "users", ["last_active_date"])
    op.create_index("idx_user_registration", "users", ["registration_date"])
    op.create_index("idx_user_notifications", "users", ["evening_notify", "morning_summary", "lesson_reminders"])


def downgrade():
    """Remove performance indexes from users table."""
    op.drop_index("idx_user_notifications", "users")
    op.drop_index("idx_user_registration", "users")
    op.drop_index("idx_user_last_active", "users")
    op.drop_index("idx_user_group", "users")
