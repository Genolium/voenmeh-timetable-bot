import pytest
from unittest.mock import AsyncMock, MagicMock, call
from datetime import datetime, time, date, timedelta

from bot.scheduler import lesson_reminders_planner
from core.config import MOSCOW_TZ

from bot import scheduler as scheduler_module

@pytest.fixture
def mock_scheduler():
    """Фикстура, создающая мок AsyncIOScheduler."""
    scheduler = AsyncMock()
    scheduler.add_job = MagicMock()
    return scheduler

@pytest.fixture
def mock_user_data_manager():
    """Фикстура, создающая мок UserDataManager."""
    udm = AsyncMock()
    udm.get_users_for_lesson_reminders.return_value = [(123, "TEST_GROUP")]
    return udm

@pytest.fixture
def mock_timetable_manager(mocker):
    """Фикстура, мокирующая глобальный TimetableManager."""
    tt_manager = MagicMock()
    
    today_lessons = [
        {'time': '09:00-10:30', 'subject': 'Матан', 'type': 'Лекция', 'start_time_raw': '09:00', 'end_time_raw': '10:30'},
        {'time': '10:50-12:20', 'subject': 'Физика', 'type': 'Прак', 'start_time_raw': '10:50', 'end_time_raw': '12:20'}
    ]
    tt_manager.get_schedule_for_day.return_value = {'lessons': today_lessons}
    
    mocker.patch.object(scheduler_module, 'global_timetable_manager_instance', tt_manager)
    return tt_manager


@pytest.mark.asyncio
class TestLessonRemindersPlanner:

    def mock_datetime_now(self, mocker, target_datetime_str: str, target_date: date):
        """Хелпер для создания и установки 'фальшивого' класса datetime."""
        
        # Создаем "осознающий" таймзону объект из строки
        aware_mock_now = MOSCOW_TZ.localize(
            datetime.combine(target_date, time.fromisoformat(target_datetime_str))
        )

        # Создаем класс, который ведет себя как настоящий datetime, но `now()` возвращает наше значение
        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return aware_mock_now

        # Патчим `datetime` в модуле `scheduler` этим новым классом
        mocker.patch('bot.scheduler.datetime', MockDateTime)


    @pytest.mark.parametrize("mock_current_time_str, expected_call_count", [
        ("05:00:00", 3),
        ("08:50:00", 2),
        ("11:00:00", 1),
        ("13:00:00", 0),
    ])
    async def test_planner_schedules_jobs_correctly_based_on_time(
        self, mocker, mock_scheduler, mock_user_data_manager, mock_timetable_manager, mock_current_time_str, expected_call_count
    ):
        """
        Проверяет, что планировщик правильно добавляет задачи в зависимости от текущего времени.
        """
        # --- Arrange ---
        today = date(2024, 7, 12)
        self.mock_datetime_now(mocker, mock_current_time_str, today)
        
        # --- Act ---
        await lesson_reminders_planner(mock_scheduler, mock_user_data_manager)
        
        # --- Assert ---
        assert mock_scheduler.add_job.call_count == expected_call_count

    async def test_planner_calculates_run_dates_correctly(self, mocker, mock_scheduler, mock_user_data_manager, mock_timetable_manager):
        """
        Главный тест: проверяет, что ВРЕМЯ для каждого job'а вычислено ВЕРНО.
        """
        # --- Arrange ---
        today = date(2024, 7, 12)
        self.mock_datetime_now(mocker, "05:00:00", today)

        expected_first_reminder_time = MOSCOW_TZ.localize(datetime(2024, 7, 12, 8, 40))
        expected_break_reminder_time = MOSCOW_TZ.localize(datetime(2024, 7, 12, 10, 30))
        expected_final_reminder_time = MOSCOW_TZ.localize(datetime(2024, 7, 12, 12, 20))

        # --- Act ---
        await lesson_reminders_planner(mock_scheduler, mock_user_data_manager)

        # --- Assert ---
        calls = mock_scheduler.add_job.call_args_list
        assert len(calls) == 3

        actual_run_dates = [c.kwargs['trigger'].run_date for c in calls]
        
        assert expected_first_reminder_time in actual_run_dates
        assert expected_break_reminder_time in actual_run_dates
        assert expected_final_reminder_time in actual_run_dates
        
        # Находим вызов для перерыва и проверяем его аргументы
        break_call = next(c for c in calls if c.kwargs['trigger'].run_date == expected_break_reminder_time)
        assert break_call.kwargs['args'][2] == 'break'
        assert break_call.kwargs['args'][3] == 20

    async def test_planner_does_nothing_for_day_without_lessons(self, mocker, mock_scheduler, mock_user_data_manager, mock_timetable_manager):
        """Проверяет, что для дня без пар не создается ни одной задачи."""
        # --- Arrange ---
        mock_timetable_manager.get_schedule_for_day.return_value = {'lessons': []}
        self.mock_datetime_now(mocker, "05:00:00", date.today())

        # --- Act ---
        await lesson_reminders_planner(mock_scheduler, mock_user_data_manager)

        # --- Assert ---
        mock_scheduler.add_job.assert_not_called()