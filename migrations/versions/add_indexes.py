"""Add indexes to users table

Revision ID: add_indexes_fix
Revises: a1b2c3d4e5f6
Create Date: 2025-08-14 16:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = 'add_indexes_fix'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

def upgrade():
    op.create_index('ix_user_group', 'users', ['group'], unique=False)
    op.create_index('ix_user_last_active', 'users', ['last_active_date'], unique=False)

def downgrade():
    op.drop_index('ix_user_last_active', table_name='users')
    op.drop_index('ix_user_group', table_name='users')
