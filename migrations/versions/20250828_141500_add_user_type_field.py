"""Add user_type field to users table

Revision ID: 7f8e9a12b3c4
Revises: perf_indexes_001
Create Date: 2025-08-28 14:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7f8e9a12b3c4'
down_revision = 'add_events_20250820'
branch_labels = None
depends_on = None


def upgrade():
    """Add user_type field to users table."""
    # Add user_type column with default value 'student'
    op.add_column('users', sa.Column('user_type', sa.String(), nullable=False, server_default='student'))
    
    # Add index for user_type
    op.create_index('idx_user_type', 'users', ['user_type'])


def downgrade():
    """Remove user_type field from users table."""
    op.drop_index('idx_user_type', 'users')
    op.drop_column('users', 'user_type')
