import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, time, timedelta

from bot import scheduler as scheduler_module
from bot.scheduler import evening_broadcast, morning_summary_broadcast, monitor_schedule_changes, collect_db_metrics
from core.config import MOSCOW_TZ


@pytest.fixture
def fake_metrics(mocker):
    class _Counter:
        def labels(self, **_):
            return self
        def inc(self):
            return None

    class _Gauge:
        def set(self, *_):
            return None

    mocker.patch.object(scheduler_module, 'TASKS_SENT_TO_QUEUE', _Counter())
    mocker.patch.object(scheduler_module, 'USERS_TOTAL', _Gauge())
    mocker.patch.object(scheduler_module, 'SUBSCRIBED_USERS', _Gauge())
    mocker.patch.object(scheduler_module, 'LAST_SCHEDULE_UPDATE_TS', _Gauge())


@pytest.fixture
def fake_send_task(mocker):
    sender = MagicMock()
    sender.send = MagicMock()
    mocker.patch.object(scheduler_module, 'send_message_task', sender)
    return sender


@pytest.fixture
def fake_weather(mocker):
    class FakeWeather:
        def __init__(self, *_, **__):
            pass
        async def get_forecast_for_time(self, *_):
            return {'temperature': 5, 'description': 'ясно', 'emoji': '☀️'}
    mocker.patch.object(scheduler_module, 'WeatherAPI', FakeWeather)


@pytest.fixture
def fake_timetable():
    return MagicMock()


@pytest.mark.asyncio
async def test_evening_broadcast_no_users(fake_metrics, fake_send_task, fake_weather, fake_timetable):
    udm = AsyncMock()
    udm.get_users_for_evening_notify.return_value = []
    await evening_broadcast(udm, fake_timetable)
    fake_send_task.send.assert_not_called()


@pytest.mark.asyncio
async def test_evening_broadcast_with_users_and_no_lessons(fake_metrics, fake_send_task, fake_weather, fake_timetable, mocker):
    fake_timetable.get_schedule_for_day.return_value = {'lessons': []}
    udm = AsyncMock()
    udm.get_users_for_evening_notify.return_value = [(10, 'G-1')]
    
    class MockDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return MOSCOW_TZ.localize(datetime(2025, 1, 1, 12, 0))
    mocker.patch('bot.scheduler.datetime', MockDT)

    await evening_broadcast(udm, fake_timetable)
    fake_send_task.send.assert_called_once()


@pytest.mark.asyncio
async def test_morning_summary_no_users(fake_metrics, fake_send_task, fake_weather, fake_timetable, mocker):
    udm = AsyncMock()
    udm.get_users_for_morning_summary.return_value = []

    class MockDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return MOSCOW_TZ.localize(datetime(2025, 1, 2, 8, 0))
    mocker.patch('bot.scheduler.datetime', MockDT)

    await morning_summary_broadcast(udm, fake_timetable)
    fake_send_task.send.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_schedule_changes_detected(fake_metrics, fake_send_task, mocker):
    redis = AsyncMock()
    redis.get.return_value = b'old'
    udm = AsyncMock()
    udm.get_all_user_ids.return_value = [1, 2]
    mock_bot = AsyncMock()
    
    new_data = {
        '__metadata__': {}, '__teachers_index__': {}, '__classrooms_index__': {},
        '__current_xml_hash__': 'new', 'G-1': {'odd': {}, 'even': {}},
    }
    mocker.patch('bot.scheduler.fetch_and_parse_all_schedules', AsyncMock(return_value=new_data))

    await monitor_schedule_changes(udm, redis, mock_bot)

    assert any(c.args == ('timetable:schedule_hash', 'new') for c in redis.set.call_args_list)
    assert fake_send_task.send.call_count == 2


@pytest.mark.asyncio
async def test_monitor_schedule_changes_no_change(fake_metrics, fake_send_task, mocker):
    redis = AsyncMock()
    redis.get.return_value = b'same'
    udm = AsyncMock()
    mock_bot = AsyncMock()

    new_data = {'__current_xml_hash__': 'same'}
    mocker.patch('bot.scheduler.fetch_and_parse_all_schedules', AsyncMock(return_value=new_data))

    await monitor_schedule_changes(udm, redis, mock_bot)
    fake_send_task.send.assert_not_called()


@pytest.mark.asyncio
async def test_collect_db_metrics_sets_gauges(fake_metrics):
    udm = AsyncMock()
    udm.get_total_users_count.return_value = 10
    udm.get_subscribed_users_count.return_value = 7
    await collect_db_metrics(udm)