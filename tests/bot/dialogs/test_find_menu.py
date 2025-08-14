import pytest
from unittest.mock import AsyncMock, MagicMock
from bot.dialogs.find_menu import on_teacher_input, on_classroom_input, on_item_selected, get_find_data
from bot.dialogs.states import FindMenu

@pytest.fixture
def mock_manager():
    mock_tt_manager = MagicMock()
    manager = AsyncMock()
    manager.middleware_data = {"manager": mock_tt_manager}
    manager.dialog_data = {}
    manager.timetable_manager = mock_tt_manager
    return manager

@pytest.mark.asyncio
class TestFindMenu:
    async def test_on_teacher_input(self, mock_manager):
        mock_message = AsyncMock(text="Иванов")
        # Case 1: Single result
        mock_manager.timetable_manager.find_teachers.return_value = ["Иванов И.И."]
        await on_teacher_input(mock_message, None, mock_manager)
        assert mock_manager.dialog_data["teacher_name"] == "Иванов И.И."
        mock_manager.switch_to.assert_called_with(FindMenu.view_result)

        # Case 2: Multiple results
        mock_manager.reset_mock(); mock_manager.dialog_data = {}
        mock_manager.timetable_manager.find_teachers.return_value = ["Иванов И.И.", "Иванов П.С."]
        await on_teacher_input(mock_message, None, mock_manager)
        assert mock_manager.dialog_data["found_items"] == ["Иванов И.И.", "Иванов П.С."]
        mock_manager.switch_to.assert_called_with(FindMenu.select_item)

        # Case 3: Not found
        mock_manager.reset_mock(); mock_manager.dialog_data = {}
        mock_manager.timetable_manager.find_teachers.return_value = []
        await on_teacher_input(mock_message, None, mock_manager)
        mock_message.answer.assert_called_with("❌ Преподаватель не найден. Попробуйте ввести только фамилию.")
        mock_manager.switch_to.assert_not_called()

    async def test_on_classroom_input(self, mock_manager):
        mock_message = AsyncMock(text="418")
        # Case 1: Single result
        mock_manager.timetable_manager.find_classrooms.return_value = ["418"]
        await on_classroom_input(mock_message, None, mock_manager)
        assert mock_manager.dialog_data["classroom_number"] == "418"
        mock_manager.switch_to.assert_called_with(FindMenu.view_result)

    async def test_on_item_selected(self, mock_manager):
        # Teacher
        mock_manager.dialog_data = {"search_type": "teacher"}
        await on_item_selected(None, None, mock_manager, item_id="Петров П.П.")
        assert mock_manager.dialog_data["teacher_name"] == "Петров П.П."
        mock_manager.switch_to.assert_called_with(FindMenu.view_result)
        
        # Classroom
        mock_manager.reset_mock(); mock_manager.dialog_data = {"search_type": "classroom"}
        await on_item_selected(None, None, mock_manager, item_id="505")
        assert mock_manager.dialog_data["classroom_number"] == "505"
        mock_manager.switch_to.assert_called_with(FindMenu.view_result)
        
    async def test_get_find_data(self, mock_manager, mocker):
        mocker.patch('bot.dialogs.find_menu.format_teacher_schedule_text', return_value="Teacher Text")
        mocker.patch('bot.dialogs.find_menu.format_classroom_schedule_text', return_value="Classroom Text")
        
        mock_manager.dialog_data = {"teacher_name": "Иванов И.И.", "find_date_iso": "2023-10-26"}
        mock_manager.timetable_manager.get_teacher_schedule = AsyncMock(return_value={"lessons": []})
        data = await get_find_data(mock_manager)
        assert data["result_text"] == "Teacher Text"
        mock_manager.timetable_manager.get_teacher_schedule.assert_called_once()