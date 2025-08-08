import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, date, time

from bot.scheduler import lesson_reminders_planner, monitor_schedule_changes, setup_scheduler
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
async def test_planner_no_users_returns_early(mocker):
    scheduler = AsyncMock(); scheduler.add_job = MagicMock()
    udm = AsyncMock(); udm.get_users_for_lesson_reminders.return_value = []
    mock_tt_manager = MagicMock()
    set_mock_datetime(mocker, MOSCOW_TZ.localize(datetime.combine(date.today(), time(5, 0))))
    await lesson_reminders_planner(scheduler, udm, mock_tt_manager)
    scheduler.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_planner_break_reminder_exception_path(mocker):
    scheduler = AsyncMock(); scheduler.add_job = MagicMock()
    udm = AsyncMock(); udm.get_users_for_lesson_reminders.return_value = [(1, 'G', 20)]

    lessons = [
        {'start_time_raw': '09:00', 'end_time_raw': 'bad'},
        {'start_time_raw': '10:40', 'end_time_raw': '12:10'},
    ]
    tt_manager = MagicMock(); tt_manager.get_schedule_for_day.return_value = {'lessons': lessons}
    
    set_mock_datetime(mocker, MOSCOW_TZ.localize(datetime.combine(date.today(), time(5, 0))))
    await lesson_reminders_planner(scheduler, udm, tt_manager)
    assert scheduler.add_job.call_count >= 1


@pytest.mark.asyncio
async def test_monitor_schedule_changes_no_data(mocker):
    udm = AsyncMock()
    redis = AsyncMock(); redis.get.return_value = b''
    mock_bot = AsyncMock()
    mocker.patch('bot.scheduler.fetch_and_parse_all_schedules', AsyncMock(return_value=None))
    await monitor_schedule_changes(udm, redis, mock_bot)


def test_setup_scheduler_adds_all_jobs(mocker):
    added = []
    class FakeScheduler:
        def __init__(self, timezone): self.timezone = timezone
        def add_job(self, *args, **kwargs): added.append((args, kwargs))
        def start(self): pass
    mocker.patch('bot.scheduler.AsyncIOScheduler', FakeScheduler)

    bot, manager, udm, redis = object(), MagicMock(), MagicMock(), MagicMock()
    setup_scheduler(bot, manager, udm, redis)
    assert len(added) == 6