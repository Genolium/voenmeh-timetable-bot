from unittest.mock import AsyncMock, MagicMock

import pytest

from bot import scheduler as scheduler_module
from bot.scheduler import backup_current_schedule, monitor_schedule_changes


@pytest.mark.asyncio
async def test_backup_creates_snapshot(mocker):
    redis = AsyncMock()

    async def fake_get(key):
        if key.endswith("schedule_cache"):
            return b'{"sample":1}'
        return b"hash"

    redis.get.side_effect = fake_get
    redis.set = AsyncMock()

    await backup_current_schedule(redis)
    assert any(k[0][0].startswith("timetable:backup:") for k in redis.set.call_args_list)


@pytest.mark.asyncio
async def test_monitor_handles_304_path(mocker):
    udm = AsyncMock()
    redis = AsyncMock()
    redis.get.return_value = b""

    # Mock the lock method to return an async context manager
    mock_lock = AsyncMock()
    mock_lock.__aenter__ = AsyncMock()
    mock_lock.__aexit__ = AsyncMock()
    redis.lock = MagicMock(return_value=mock_lock)

    mock_bot = AsyncMock()
    mocker.patch("bot.scheduler.fetch_and_parse_all_schedules", AsyncMock(return_value=None))
    await monitor_schedule_changes(udm, redis, mock_bot)
