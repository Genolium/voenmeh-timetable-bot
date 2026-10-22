from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from core.db.migrate import assert_schema_current, run_migrations
from core.user_data import UserDataManager


def test_run_migrations_upgrades_to_head():
    with (
        patch("core.db.migrate.command.upgrade") as upgrade,
        patch("core.db.migrate.Config") as config_cls,
    ):
        run_migrations("postgresql+asyncpg://user:pass@db:5432/voenmeh")

    config_cls.assert_called_once_with("alembic.ini")
    upgrade.assert_called_once_with(config_cls.return_value, "head")


@pytest.mark.asyncio
async def test_assert_schema_current_accepts_latest_revision():
    result = MagicMock()
    result.scalar_one_or_none.return_value = "abc123"
    connection = AsyncMock()
    connection.execute.return_value = result
    engine = MagicMock()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def connect():
        yield connection

    engine.connect = connect
    with patch("core.db.migrate.ScriptDirectory.from_config") as script:
        script.return_value.get_current_head.return_value = "abc123"
        await assert_schema_current(engine)


@pytest.mark.asyncio
async def test_assert_schema_current_rejects_old_revision():
    result = MagicMock()
    result.scalar_one_or_none.return_value = "old"
    connection = AsyncMock()
    connection.execute.return_value = result
    engine = MagicMock()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def connect():
        yield connection

    engine.connect = connect
    with patch("core.db.migrate.ScriptDirectory.from_config") as script:
        script.return_value.get_current_head.return_value = "new"
        with pytest.raises(RuntimeError, match="does not match"):
            await assert_schema_current(engine)


@pytest.mark.asyncio
async def test_init_db_fails_fast_for_unmigrated_postgres():
    manager = UserDataManager.__new__(UserDataManager)
    manager.engine = MagicMock()
    manager.engine.url = "postgresql+asyncpg://user:pass@db/voenmeh"
    with patch("core.db.migrate.assert_schema_current", new=AsyncMock()) as check:
        await manager.init_db()
    check.assert_awaited_once_with(manager.engine)


def test_run_migrations_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        run_migrations(None)


@pytest.mark.asyncio
async def test_assert_schema_current_wraps_connection_error():
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("db down")
    with pytest.raises(RuntimeError, match="not initialized"):
        await assert_schema_current(engine)


def test_compose_has_completed_migration_gate():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert "migrate" in services
    assert services["bot"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["worker"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
