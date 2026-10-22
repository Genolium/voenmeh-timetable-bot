from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.subscription_filter import is_user_subscribed


@pytest.mark.asyncio
async def test_db_filter_has_priority_over_legacy_channel():
    bot = AsyncMock()
    session_factory = MagicMock()
    with patch("core.subscription_filter.SubscriptionFilterManager") as manager_cls:
        manager = manager_cls.return_value
        manager.is_filter_enabled = AsyncMock(return_value=True)
        manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@db"}])
        manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@db"}]))

        result = await is_user_subscribed(1, bot, session_factory, None, fallback_channel="@legacy")

    assert result is False
    manager.check_user_subscription.assert_awaited_once()
    bot.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_channel_is_used_when_filter_is_disabled():
    bot = AsyncMock()
    bot.get_chat_member.return_value = MagicMock(status="member")
    with patch("core.subscription_filter.SubscriptionFilterManager") as manager_cls:
        manager = manager_cls.return_value
        manager.is_filter_enabled = AsyncMock(return_value=False)

        assert await is_user_subscribed(1, bot, MagicMock(), None, fallback_channel="@legacy") is True

    bot.get_chat_member.assert_awaited_once_with("@legacy", 1)


@pytest.mark.asyncio
async def test_legacy_channel_error_is_fail_closed():
    bot = AsyncMock()
    bot.get_chat_member.side_effect = RuntimeError("telegram down")
    assert await is_user_subscribed(1, bot, None, None, fallback_channel="@legacy") is False
