"""Merge heads: add_feedback and add_user_theme

Revision ID: b344ca4372f8
Revises: add_feedback_20251022, d607420aa776
Create Date: 2025-10-26 21:07:10.328700

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b344ca4372f8"
down_revision: Union[str, tuple[str, ...]] = ("add_feedback_20251022", "d607420aa776")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge-only migration; no schema changes required.
    pass


def downgrade() -> None:
    # Merge-only migration; no schema changes to revert.
    pass
