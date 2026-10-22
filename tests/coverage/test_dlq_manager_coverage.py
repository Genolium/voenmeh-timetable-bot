"""Coverage tests for ``core.dlq_manager``."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.dlq_manager import REDIS_DLQ_KEY, DLQManager


@pytest.fixture
def session_factory():
    session = AsyncMock()
    session.add = MagicMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session


@pytest.fixture
def redis():
    client = AsyncMock()
    client.lpush = AsyncMock()
    client.ltrim = AsyncMock()
    return client


async def test_record_failed_message_with_dict_payload(session_factory, redis):
    factory, session = session_factory
    manager = DLQManager(session_factory=factory, redis_client=redis)
    msg_id = await manager.record_failed_message(
        user_id=1,
        payload={"text": "hi"},
        error_message="boom",
        message_type="photo",
        retry_count=3,
    )
    assert msg_id is None
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    redis.lpush.assert_awaited_once()
    redis.ltrim.assert_awaited_with(REDIS_DLQ_KEY, 0, 999)


async def test_record_failed_message_with_str_payload(session_factory):
    factory, session = session_factory
    manager = DLQManager(session_factory=factory)
    await manager.record_failed_message(user_id=1, payload="raw", error_message="boom")
    session.add.assert_called_once()


async def test_record_failed_message_db_error(session_factory, redis):
    factory, session = session_factory
    session.commit.side_effect = Exception("db down")
    manager = DLQManager(session_factory=factory, redis_client=redis)
    msg_id = await manager.record_failed_message(user_id=1, payload="x", error_message="boom")
    assert msg_id is None
    redis.lpush.assert_awaited_once()


async def test_record_failed_message_redis_error(session_factory, redis):
    factory, _ = session_factory
    redis.lpush.side_effect = Exception("redis down")
    manager = DLQManager(session_factory=factory, redis_client=redis)
    await manager.record_failed_message(user_id=1, payload="x", error_message="boom")  # must not raise


async def test_record_failed_message_no_backends():
    manager = DLQManager()
    assert await manager.record_failed_message(user_id=1, payload="x", error_message="boom") is None


async def test_get_failed_messages_no_factory():
    manager = DLQManager()
    assert await manager.get_failed_messages() == []


async def test_get_failed_messages_with_filters(session_factory):
    factory, session = session_factory
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["a", "b"]
    session.execute = AsyncMock(return_value=result)
    manager = DLQManager(session_factory=factory)
    assert await manager.get_failed_messages(status="failed", limit=10) == ["a", "b"]
    assert await manager.get_failed_messages(status="failed", limit=10, user_id=42) == ["a", "b"]


async def test_mark_status_no_factory():
    manager = DLQManager()
    assert await manager.mark_status(1, "resolved") is False


async def test_mark_status_success(session_factory):
    factory, session = session_factory
    manager = DLQManager(session_factory=factory)
    assert await manager.mark_status(1, "resolved") is True
    session.commit.assert_awaited_once()


async def test_mark_status_error(session_factory):
    factory, session = session_factory
    session.execute.side_effect = Exception("db down")
    manager = DLQManager(session_factory=factory)
    assert await manager.mark_status(1, "resolved") is False


async def test_get_stats_no_factory():
    manager = DLQManager()
    stats = await manager.get_stats()
    assert stats == {"failed": 0, "resolved": 0, "discarded": 0, "total": 0}


async def test_get_stats_with_results(session_factory):
    factory, session = session_factory
    result = MagicMock()
    result.all.return_value = [("failed", 3), ("resolved", 2), ("unknown", 9)]
    session.execute = AsyncMock(return_value=result)
    manager = DLQManager(session_factory=factory)
    stats = await manager.get_stats()
    assert stats["failed"] == 3
    assert stats["resolved"] == 2
    assert stats["total"] == 5


async def test_get_stats_error(session_factory):
    factory, session = session_factory
    session.execute.side_effect = Exception("db down")
    manager = DLQManager(session_factory=factory)
    stats = await manager.get_stats()
    assert stats["total"] == 0
