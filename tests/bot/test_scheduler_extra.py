import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, date, time

from bot import scheduler as scheduler_module
from bot.scheduler import lesson_reminders_planner
from core.config import MOSCOW_TZ


def set_mock_datetime(mocker, dt: datetime):
    class MockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.astimezone(tz)
        @classmethod
        def combine(cls, d, t, tzinfo=None):
            return datetime.combine(d, t, tzinfo=tzinfo)
    mocker.patch('bot.scheduler.datetime', MockDateTime)


@pytest.mark.asyncio
async def test_planner_handles_invalid_times_safely(mocker):
    mock_scheduler = AsyncMock()
    mock_scheduler.add_job = MagicMock()

    udm = AsyncMock()
    udm.get_users_for_lesson_reminders.return_value = [(1, 'G', 20)]

    tt_manager = MagicMock()
    tt_manager.get_schedule_for_day.return_value = {
        'lessons': [
            {'start_time_raw': 'bad', 'end_time_raw': 'bad'},
            {'start_time_raw': '10:00', 'end_time_raw': 'bad'},
        ]
    }
    
    mock_now = MOSCOW_TZ.localize(datetime.combine(date.today(), time(5, 0)))
    set_mock_datetime(mocker, mock_now)

    await lesson_reminders_planner(mock_scheduler, udm, tt_manager)
    mock_scheduler.add_job.assert_not_called()