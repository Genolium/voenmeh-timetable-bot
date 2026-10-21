"""Alembic entrypoint used by the Docker migration service."""

from __future__ import annotations

import logging
import os

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


def run_migrations(database_url: str | None = None) -> None:
    """Apply all migrations to ``head`` using the configured database URL."""
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required to run migrations")
    os.environ["DATABASE_URL"] = url
    logger.info("Applying Alembic migrations to head")
    command.upgrade(Config("alembic.ini"), "head")


async def assert_schema_current(engine: AsyncEngine) -> None:
    """Fail fast when the production schema is missing or not at the latest revision."""
    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar_one_or_none()
    except Exception as exc:
        raise RuntimeError("Database schema is not initialized; run migrations first") from exc

    if current != expected:
        raise RuntimeError(f"Database schema revision {current!r} does not match expected {expected!r}")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    run_migrations()
