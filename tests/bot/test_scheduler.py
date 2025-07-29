import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, time, date, timezone

from bot.scheduler import lesson_reminders_planner
from core.config import MOSCOW_TZ

from bot import scheduler as scheduler_module

@pytest.fixture
def mock_scheduler():
    scheduler = AsyncMock()
    scheduler.add_job = MagicMock()
    return scheduler

@pytest.fixture
def mock_user_data_manager():
    udm = AsyncMock()
    udm.get_users_for_lesson_reminders.return_value = [(123, "TEST_GROUP", 20)]
    return udm

@pytest.fixture
def mock_timetable_manager(mocker):
    tt_manager = MagicMock()
    today_lessons = [
        {'start_time_raw': '09:00', 'end_time_raw': '10:30'},
        {'start_time_raw': '10:50', 'end_time_raw': '12:20'}
    ]
    tt_manager.get_schedule_for_day.return_value = {'lessons': today_lessons}
    mocker.patch.object(scheduler_module, 'global_timetable_manager_instance', tt_manager)
    return tt_manager

def mock_datetime(mocker, target_dt: datetime):
    class MockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return target_dt.astimezone(tz)
        
        @classmethod
        def combine(cls, d, t, tzinfo=None):
            return datetime.combine(d, t, tzinfo=tzinfo)

    mocker.patch('bot.scheduler.datetime', MockDateTime)


@pytest.mark.asyncio
class TestLessonRemindersPlanner:

    @pytest.mark.parametrize("mock_current_time_str, expected_call_count", [
        ("05:00:00", 3),
        ("08:50:00", 2),
        ("11:00:00", 1),
        ("13:00:00", 0),
    ])
    async def test_planner_schedules_jobs_correctly_based_on_time(
        self, mocker, mock_scheduler, mock_user_data_manager, mock_timetable_manager, mock_current_time_str, expected_call_count
    ):
        mock_now = MOSCOW_TZ.localize(datetime.combine(date.today(), time.fromisoformat(mock_current_time_str)))
        mock_datetime(mocker, mock_now)
        
        await lesson_reminders_planner(mock_scheduler, mock_user_data_manager)
        
        assert mock_scheduler.add_job.call_count == expected_call_count

    async def test_planner_calculates_run_dates_correctly(self, mocker, mock_scheduler, mock_user_data_manager, mock_timetable_manager):
        mock_now = MOSCOW_TZ.localize(datetime.combine(date.today(), time(5, 0)))
        mock_datetime(mocker, mock_now)

        await lesson_reminders_planner(mock_scheduler, mock_user_data_manager)

        calls = mock_scheduler.add_job.call_args_list
        assert len(calls) == 3

        run_times = sorted([c.kwargs['trigger'].run_date.time() for c in calls])
        
        assert run_times[0] == time(8, 40)
        assert run_times[1] == time(10, 30)
        assert run_times[2] == time(12, 20)

        break_call = next(c for c in calls if c.kwargs['trigger'].run_date.time() == time(10, 30))
        assert break_call.kwargs['args'][2] == 'break'

    async def test_planner_does_nothing_for_day_without_lessons(self, mocker, mock_scheduler, mock_user_data_manager, mock_timetable_manager):
        mock_timetable_manager.get_schedule_for_day.return_value = {'lessons': []}
        mock_now = MOSCOW_TZ.localize(datetime.combine(date.today(), time(5, 0)))
        mock_datetime(mocker, mock_now)

        await lesson_reminders_planner(mock_scheduler, mock_user_data_manager)
        mock_scheduler.add_job.assert_not_called()
