import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, date, time

from bot import scheduler as scheduler_module
from bot.scheduler import morning_summary_broadcast, lesson_reminders_planner, collect_db_metrics
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
async def test_morning_summary_with_lessons_sends(mocker):
    udm = AsyncMock()
    udm.get_users_for_morning_summary.return_value = [(1, 'G')]

    tt_manager = MagicMock()
    tt_manager.get_schedule_for_day.return_value = {'lessons': [{'time': '09:00-10:30'}]}

    class FakeWeather:
        def __init__(self, *_, **__): pass
        async def get_forecast_for_time(self, *_):
            return {'temperature': 10, 'description': 'ясно'}
    mocker.patch.object(scheduler_module, 'WeatherAPI', FakeWeather)

    sender = MagicMock(); sender.send = MagicMock()
    mocker.patch.object(scheduler_module, 'send_message_task', sender)

    set_mock_datetime(mocker, MOSCOW_TZ.localize(datetime.combine(date.today(), time(8, 0))))

    await morning_summary_broadcast(udm, tt_manager)
    sender.send.assert_called_once()


@pytest.mark.asyncio
async def test_collect_db_metrics_logs_error(mocker):
    udm = AsyncMock()
    udm.get_total_users_count.side_effect = Exception("db down")
    mocker.patch.object(scheduler_module, 'USERS_TOTAL', MagicMock(set=MagicMock()))
    mocker.patch.object(scheduler_module, 'SUBSCRIBED_USERS', MagicMock(set=MagicMock()))
    await collect_db_metrics(udm)


@pytest.mark.asyncio
async def test_reminders_planner_final_lesson_branch(mocker):
    scheduler = AsyncMock(); scheduler.add_job = MagicMock()
    udm = AsyncMock()
    udm.get_users_for_lesson_reminders.return_value = [(7, 'G', 20)]

    lessons = [{'start_time_raw': '09:00', 'end_time_raw': '10:30'}]
    tt_manager = MagicMock(); tt_manager.get_schedule_for_day.return_value = {'lessons': lessons}

    set_mock_datetime(mocker, MOSCOW_TZ.localize(datetime.combine(date.today(), time(5, 0))))
    await lesson_reminders_planner(scheduler, udm, tt_manager)

    assert scheduler.add_job.call_count == 2