from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram_dialog import StartMode

from bot.dialogs import main_menu as mm
from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.states import About, MainMenu, Schedule
from tests.coverage.helpers import make_callback, make_dm, make_message, make_timetable_manager


def _dm(tm=None, udm=None, dialog_data=None, lang="ru"):
    udm = udm or AsyncMock()
    udm.get_user_language.return_value = lang
    return make_dm(
        middleware={"manager": tm, "user_data_manager": udm, "_": lambda k, **kw: k},
        dialog_data=dialog_data or {},
    )


class TestGetMainMenuData:
    async def test_with_groups(self):
        tm = make_timetable_manager(_schedules={"О735Б": {}, "И905": {}, "__service__": {}})
        dm = _dm(tm=tm, dialog_data={DialogDataKeys.GROUP: "О735Б"})
        data = await mm.get_main_menu_data(dm)
        assert data["random_group"] in {"О735Б", "И905"}
        assert "reg_complete" in data

    async def test_without_groups_uses_fallback(self):
        tm = make_timetable_manager(_schedules={"__service__": {}})
        dm = _dm(tm=tm)
        data = await mm.get_main_menu_data(dm)
        assert data["random_group"] == "О735Б"

    async def test_english_language(self):
        tm = make_timetable_manager(_schedules={"О735Б": {}})
        dm = _dm(tm=tm, lang="en")
        data = await mm.get_main_menu_data(dm)
        assert data["en_checked"] == "✅"


class TestOnLangSelected:
    async def test_switch_to_role_selection(self):
        udm = AsyncMock()
        dm = _dm(udm=udm)
        button = MagicMock(widget_id="lang_en")
        await mm.on_lang_selected(make_callback(), button, dm)
        udm.set_user_language.assert_awaited_once_with(123, "en")
        dm.switch_to.assert_awaited_with(MainMenu.choose_user_type)


class TestOnUserTypeSelected:
    async def test_student(self):
        dm = _dm()
        callback = make_callback(data="user_type_student")
        await mm.on_user_type_selected(callback, MagicMock(), dm)
        assert dm.dialog_data["user_type"] == "student"
        dm.switch_to.assert_awaited_with(MainMenu.enter_group)

    async def test_teacher(self):
        dm = _dm()
        callback = make_callback(data="user_type_teacher")
        await mm.on_user_type_selected(callback, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(MainMenu.enter_teacher)


class TestOnGroupEntered:
    async def test_empty_normalized_input(self):
        dm = _dm(tm=make_timetable_manager())
        message = make_message(text="@@@")
        await mm.on_group_entered(message, MagicMock(), dm)
        message.answer.assert_awaited_once_with("incorrect_group")

    async def test_exact_match_registers_student(self):
        tm = make_timetable_manager(_schedules={"О735Б": {}})
        udm = AsyncMock()
        dm = _dm(tm=tm, udm=udm)
        message = make_message(text="о735б")
        await mm.on_group_entered(message, MagicMock(), dm)
        udm.register_user.assert_awaited_once()
        udm.set_user_group.assert_awaited_once_with(user_id=123, group="О735Б")
        udm.set_user_type.assert_awaited_once_with(user_id=123, user_type="student")
        dm.switch_to.assert_awaited_with(MainMenu.offer_tutorial)

    async def test_suggestions_offered(self):
        tm = make_timetable_manager(_schedules={"О735Б": {}})
        dm = _dm(tm=tm)
        message = make_message(text="О735Х")
        with patch.object(mm.process, "extract", return_value=[("О735Б", 90), ("X", 40)]):
            await mm.on_group_entered(message, MagicMock(), dm)
        message.answer.assert_awaited_once()
        dm.switch_to.assert_not_awaited()

    async def test_not_found(self):
        tm = make_timetable_manager(_schedules={"О735Б": {}})
        dm = _dm(tm=tm)
        message = make_message(text="ZZZZZ")
        with patch.object(mm.process, "extract", return_value=[("О735Б", 10)]):
            await mm.on_group_entered(message, MagicMock(), dm)
        message.answer.assert_awaited_once_with("group_not_found")
        dm.switch_to.assert_not_awaited()


class TestOnTeacherEntered:
    async def test_too_short(self):
        dm = _dm(tm=make_timetable_manager())
        message = make_message(text="ab")
        await mm.on_teacher_entered(message, MagicMock(), dm)
        message.answer.assert_awaited_once()

    async def test_empty(self):
        dm = _dm(tm=make_timetable_manager())
        message = make_message(text=None)
        await mm.on_teacher_entered(message, MagicMock(), dm)
        message.answer.assert_awaited_once()

    async def test_canonical_registers_teacher(self):
        tm = make_timetable_manager()
        tm.resolve_canonical_teacher.return_value = "Иванов Иван Иванович"
        udm = AsyncMock()
        dm = _dm(tm=tm, udm=udm)
        message = make_message(text="Иванов Иван")
        await mm.on_teacher_entered(message, MagicMock(), dm)
        udm.set_user_group.assert_awaited_once_with(user_id=123, group="Иванов Иван Иванович")
        udm.set_user_type.assert_awaited_once_with(user_id=123, user_type="teacher")
        dm.switch_to.assert_awaited_with(MainMenu.offer_tutorial)

    async def test_suggestions(self):
        tm = make_timetable_manager()
        tm.resolve_canonical_teacher.return_value = None
        tm.find_teachers_fuzzy.return_value = ["Иванов И.И.", "Петров П.П.", "Сидоров С.С.", "Четвёртый"]
        dm = _dm(tm=tm)
        message = make_message(text="Иванов")
        await mm.on_teacher_entered(message, MagicMock(), dm)
        message.answer.assert_awaited_once()
        dm.switch_to.assert_not_awaited()

    async def test_not_found(self):
        tm = make_timetable_manager()
        tm.resolve_canonical_teacher.return_value = None
        tm.find_teachers_fuzzy.return_value = []
        dm = _dm(tm=tm)
        message = make_message(text="Несуществующий")
        await mm.on_teacher_entered(message, MagicMock(), dm)
        message.answer.assert_awaited_once()
        dm.switch_to.assert_not_awaited()


class TestTutorialButtons:
    async def test_skip_tutorial(self):
        dm = _dm(dialog_data={DialogDataKeys.GROUP: "О735Б"})
        await mm.on_skip_tutorial_clicked(make_callback(), MagicMock(), dm)
        dm.start.assert_awaited_once_with(
            Schedule.view,
            data={DialogDataKeys.GROUP: "О735Б"},
            mode=StartMode.RESET_STACK,
        )

    async def test_show_tutorial(self):
        dm = _dm()
        await mm.on_show_tutorial_clicked(make_callback(), MagicMock(), dm)
        dm.start.assert_awaited_once_with(About.page_1, mode=StartMode.RESET_STACK)
