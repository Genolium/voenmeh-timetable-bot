"""Coverage tests for ``core.subscription_filter``.

Uses an in-memory SQLite session factory and a mocked Redis / Bot.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db.models import Base, RequiredChannel
from core.subscription_filter import (
    KEY_CHANNELS_LIST,
    KEY_FILTER_ENABLED,
    SubscriptionFilterManager,
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def redis():
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock()
    client.delete = AsyncMock()
    return client


def member(status):
    m = MagicMock()
    m.status = status
    return m


# ---------------------------------------------------------------------------
# is_filter_enabled / set_filter_enabled
# ---------------------------------------------------------------------------


async def test_is_filter_enabled_cached_bytes(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.return_value = b"1"
    assert await manager.is_filter_enabled() is True
    redis.get.return_value = b"0"
    assert await manager.is_filter_enabled() is False


async def test_is_filter_enabled_cached_str(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.return_value = "1"
    assert await manager.is_filter_enabled() is True


async def test_is_filter_enabled_redis_error_falls_back(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.side_effect = Exception("redis down")
    assert await manager.is_filter_enabled() is False


async def test_is_filter_enabled_redis_set_error(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.set.side_effect = Exception("redis down")
    assert await manager.is_filter_enabled() is False


async def test_set_filter_enabled_creates_and_updates(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    await manager.set_filter_enabled(True)
    assert await manager.is_filter_enabled() is True
    await manager.set_filter_enabled(False)
    assert await manager.is_filter_enabled() is False


async def test_set_filter_enabled_redis_error(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.set.side_effect = Exception("redis down")
    await manager.set_filter_enabled(True)  # must not raise


async def test_set_filter_enabled_without_redis(session_factory):
    manager = SubscriptionFilterManager(session_factory, None)
    await manager.set_filter_enabled(True)
    assert await manager.is_filter_enabled() is True


# ---------------------------------------------------------------------------
# get_required_channels
# ---------------------------------------------------------------------------


async def test_get_required_channels_from_cache(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    payload = [{"id": 1, "channel_id": "@c", "title": "t", "invite_link": "l"}]
    redis.get.return_value = json.dumps(payload).encode()
    assert await manager.get_required_channels() == payload


async def test_get_required_channels_cache_error(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.side_effect = Exception("redis down")
    assert await manager.get_required_channels() == []


async def test_get_required_channels_default_invite_link(session_factory, redis):
    async with session_factory() as session:
        session.add(RequiredChannel(channel_id="@noinvite", title="T", invite_link=None))
        await session.commit()
    manager = SubscriptionFilterManager(session_factory, redis)
    channels = await manager.get_required_channels()
    assert channels[0]["invite_link"] == "https://t.me/noinvite"
    redis.set.assert_awaited()


async def test_get_required_channels_cache_set_error(session_factory, redis):
    redis.set.side_effect = Exception("redis down")
    manager = SubscriptionFilterManager(session_factory, redis)
    async with session_factory() as session:
        session.add(RequiredChannel(channel_id="@x", title="T", invite_link="l"))
        await session.commit()
    channels = await manager.get_required_channels()
    assert len(channels) == 1


async def test_get_required_channels_without_redis(session_factory):
    manager = SubscriptionFilterManager(session_factory, None)
    assert await manager.get_required_channels() == []


# ---------------------------------------------------------------------------
# add / remove channels
# ---------------------------------------------------------------------------


async def test_add_required_channel_new_and_update(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    created = await manager.add_required_channel("@c", "Title", "https://t.me/c")
    assert created["channel_id"] == "@c"

    updated = await manager.add_required_channel("@c", "New title")
    assert updated["title"] == "New title"
    assert updated["invite_link"] == "https://t.me/c"

    updated2 = await manager.add_required_channel("@c", "Newest", "https://t.me/c2")
    assert updated2["invite_link"] == "https://t.me/c2"


async def test_remove_required_channel_variants(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    await manager.add_required_channel("@a", "A")
    await manager.add_required_channel("@b", "B")

    assert await manager.remove_required_channel("@a") is True
    assert await manager.remove_required_channel("@missing") is False

    # numeric removal by primary key
    channels = await manager.get_required_channels()
    pk = str(channels[0]["id"])
    assert await manager.remove_required_channel(pk) is True


async def test_remove_required_channel_digit_channel_id(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    async with session_factory() as session:
        session.add(RequiredChannel(channel_id="12345", title="T"))
        await session.commit()
    assert await manager.remove_required_channel("12345") is True


# ---------------------------------------------------------------------------
# cache invalidation
# ---------------------------------------------------------------------------


async def test_invalidate_user_cache(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    await manager.invalidate_user_cache(1)
    redis.delete.assert_awaited_with("timetable:sub_filter:user:1")

    redis.delete.side_effect = Exception("boom")
    await manager.invalidate_user_cache(2)  # must not raise


async def test_invalidate_all_caches(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    await manager.invalidate_all_caches()
    redis.delete.assert_awaited_with(KEY_CHANNELS_LIST)

    redis.delete.side_effect = Exception("boom")
    await manager.invalidate_all_caches()  # must not raise


async def test_invalidation_without_redis(session_factory):
    manager = SubscriptionFilterManager(session_factory, None)
    await manager.invalidate_user_cache(1)
    await manager.invalidate_all_caches()


# ---------------------------------------------------------------------------
# check_user_subscription
# ---------------------------------------------------------------------------


async def test_check_user_subscription_cache_hit(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.return_value = b"1"
    bot = AsyncMock()
    is_sub, missing = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is True
    assert missing == []
    bot.get_chat_member.assert_not_awaited()


async def test_check_user_subscription_cache_hit_str(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.return_value = "1"
    bot = AsyncMock()
    is_sub, _ = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is True


async def test_check_user_subscription_cache_error(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.side_effect = Exception("redis down")
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("member")
    is_sub, _ = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is True


async def test_check_user_subscription_bypass_cache(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.get.return_value = b"1"
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("member")
    is_sub, _ = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}], bypass_cache=True)
    assert is_sub is True
    bot.get_chat_member.assert_awaited()


async def test_check_user_subscription_channels_none_empty(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    bot = AsyncMock()
    is_sub, missing = await manager.check_user_subscription(bot, 1)
    assert is_sub is True
    assert missing == []


async def test_check_user_subscription_channels_from_db(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    await manager.add_required_channel("@c", "T")
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("member")
    is_sub, _ = await manager.check_user_subscription(bot, 1)
    assert is_sub is True
    redis.set.assert_any_await("timetable:sub_filter:user:1", "1", ex=600)


async def test_check_user_subscription_numeric_and_at_channels(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("member")
    channels = [{"channel_id": "-100123456"}, {"channel_id": "@at"}, {"channel_id": "555"}]
    is_sub, _ = await manager.check_user_subscription(bot, 1, channels=channels)
    assert is_sub is True
    assert bot.get_chat_member.await_args_list[0].kwargs["chat_id"] == -100123456
    assert bot.get_chat_member.await_args_list[2].kwargs["chat_id"] == 555


async def test_check_user_subscription_cache_set_error(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.set.side_effect = Exception("redis down")
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("member")
    is_sub, _ = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is True


async def test_check_user_subscription_missing_channel(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("left")
    is_sub, missing = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is False
    assert len(missing) == 1
    redis.delete.assert_awaited_with("timetable:sub_filter:user:1")


async def test_check_user_subscription_delete_cache_error(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    redis.delete.side_effect = Exception("boom")
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("left")
    is_sub, _ = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is False


async def test_check_user_subscription_forbidden(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    bot = AsyncMock()
    bot.get_chat_member.side_effect = TelegramForbiddenError(method=MagicMock(), message="Forbidden")
    is_sub, missing = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is False
    assert len(missing) == 1


@pytest.mark.parametrize("message", ["User not found", "participant_id_invalid", "Chat not found"])
async def test_check_user_subscription_bad_request_missing(session_factory, redis, message):
    manager = SubscriptionFilterManager(session_factory, redis)
    bot = AsyncMock()
    bot.get_chat_member.side_effect = TelegramBadRequest(method=MagicMock(), message=message)
    is_sub, missing = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is False
    assert len(missing) == 1


async def test_check_user_subscription_bad_request_other(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    bot = AsyncMock()
    bot.get_chat_member.side_effect = TelegramBadRequest(method=MagicMock(), message="Something else")
    is_sub, missing = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is True
    assert missing == []


async def test_check_user_subscription_unexpected_error(session_factory, redis):
    manager = SubscriptionFilterManager(session_factory, redis)
    bot = AsyncMock()
    bot.get_chat_member.side_effect = RuntimeError("boom")
    is_sub, missing = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is True
    assert missing == []


async def test_check_user_subscription_without_redis(session_factory):
    manager = SubscriptionFilterManager(session_factory, None)
    bot = AsyncMock()
    bot.get_chat_member.return_value = member("member")
    is_sub, _ = await manager.check_user_subscription(bot, 1, channels=[{"channel_id": "@c"}])
    assert is_sub is True
