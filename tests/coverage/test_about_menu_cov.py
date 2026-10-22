from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiogram_dialog import StartMode

from bot.dialogs import about_menu as am
from bot.dialogs.states import Schedule
from tests.coverage.helpers import make_callback, make_dm


class TestGetAboutData:
    async def test_returns_page_keys(self):
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        dm = make_dm(middleware={"user_data_manager": udm})
        data = await am.get_about_data(dm)
        assert "about_page_1" in data
        assert "about_finish" in data


class TestOnFinishClicked:
    async def test_success_with_group(self):
        udm = AsyncMock()
        udm.get_user_group.return_value = "О735Б"
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        callback.message.delete = AsyncMock()

        await am.on_finish_clicked(callback, MagicMock(), dm)

        callback.message.delete.assert_awaited_once()
        dm.start.assert_awaited_once_with(Schedule.view, data={"group": "О735Б"}, mode=StartMode.RESET_STACK)

    async def test_stale_message_delete_error_is_ignored(self):
        udm = AsyncMock()
        udm.get_user_group.return_value = "О735Б"
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        callback.message.delete = AsyncMock(
            side_effect=TelegramBadRequest(
                method=DeleteMessage(chat_id=1, message_id=1),
                message="message can't be deleted",
            )
        )

        await am.on_finish_clicked(callback, MagicMock(), dm)
        dm.start.assert_awaited_once()

    async def test_unexpected_delete_error_reraises(self):
        udm = AsyncMock()
        udm.get_user_group.return_value = "О735Б"
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        callback.message.delete = AsyncMock(
            side_effect=TelegramBadRequest(
                method=DeleteMessage(chat_id=1, message_id=1),
                message="unexpected telegram failure",
            )
        )

        with pytest.raises(TelegramBadRequest):
            await am.on_finish_clicked(callback, MagicMock(), dm)

    async def test_no_group_finishes_dialog(self):
        udm = AsyncMock()
        udm.get_user_group.return_value = None
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        callback.message.delete = AsyncMock()

        await am.on_finish_clicked(callback, MagicMock(), dm)

        dm.done.assert_awaited_once()
        dm.start.assert_not_awaited()
