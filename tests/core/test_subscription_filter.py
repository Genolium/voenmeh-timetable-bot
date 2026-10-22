from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db.models import Base
from core.subscription_filter import SubscriptionFilterManager


@pytest.fixture
async def async_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.delete = AsyncMock()
    return r


class TestSubscriptionFilterManager:
    @pytest.mark.asyncio
    async def test_filter_enabled_default_and_set(self, async_session_factory, mock_redis):
        mgr = SubscriptionFilterManager(async_session_factory, mock_redis)

        # По умолчанию выключен
        assert await mgr.is_filter_enabled() is False

        # Включаем
        await mgr.set_filter_enabled(True)
        assert await mgr.is_filter_enabled() is True
        mock_redis.set.assert_called()

        # Выключаем
        await mgr.set_filter_enabled(False)
        assert await mgr.is_filter_enabled() is False

    @pytest.mark.asyncio
    async def test_redis_cache_used_for_is_enabled(self, async_session_factory, mock_redis):
        mgr = SubscriptionFilterManager(async_session_factory, mock_redis)

        mock_redis.get.return_value = b"1"
        assert await mgr.is_filter_enabled() is True

        mock_redis.get.return_value = b"0"
        assert await mgr.is_filter_enabled() is False

    @pytest.mark.asyncio
    async def test_add_get_remove_channel(self, async_session_factory, mock_redis):
        mgr = SubscriptionFilterManager(async_session_factory, mock_redis)

        # Список пуст
        channels = await mgr.get_required_channels()
        assert channels == []

        # Добавляем канал
        ch = await mgr.add_required_channel(
            channel_id="@test_channel",
            title="Тестовый канал",
            invite_link="https://t.me/test_channel",
        )
        assert ch["channel_id"] == "@test_channel"
        assert ch["title"] == "Тестовый канал"

        # Получаем список
        channels = await mgr.get_required_channels()
        assert len(channels) == 1
        assert channels[0]["channel_id"] == "@test_channel"

        # Обновляем канал
        await mgr.add_required_channel(
            channel_id="@test_channel",
            title="Обновленное название",
        )
        channels = await mgr.get_required_channels()
        assert len(channels) == 1
        assert channels[0]["title"] == "Обновленное название"

        # Удаляем канал
        removed = await mgr.remove_required_channel("@test_channel")
        assert removed is True
        channels = await mgr.get_required_channels()
        assert channels == []

    @pytest.mark.asyncio
    async def test_check_user_subscription_all_subscribed(self, async_session_factory, mock_redis):
        mgr = SubscriptionFilterManager(async_session_factory, mock_redis)
        mock_bot = AsyncMock()

        member_mock = MagicMock()
        member_mock.status = "member"
        mock_bot.get_chat_member.return_value = member_mock

        channels = [
            {"channel_id": "@chan1", "title": "Chan 1", "invite_link": "https://t.me/chan1"},
            {"channel_id": "-100123456", "title": "Chan 2", "invite_link": "https://t.me/chan2"},
        ]

        is_sub, missing = await mgr.check_user_subscription(mock_bot, 12345, channels=channels)
        assert is_sub is True
        assert missing == []
        mock_redis.set.assert_called_with("timetable:sub_filter:user:12345", "1", ex=600)

    @pytest.mark.asyncio
    async def test_check_user_subscription_missing_channel(self, async_session_factory, mock_redis):
        mgr = SubscriptionFilterManager(async_session_factory, mock_redis)
        mock_bot = AsyncMock()

        member1 = MagicMock()
        member1.status = "member"
        member2 = MagicMock()
        member2.status = "left"  # Не подписан

        mock_bot.get_chat_member.side_effect = [member1, member2]

        channels = [
            {"channel_id": "@chan1", "title": "Chan 1", "invite_link": "https://t.me/chan1"},
            {"channel_id": "@chan2", "title": "Chan 2", "invite_link": "https://t.me/chan2"},
        ]

        is_sub, missing = await mgr.check_user_subscription(mock_bot, 12345, channels=channels)
        assert is_sub is False
        assert len(missing) == 1
        assert missing[0]["channel_id"] == "@chan2"

    @pytest.mark.asyncio
    async def test_check_user_subscription_telegram_error(self, async_session_factory, mock_redis):
        mgr = SubscriptionFilterManager(async_session_factory, mock_redis)
        mock_bot = AsyncMock()

        mock_bot.get_chat_member.side_effect = TelegramBadRequest(
            method=MagicMock(),
            message="User not found",
        )

        channels = [{"channel_id": "@chan1", "title": "Chan 1", "invite_link": "https://t.me/chan1"}]

        is_sub, missing = await mgr.check_user_subscription(mock_bot, 12345, channels=channels)
        assert is_sub is False
        assert len(missing) == 1

    @pytest.mark.asyncio
    async def test_check_user_subscription_cached(self, async_session_factory, mock_redis):
        mgr = SubscriptionFilterManager(async_session_factory, mock_redis)
        mock_bot = AsyncMock()
        mock_redis.get.return_value = b"1"

        is_sub, missing = await mgr.check_user_subscription(
            mock_bot, 12345, channels=[{"channel_id": "@chan"}], bypass_cache=False
        )
        assert is_sub is True
        mock_bot.get_chat_member.assert_not_called()
