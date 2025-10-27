from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from aiogram_dialog import StartMode

from bot.dialogs.main_menu import on_group_entered, on_show_tutorial_clicked, on_skip_tutorial_clicked
from bot.dialogs.states import About, MainMenu, Schedule


@pytest.fixture
def mock_manager(mocker):
    manager = AsyncMock()
    manager.middleware_data = {
        "manager": MagicMock(_schedules={"О735Б": {}}),
        "user_data_manager": AsyncMock(),
    }
    manager.dialog_data = {}
    manager.event.from_user = MagicMock(id=123, username="test")
    return manager


@pytest.mark.asyncio
class TestMainMenu:
    async def test_on_group_entered_success(self, mock_manager):
        mock_message = AsyncMock(text="о735б")
        mock_message.from_user = mock_manager.event.from_user

        await on_group_entered(mock_message, None, mock_manager)

        udm = mock_manager.middleware_data["user_data_manager"]
        udm.register_user.assert_called_once_with(user_id=123, username="test")
        udm.set_user_group.assert_called_once_with(user_id=123, group="О735Б")

        assert mock_manager.dialog_data["group"] == "О735Б"
        mock_manager.switch_to.assert_called_once_with(MainMenu.offer_tutorial)

    async def test_on_group_entered_fail(self, mock_manager):
        mock_message = AsyncMock(text="XXXXX")
        await on_group_entered(mock_message, None, mock_manager)

        mock_message.answer.assert_called_once()
        assert "Группа <b>XXXXX</b> не найдена" in mock_message.answer.call_args[0][0]
        mock_manager.switch_to.assert_not_called()

    async def test_on_skip_tutorial_clicked(self, mock_manager):
        mock_manager.dialog_data = {"group": "TESTGROUP"}
        await on_skip_tutorial_clicked(None, None, mock_manager)
        mock_manager.start.assert_called_once_with(Schedule.view, data={"group": "TESTGROUP"}, mode=StartMode.RESET_STACK)

    async def test_on_show_tutorial_clicked(self, mock_manager):
        await on_show_tutorial_clicked(None, None, mock_manager)
        mock_manager.start.assert_called_once_with(About.page_1, mode=StartMode.RESET_STACK)
