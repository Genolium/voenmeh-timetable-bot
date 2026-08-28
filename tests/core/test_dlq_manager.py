from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dlq_manager import DLQManager, REDIS_DLQ_KEY


@pytest.fixture
def mock_session_factory():
    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    session_factory.return_value.__aexit__.return_value = None
    return session_factory, session


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.lpush = AsyncMock()
    r.ltrim = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_record_failed_message(mock_session_factory, mock_redis):
    session_factory, session = mock_session_factory
    manager = DLQManager(session_factory=session_factory, redis_client=mock_redis)

    await manager.record_failed_message(
        user_id=123456789,
        payload="Test message",
        error_message="Telegram API down",
        message_type="text",
        retry_count=5,
    )

    session.add.assert_called_once()
    session.commit.assert_called_once()
    mock_redis.lpush.assert_called_once()
    mock_redis.ltrim.assert_called_once_with(REDIS_DLQ_KEY, 0, 999)


@pytest.mark.asyncio
async def test_mark_status(mock_session_factory):
    session_factory, session = mock_session_factory
    manager = DLQManager(session_factory=session_factory)

    result = await manager.mark_status(message_id=1, new_status="resolved")
    assert result is True
    session.execute.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_stats_empty():
    manager = DLQManager(session_factory=None)
    stats = await manager.get_stats()
    assert stats == {"failed": 0, "resolved": 0, "discarded": 0, "total": 0}
