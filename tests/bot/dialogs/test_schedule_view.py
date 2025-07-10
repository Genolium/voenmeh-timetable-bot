import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import date, datetime, time, timedelta

from aiogram_dialog import StartMode
from bot.dialogs.schedule_view import (
    generate_dynamic_header,
    get_schedule_data,
    on_date_shift,
    on_today_click,
    on_change_group_click,
    on_settings_click,
    on_find_click
)
from bot.dialogs.states import Schedule, MainMenu, SettingsMenu, FindMenu
from core.config import MOSCOW_TZ


@pytest.fixture
def mock_manager():
    manager = AsyncMock()
    mock_context = MagicMock()
    mock_context.dialog_data = {}
    manager.current_context = MagicMock(return_value=mock_context)
    manager.middleware_data = {"manager": MagicMock()}
    return manager

@pytest.fixture
def lessons_sample():
    return [
        {'start_time_raw': '09:00', 'end_time_raw': '10:30', 'subject': 'Матан', 'time': '09:00-10:30'},
        {'start_time_raw': '10:40', 'end_time_raw': '12:10', 'subject': 'Физика', 'time': '10:40-12:10'}
    ]

class TestGenerateDynamicHeader:

    # Ожидаемый результат теперь включает HTML-теги, эмодзи и знаки препинания
    @pytest.mark.parametrize("mock_time_str, expected_header", [
        ("08:00", "☀️ <b>Доброе утро!</b> Первая пара в 09:00."),
        ("09:30", "⏳ <b>Идет пара:</b> Матан.\nЗакончится в 10:30."),
        ("10:35", "☕️ <b>Перерыв до 10:40.</b>\nСледующая пара: Физика."),
        ("11:00", "⏳ <b>Идет пара:</b> Физика.\nЗакончится в 12:10."),
        ("13:00", "✅ <b>Пары на сегодня закончились.</b> Отдыхайте!"),
        ("04:00", "🌙 <b>Поздняя ночь.</b> Скоро утро!"),
    ])
    def test_header_for_today(self, mocker, lessons_sample, mock_time_str, expected_header):
        today = datetime.now(MOSCOW_TZ).date()
        mock_time = time.fromisoformat(mock_time_str)
        mocked_now_dt = datetime.combine(today, mock_time, tzinfo=MOSCOW_TZ)
        
        mock_datetime = mocker.patch('bot.dialogs.schedule_view.datetime')
        mock_datetime.now.return_value = mocked_now_dt
        mock_datetime.strptime = datetime.strptime

        header, progress_bar = generate_dynamic_header(lessons_sample, today)
        
        assert header == expected_header
        assert "Прогресс дня" in progress_bar

    def test_header_not_today(self, lessons_sample):
        not_today = datetime.now(MOSCOW_TZ).date() + timedelta(days=1)
        header, progress_bar = generate_dynamic_header(lessons_sample, not_today)
        assert header == ""
        assert progress_bar == ""

    def test_header_no_lessons(self, mocker):
        today = datetime.now(MOSCOW_TZ).date()
        mocker.patch('bot.dialogs.schedule_view.datetime', MagicMock(now=lambda tz: datetime.combine(today, time(12,0), tzinfo=MOSCOW_TZ)))
        header, progress_bar = generate_dynamic_header([], today)
        assert "Сегодня занятий нет" in header
        assert progress_bar == ""


@pytest.mark.asyncio
class TestScheduleViewHandlers:

    async def test_on_date_shift(self, mock_manager):
        today = date(2023, 10, 26)
        mock_manager.current_context().dialog_data["current_date_iso"] = today.isoformat()
        await on_date_shift(AsyncMock(), MagicMock(), mock_manager, days=1)
        expected_date = date(2023, 10, 27).isoformat()
        assert mock_manager.current_context().dialog_data["current_date_iso"] == expected_date

    async def test_on_today_click(self, mock_manager):
        yesterday = (datetime.now(MOSCOW_TZ).date() - timedelta(days=1)).isoformat()
        mock_manager.current_context().dialog_data["current_date_iso"] = yesterday
        await on_today_click(AsyncMock(), MagicMock(), mock_manager)
        today_iso = datetime.now(MOSCOW_TZ).date().isoformat()
        assert mock_manager.current_context().dialog_data["current_date_iso"] == today_iso

    async def test_navigation_clicks(self, mock_manager):
        await on_change_group_click(AsyncMock(), MagicMock(), mock_manager)
        mock_manager.start.assert_called_with(MainMenu.enter_group, mode=StartMode.RESET_STACK)
        mock_manager.start.reset_mock()

        await on_settings_click(AsyncMock(), MagicMock(), mock_manager)
        mock_manager.start.assert_called_with(SettingsMenu.main)
        mock_manager.start.reset_mock()

        await on_find_click(AsyncMock(), MagicMock(), mock_manager)
        mock_manager.start.assert_called_with(FindMenu.choice)
        mock_manager.start.reset_mock()

    async def test_get_schedule_data(self, mock_manager, lessons_sample, mocker):
        today_iso = datetime.now(MOSCOW_TZ).date().isoformat()
        group_name = "O735Б"
        
        ctx = mock_manager.current_context()
        ctx.dialog_data = {"group": group_name, "current_date_iso": today_iso}

        timetable_manager = mock_manager.middleware_data['manager']
        timetable_manager.get_schedule_for_day.return_value = {
            "lessons": lessons_sample, "date": date.fromisoformat(today_iso), "day_name": "Тест"
        }
        
        mocker.patch('bot.dialogs.schedule_view.format_schedule_text', return_value="FormattedText")
        mocker.patch('bot.dialogs.schedule_view.generate_dynamic_header', return_value=("DynamicHeader", "ProgressBar"))

        data = await get_schedule_data(dialog_manager=mock_manager)

        timetable_manager.get_schedule_for_day.assert_called_once_with(group_name, target_date=date.fromisoformat(today_iso))
        assert data["dynamic_header"] == "DynamicHeader"
        assert data["progress_bar"] == "ProgressBar"
        assert data["schedule_text"] == "FormattedText"
        assert data["has_lessons"] is True