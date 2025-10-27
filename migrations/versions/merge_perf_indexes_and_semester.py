"""Merge heads: perf_indexes_001 and add_semester_settings_table

Revision ID: merge_perf_and_semester_20250816
Revises: perf_indexes_001, add_semester_settings_table
Create Date: 2025-08-16 13:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "merge_perf_and_semester_20250816"
down_revision: Union[str, tuple[str, ...]] = (
    "perf_indexes_001",
    "add_semester_settings_table",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge-only migration; no schema changes required.
    pass


def downgrade() -> None:
    # Merge-only migration; no schema changes to revert.
    pass
