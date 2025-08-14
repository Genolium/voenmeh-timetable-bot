"""Add indexes to users table

Revision ID: new_id
Revises: previous_id
Create Date: 2023-...

"""

from alembic import op
import sqlalchemy as sa

revision = 'new_id'
down_revision = 'previous_id'
branch_labels = None
depends_on = None

def upgrade():
    op.create_index('ix_user_group', 'users', ['group'], unique=False)
    op.create_index('ix_user_last_active', 'users', ['last_active_date'], unique=False)

def downgrade():
    op.drop_index('ix_user_last_active', table_name='users')
    op.drop_index('ix_user_group', table_name='users')
