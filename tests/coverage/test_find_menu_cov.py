from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs import find_menu as fm
from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.states import FindMenu
from tests.coverage.helpers import make_callback, make_dm, make_message, make_timetable_manager


def _dm(tm=None, udm=None, dialog_data=None, lang="ru"):
    udm = udm or AsyncMock()
    udm.get_user_language.return_value = lang
    return make_dm(
        middleware={"manager": tm, "user_data_manager": udm, "_": lambda k, **kw: k},
        dialog_data=dialog_data if dialog_data is not None else {},
    )


class TestGetFindData:
    async def test_defaults_date_and_returns_translations(self):
        dm = _dm(tm=make_timetable_manager())
        data = await fm.get_find_data(dm)
        assert DialogDataKeys.CURRENT_DATE_ISO in dm.dialog_data
        assert "find_title" in data

    async def test_teacher_result(self):
        tm = make_timetable_manager()
        tm.resolve_canonical_teacher.return_value = "Иванов И.И."
        tm.get_teacher_schedule.return_value = {"lessons": [], "date": date(2025, 9, 1)}
        dm = _dm(
            tm=tm,
            dialog_data={
                DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                DialogDataKeys.TEACHER_NAME: "Иванов",
            },
        )
        data = await fm.get_find_data(dm)
        assert "result_text" in data
        tm.get_teacher_schedule.assert_awaited_once()

    async def test_classroom_result(self):
        tm = make_timetable_manager()
        tm.get_classroom_schedule.return_value = {"lessons": [], "date": date(2025, 9, 1)}
        dm = _dm(
            tm=tm,
            dialog_data={
                DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                DialogDataKeys.CLASSROOM_NUMBER: "521",
            },
        )
        data = await fm.get_find_data(dm)
        assert "result_text" in data
        tm.get_classroom_schedule.assert_awaited_once()


class TestOnTeacherInput:
    async def test_single_direct_match(self):
        tm = make_timetable_manager()
        tm.find_teachers.return_value = ["Иванов И.И."]
        udm = AsyncMock()
        dm = _dm(tm=tm, udm=udm)
        message = make_message(text="Иванов")
        await fm.on_teacher_input(message, MagicMock(), dm)
        assert dm.dialog_data[DialogDataKeys.TEACHER_NAME] == "Иванов И.И."
        udm.add_to_history.assert_awaited_once()
        dm.switch_to.assert_awaited_with(FindMenu.view_result)

    async def test_transliteration_fallback(self):
        tm = make_timetable_manager()
        tm.find_teachers.side_effect = [[], ["Иванов И.И."]]
        dm = _dm(tm=tm)
        message = make_message(text="Ivanov")
        await fm.on_teacher_input(message, MagicMock(), dm)
        assert tm.find_teachers.call_count == 2
        dm.switch_to.assert_awaited_with(FindMenu.view_result)

    async def test_not_found(self):
        tm = make_timetable_manager()
        tm.find_teachers.return_value = []
        dm = _dm(tm=tm)
        message = make_message(text="Иванов")
        await fm.on_teacher_input(message, MagicMock(), dm)
        message.answer.assert_awaited_once()
        dm.switch_to.assert_not_awaited()

    async def test_multiple_matches(self):
        tm = make_timetable_manager()
        tm.find_teachers.return_value = ["A", "B", "C"]
        dm = _dm(tm=tm)
        message = make_message(text="Иванов")
        await fm.on_teacher_input(message, MagicMock(), dm)
        assert dm.dialog_data[DialogDataKeys.FOUND_ITEMS] == ["A", "B", "C"]
        dm.switch_to.assert_awaited_with(FindMenu.select_item)

    async def test_analytics_error_is_swallowed(self):
        tm = make_timetable_manager()
        tm.find_teachers.return_value = ["Иванов И.И."]
        dm = _dm(tm=tm)
        message = make_message(text="Иванов")
        with patch("core.analytics.track_feature_click", side_effect=RuntimeError("boom")):
            await fm.on_teacher_input(message, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(FindMenu.view_result)

    async def test_single_match_without_user_data_manager(self):
        tm = make_timetable_manager()
        tm.find_teachers.return_value = ["Иванов И.И."]
        dm = _dm(tm=tm)
        dm.middleware_data.pop("user_data_manager")
        message = make_message(text="Иванов")
        await fm.on_teacher_input(message, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(FindMenu.view_result)


class TestOnClassroomInput:
    async def test_single_match(self):
        tm = make_timetable_manager()
        tm.find_classrooms.return_value = ["521"]
        udm = AsyncMock()
        dm = _dm(tm=tm, udm=udm)
        message = make_message(text="521")
        await fm.on_classroom_input(message, MagicMock(), dm)
        assert dm.dialog_data[DialogDataKeys.CLASSROOM_NUMBER] == "521"
        udm.add_to_history.assert_awaited_once()
        dm.switch_to.assert_awaited_with(FindMenu.view_result)

    async def test_not_found(self):
        tm = make_timetable_manager()
        tm.find_classrooms.return_value = []
        dm = _dm(tm=tm)
        message = make_message(text="999")
        await fm.on_classroom_input(message, MagicMock(), dm)
        message.answer.assert_awaited_once()
        dm.switch_to.assert_not_awaited()

    async def test_multiple_matches(self):
        tm = make_timetable_manager()
        tm.find_classrooms.return_value = ["521", "522"]
        dm = _dm(tm=tm)
        message = make_message(text="52")
        await fm.on_classroom_input(message, MagicMock(), dm)
        assert dm.dialog_data[DialogDataKeys.FOUND_ITEMS] == ["521", "522"]
        dm.switch_to.assert_awaited_with(FindMenu.select_item)

    async def test_analytics_error_is_swallowed(self):
        tm = make_timetable_manager()
        tm.find_classrooms.return_value = ["521"]
        dm = _dm(tm=tm)
        message = make_message(text="521")
        with patch("core.analytics.track_feature_click", side_effect=RuntimeError("boom")):
            await fm.on_classroom_input(message, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(FindMenu.view_result)


class TestOnGroupInput:
    async def test_single_match_records_history(self):
        tm = make_timetable_manager()
        tm.find_groups.return_value = ["О735Б"]
        udm = AsyncMock()
        dm = _dm(tm=tm, udm=udm)
        message = make_message(text="О735Б")
        await fm.on_group_input(message, MagicMock(), dm)
        udm.add_to_history.assert_awaited_once()
        dm.start.assert_awaited()


class TestOnItemSelected:
    async def test_teacher(self):
        udm = AsyncMock()
        dm = _dm(udm=udm, dialog_data={DialogDataKeys.SEARCH_TYPE: "teacher"})
        await fm.on_item_selected(make_callback(), MagicMock(), dm, "Иванов И.И.")
        assert dm.dialog_data[DialogDataKeys.TEACHER_NAME] == "Иванов И.И."
        udm.add_to_history.assert_awaited_once()
        dm.switch_to.assert_awaited_with(FindMenu.view_result)

    async def test_classroom(self):
        udm = AsyncMock()
        dm = _dm(udm=udm, dialog_data={DialogDataKeys.SEARCH_TYPE: "classroom"})
        await fm.on_item_selected(make_callback(), MagicMock(), dm, "521")
        assert dm.dialog_data[DialogDataKeys.CLASSROOM_NUMBER] == "521"
        udm.add_to_history.assert_awaited_once()
        dm.switch_to.assert_awaited_with(FindMenu.view_result)

    async def test_unknown_search_type(self):
        udm = AsyncMock()
        dm = _dm(udm=udm, dialog_data={})
        await fm.on_item_selected(make_callback(), MagicMock(), dm, "X")
        udm.add_to_history.assert_not_awaited()
        dm.switch_to.assert_awaited_with(FindMenu.view_result)


class TestDateNavigation:
    async def test_date_shift(self):
        dm = _dm(dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"})
        await fm.on_find_date_shift(make_callback(), MagicMock(), dm, -7)
        assert dm.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == "2025-08-25"

    async def test_today_click(self):
        dm = _dm(dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2000-01-01"})
        await fm.on_find_today_click(make_callback(), MagicMock(), dm)
        assert dm.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] != "2000-01-01"

    async def test_back_to_main_menu(self):
        dm = _dm()
        await fm.on_back_to_main_menu(make_callback(), MagicMock(), dm)
        dm.done.assert_awaited_once()
