import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs import about_menu, schedule_view
from bot.dialogs.find_menu import get_history_data, on_history_selected
from bot.dialogs.settings_menu import get_settings_data
from bot.dialogs.states import About, Feedback, FindMenu, Schedule
from bot.scheduler import morning_summary_broadcast
from core.config import MOSCOW_TZ
from main import start_command_handler


@pytest.mark.asyncio
async def test_settings_show_actual_notification_times():
    udm = AsyncMock()
    udm.get_user_settings.return_value = {
        "evening_notify": True,
        "morning_summary": True,
        "lesson_reminders": True,
        "reminder_time_minutes": 20,
        "theme": "standard",
        "morning_time": "07:00",
        "evening_time": "21:00",
    }
    udm.get_user_language.return_value = "ru"
    dm = MagicMock()
    dm.event.from_user.id = 1
    dm.middleware_data = {"user_data_manager": udm}

    data = await get_settings_data(dm)

    assert "21:00" in data["settings_evening_label"]
    assert "07:00" in data["settings_morning_label"]


@pytest.mark.asyncio
async def test_morning_summary_sends_no_lessons_message(monkeypatch):
    udm = AsyncMock()
    udm.get_users_for_morning_summary.return_value = [(1, "G", "ru", "student")]
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(
        return_value={
            "lessons": [],
            "date": date(2026, 10, 22),
            "day_name": "Среда",
            "week_name": "Четная",
        }
    )
    weather = MagicMock()
    weather.get_forecast_for_time = AsyncMock(return_value=None)
    monkeypatch.setattr("bot.scheduler.WeatherAPI", MagicMock(return_value=weather))
    monkeypatch.setattr("bot.scheduler.generate_morning_intro", MagicMock(return_value="Intro"))
    send = MagicMock()
    monkeypatch.setattr("bot.scheduler.send_message_task", send)

    await morning_summary_broadcast(udm, tm)

    send.send.assert_called_once()
    assert "Занятий нет" in send.send.call_args.args[1]


@pytest.mark.asyncio
async def test_help_and_feedback_callbacks_open_dialogs():
    dm = MagicMock()
    dm.start = AsyncMock()
    await schedule_view.on_help_click(MagicMock(), MagicMock(), dm)
    dm.start.assert_awaited_with(About.page_1, mode=schedule_view.StartMode.RESET_STACK)

    dm.start.reset_mock()
    await schedule_view.on_feedback_click(MagicMock(), MagicMock(), dm)
    dm.start.assert_awaited_with(Feedback.enter_feedback, mode=schedule_view.StartMode.RESET_STACK)


def test_tutorial_has_exit_on_every_page_and_honest_onboarding():
    assert about_menu.__file__
    source = Path(about_menu.__file__).read_text(encoding="utf-8")
    assert source.count("on_click=on_finish_clicked") >= 5
    reg_complete = json.loads(Path("locales/ru.json").read_text(encoding="utf-8"))["reg_complete"]
    assert "Статистика" not in reg_complete
    assert "групповых чатах" not in reg_complete


@pytest.mark.asyncio
async def test_history_ui_reads_and_routes_entries():
    udm = AsyncMock()
    udm.get_user_language.return_value = "ru"
    udm.get_history.side_effect = lambda _uid, kind: {
        "group": ["О735Б"],
        "teacher": ["Иванов И.И."],
        "room": ["101"],
    }[kind]
    dm = MagicMock()
    dm.event.from_user.id = 1
    dm.middleware_data = {"user_data_manager": udm}

    data = await get_history_data(dm)
    assert "group:О735Б" in data["history_items"]

    dm.start = AsyncMock()
    dm.switch_to = AsyncMock()
    await on_history_selected(MagicMock(), MagicMock(), dm, "group:О735Б")
    dm.start.assert_awaited_with(
        Schedule.view,
        data={"group": "О735Б"},
        mode=schedule_view.StartMode.RESET_STACK,
    )


def test_week_image_contains_teacher_field():
    generator = Path("core/image_generator.py").read_text(encoding="utf-8")
    template = Path("templates/schedule_template.html").read_text(encoding="utf-8")
    assert '"teacher": teacher_fmt' in generator
    assert "l.teacher" in template


@pytest.mark.asyncio
async def test_deep_link_opens_group_preview():
    from core.user_data import UserDataManager

    udm = MagicMock(spec=UserDataManager)
    udm.get_user_group = AsyncMock()
    dm = MagicMock()
    dm.middleware_data = {
        "user_data_manager": udm,
        "manager": MagicMock(_schedules={"О735Б": {}}),
    }
    dm.start = AsyncMock()
    message = MagicMock()
    message.from_user.id = 1
    command = MagicMock(args="group_О735Б")

    await start_command_handler(message, dm, command)

    dm.start.assert_awaited_with(
        Schedule.view,
        data={"group": "О735Б"},
        mode=schedule_view.StartMode.RESET_STACK,
    )


@pytest.mark.asyncio
async def test_text_week_sends_formatted_message():
    dm = MagicMock()
    ctx = MagicMock()
    ctx.dialog_data = {"group": "G1", "current_date_iso": "2026-10-22", "user_type": "student"}
    dm.current_context.return_value = ctx
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(
        return_value={
            "day_name": "Четверг",
            "week_name": "Четная",
            "lessons": [{"time": "09:00-10:30", "subject": "Math", "start_time_raw": "09:00"}],
        }
    )
    udm = AsyncMock()
    udm.get_user_language.return_value = "ru"
    dm.middleware_data = {"manager": tm, "user_data_manager": udm}
    callback = MagicMock()
    callback.from_user.id = 1
    callback.message.answer = AsyncMock()

    await schedule_view.on_text_week_click(callback, MagicMock(), dm)

    callback.message.answer.assert_awaited_once()
    assert "Math" in callback.message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_quality_gate_button_delegates_to_original():
    dm = MagicMock()
    with patch.object(schedule_view, "on_send_original_file", new=AsyncMock()) as send:
        await schedule_view.on_check_subscription_button(MagicMock(), MagicMock(), dm)
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_week_teacher_branch():
    dm = MagicMock()
    ctx = MagicMock()
    ctx.dialog_data = {"group": "T1", "current_date_iso": "2026-10-22", "user_type": "teacher"}
    dm.current_context.return_value = ctx
    tm = MagicMock()
    tm.get_teacher_schedule = AsyncMock(return_value={"day_name": "Четверг", "week_name": "Четная", "lessons": []})
    udm = AsyncMock()
    udm.get_user_language.return_value = "ru"
    dm.middleware_data = {"manager": tm, "user_data_manager": udm}
    callback = MagicMock()
    callback.from_user.id = 1
    callback.message.answer = AsyncMock()

    await schedule_view.on_text_week_click(callback, MagicMock(), dm)

    tm.get_teacher_schedule.assert_awaited()
    callback.message.answer.assert_awaited_once()
