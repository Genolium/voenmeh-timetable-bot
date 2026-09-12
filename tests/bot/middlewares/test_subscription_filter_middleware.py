from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message, Update, User

from bot.middlewares.subscription_filter_middleware import (
    SUB_CHECK_CALLBACK,
    SubscriptionFilterMiddleware,
)


@pytest.fixture
def mock_session_factory():
    return MagicMock()


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.delete = AsyncMock()
    return r


class TestSubscriptionFilterMiddleware:
    @pytest.mark.asyncio
    async def test_bypass_admin(self, mock_session_factory, mock_redis):
        """Администраторы бота всегда пропускаются без проверки."""
        mw = SubscriptionFilterMiddleware(mock_session_factory, mock_redis)
        handler = AsyncMock(return_value="handler_result")

        update = MagicMock(spec=Update)
        message = MagicMock(spec=Message)
        message.from_user = User(id=123456789, is_bot=False, first_name="Admin")
        message.chat = MagicMock(id=123456789)
        update.message = message
        update.callback_query = None

        with patch("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [123456789]):
            res = await mw(handler, update, {})
            assert res == "handler_result"
            handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_filter_disabled_passes_through(self, mock_session_factory, mock_redis):
        """Если фильтр выключен, все запросы проходят."""
        mw = SubscriptionFilterMiddleware(mock_session_factory, mock_redis)
        mw.manager.is_filter_enabled = AsyncMock(return_value=False)
        handler = AsyncMock(return_value="ok")

        update = MagicMock(spec=Update)
        message = MagicMock(spec=Message)
        message.from_user = User(id=99999, is_bot=False, first_name="User")
        message.chat = MagicMock(id=99999)
        update.message = message
        update.callback_query = None

        with patch("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", []):
            res = await mw(handler, update, {})
            assert res == "ok"
            handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_user_not_subscribed_blocked_message(self, mock_session_factory, mock_redis):
        """Неподписанный пользователь блокируется и получает сообщение со ссылками."""
        mw = SubscriptionFilterMiddleware(mock_session_factory, mock_redis)
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(
            return_value=[
                {"channel_id": "@ch1", "title": "Канал 1", "invite_link": "https://t.me/ch1"}
            ]
        )
        mw.manager.check_user_subscription = AsyncMock(
            return_value=(False, [{"channel_id": "@ch1", "title": "Канал 1", "invite_link": "https://t.me/ch1"}])
        )

        handler = AsyncMock()

        update = MagicMock(spec=Update)
        message = MagicMock(spec=Message)
        message.from_user = User(id=99999, is_bot=False, first_name="User")
        message.chat = MagicMock(id=99999)
        message.answer = AsyncMock()
        update.message = message
        update.callback_query = None

        mock_bot = AsyncMock()

        with patch("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", []):
            res = await mw(handler, update, {"bot": mock_bot})
            assert res is None
            handler.assert_not_called()
            message.answer.assert_called_once()
            call_kwargs = message.answer.call_args[1]
            assert "Обязательная подписка" in call_kwargs.get("text", "")
            assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_sub_filter_check_callback_success(self, mock_session_factory, mock_redis):
        """Проверка подписки по кнопке 'Проверить подписку' (успешно)."""
        mw = SubscriptionFilterMiddleware(mock_session_factory, mock_redis)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@ch1"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(True, []))

        handler = AsyncMock()

        update = MagicMock(spec=Update)
        cb = MagicMock(spec=CallbackQuery)
        cb.data = SUB_CHECK_CALLBACK
        cb.from_user = User(id=99999, is_bot=False, first_name="User")
        cb.message = MagicMock(spec=Message)
        cb.message.chat = MagicMock(id=99999)
        cb.message.delete = AsyncMock()
        cb.answer = AsyncMock()
        update.message = None
        update.callback_query = cb

        mock_bot = AsyncMock()

        with patch("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", []):
            res = await mw(handler, update, {"bot": mock_bot})
            assert res is None
            cb.answer.assert_called_once()
            assert "✅ Подписка подтверждена" in cb.answer.call_args[0][0]
            cb.message.delete.assert_called_once()
            mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_sub_filter_check_callback_failed(self, mock_session_factory, mock_redis):
        """Проверка подписки по кнопке 'Проверить подписку' (ещё не подписался)."""
        mw = SubscriptionFilterMiddleware(mock_session_factory, mock_redis)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@ch1"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@ch1"}]))

        handler = AsyncMock()

        update = MagicMock(spec=Update)
        cb = MagicMock(spec=CallbackQuery)
        cb.data = SUB_CHECK_CALLBACK
        cb.from_user = User(id=99999, is_bot=False, first_name="User")
        cb.message = MagicMock(spec=Message)
        cb.message.chat = MagicMock(id=99999)
        cb.answer = AsyncMock()
        update.message = None
        update.callback_query = cb

        mock_bot = AsyncMock()

        with patch("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", []):
            res = await mw(handler, update, {"bot": mock_bot})
            assert res is None
            cb.answer.assert_called_once()
            assert "❌ Вы ещё не подписались" in cb.answer.call_args[0][0]
            assert cb.answer.call_args[1].get("show_alert") is True
