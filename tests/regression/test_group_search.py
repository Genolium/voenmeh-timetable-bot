from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.find_menu import on_group_input, on_history_selected, on_item_selected
from bot.dialogs.states import FindMenu, Schedule


@pytest.mark.asyncio
async def test_group_search_opens_schedule_without_changing_saved_group():
    timetable = MagicMock()
    timetable.find_groups.return_value = ["О735Б"]
    dialog = MagicMock()
    dialog.middleware_data = {"manager": timetable, "_": lambda key, **kw: key}
    dialog.start = AsyncMock()
    message = MagicMock()
    message.text = "О735Б"
    message.from_user = MagicMock(id=1)

    await on_group_input(message, MagicMock(), dialog)

    dialog.start.assert_awaited_once()
    assert dialog.start.call_args.args[0] is Schedule.view
    assert dialog.start.call_args.kwargs["data"]["group"] == "О735Б"
    dialog.middleware_data["manager"].find_groups.assert_called_once()


@pytest.mark.asyncio
async def test_history_routes_teacher_and_room():
    dm = MagicMock()
    dm.dialog_data = {}
    dm.switch_to = AsyncMock()
    await on_history_selected(MagicMock(), MagicMock(), dm, "teacher:Иванов И.И.")
    assert dm.dialog_data[DialogDataKeys.TEACHER_NAME] == "Иванов И.И."
    dm.switch_to.assert_awaited_with(FindMenu.view_result)

    dm.switch_to.reset_mock()
    await on_history_selected(MagicMock(), MagicMock(), dm, "room:101")
    assert dm.dialog_data[DialogDataKeys.CLASSROOM_NUMBER] == "101"
    dm.switch_to.assert_awaited_with(FindMenu.view_result)


@pytest.mark.asyncio
async def test_group_input_empty_and_not_found_and_multiple():
    timetable = MagicMock()
    timetable.find_groups.return_value = []
    timetable.find_groups_fuzzy.return_value = []
    dm = MagicMock()
    dm.middleware_data = {"manager": timetable, "_": lambda key, **kw: key}
    dm.dialog_data = {}
    dm.start = AsyncMock()
    dm.switch_to = AsyncMock()
    message = MagicMock()
    message.text = ""
    message.answer = AsyncMock()

    await on_group_input(message, MagicMock(), dm)
    message.answer.assert_awaited()

    message.text = "ZZZ"
    await on_group_input(message, MagicMock(), dm)
    assert message.answer.await_count == 2

    timetable.find_groups_fuzzy.return_value = ["G1", "G2"]
    message.text = "G"
    await on_group_input(message, MagicMock(), dm)
    assert dm.dialog_data[DialogDataKeys.SEARCH_TYPE] == "group"
    assert dm.dialog_data[DialogDataKeys.FOUND_ITEMS] == ["G1", "G2"]


@pytest.mark.asyncio
async def test_group_selection_starts_schedule_and_writes_history():
    udm = AsyncMock()
    dm = MagicMock()
    dm.dialog_data = {DialogDataKeys.SEARCH_TYPE: "group"}
    dm.middleware_data = {"user_data_manager": udm}
    dm.start = AsyncMock()
    callback = MagicMock()
    callback.from_user.id = 1

    await on_item_selected(callback, MagicMock(), dm, "G1")

    udm.add_to_history.assert_awaited_once_with(1, "group", "G1")
    dm.start.assert_awaited_once()
