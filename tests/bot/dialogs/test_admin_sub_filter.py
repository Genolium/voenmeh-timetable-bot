from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message, User
from aiogram_dialog import DialogManager

from bot.dialogs.admin_menu import (
    get_sub_filter_data,
    on_add_channel_input,
    on_delete_sub_filter_channel,
    on_toggle_sub_filter,
)
from bot.dialogs.states import Admin


@pytest.fixture
def mock_manager():
    mgr = AsyncMock(spec=DialogManager)
    mgr.middleware_data = {
        "session_factory": MagicMock(),
        "redis_client": AsyncMock(),
        "bot": AsyncMock(),
    }
    mgr.dialog_data = {}
    mgr.switch_to = AsyncMock()
    return mgr


class TestAdminSubFilter:
    @pytest.mark.asyncio
    async def test_get_sub_filter_data(self, mock_manager):
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.is_filter_enabled = AsyncMock(return_value=True)
            inst.get_required_channels = AsyncMock(
                return_value=[
                    {"id": 1, "channel_id": "@chan1", "title": "Канал 1", "invite_link": "https://t.me/chan1"}
                ]
            )

            data = await get_sub_filter_data(mock_manager)
            assert data["is_enabled"] is True
            assert "🟢" in data["status_text"]
            assert data["channels_count"] == 1
            assert "Канал 1" in data["channels_text"]
            assert "🔴 Выключить фильтр" in data["toggle_btn_text"]

    @pytest.mark.asyncio
    async def test_on_toggle_sub_filter(self, mock_manager):
        callback = AsyncMock(spec=CallbackQuery)
        callback.answer = AsyncMock()

        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.is_filter_enabled = AsyncMock(return_value=False)
            inst.set_filter_enabled = AsyncMock()

            await on_toggle_sub_filter(callback, MagicMock(), mock_manager)

            inst.set_filter_enabled.assert_called_once_with(True)
            callback.answer.assert_called_once()
            assert "включен" in callback.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_on_add_channel_input_by_username(self, mock_manager):
        message = AsyncMock(spec=Message)
        message.text = "@my_new_channel"
        message.answer = AsyncMock()

        mock_bot = mock_manager.middleware_data["bot"]
        mock_chat = MagicMock()
        mock_chat.id = -100123456789
        mock_chat.title = "Мой Канал"
        mock_chat.username = "my_new_channel"
        mock_chat.invite_link = None
        mock_bot.get_chat.return_value = mock_chat

        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()

            await on_add_channel_input(message, MagicMock(), mock_manager, data="@my_new_channel")

            inst.add_required_channel.assert_called_once_with(
                channel_id="-100123456789",
                title="Мой Канал",
                invite_link="https://t.me/my_new_channel",
            )
            message.answer.assert_called_once()
            assert "успешно добавлен" in message.answer.call_args[0][0]
            mock_manager.switch_to.assert_called_once_with(Admin.sub_filter_menu)

    @pytest.mark.asyncio
    async def test_on_add_channel_input_cancel(self, mock_manager):
        message = AsyncMock(spec=Message)
        message.text = "/cancel"
        message.answer = AsyncMock()

        await on_add_channel_input(message, MagicMock(), mock_manager, data="/cancel")

        message.answer.assert_called_once_with("↩️ Отменено")
        mock_manager.switch_to.assert_called_once_with(Admin.sub_filter_menu)

    @pytest.mark.asyncio
    async def test_on_delete_sub_filter_channel(self, mock_manager):
        callback = AsyncMock(spec=CallbackQuery)
        callback.answer = AsyncMock()

        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.remove_required_channel = AsyncMock(return_value=True)

            await on_delete_sub_filter_channel(callback, MagicMock(), mock_manager, item_id="@old_chan")

            inst.remove_required_channel.assert_called_once_with("@old_chan")
            callback.answer.assert_called_once()
            assert "удалён" in callback.answer.call_args[0][0]
