import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import date
from core.manager import TimetableManager

@pytest.fixture
def sample_schedules():
    """Фикстура с примером данных расписания."""
    return {
        "__metadata__": {
            "period": {"StartYear": "2023", "StartMonth": "9", "StartDay": "1"}
        },
        "O735Б": {
            "odd": {
                "Понедельник": [
                    {"start_time_raw": "09:00", "subject": "Матан"},
                    {"start_time_raw": "10:40", "subject": "Физика"}
                ]
            },
            "even": {
                "Понедельник": [
                    {"start_time_raw": "09:00", "subject": "Программирование"}
                ]
            }
        },
        "__teachers_index__": {
            "Иванов И.И.": [
                {"day": "Понедельник", "week_code": "1", "start_time_raw": "09:00", "subject": "Матан"},
                {"day": "Вторник", "week_code": "0", "start_time_raw": "10:40", "subject": "Физика"}
            ],
            "Петров П.П.": []
        },
        "__classrooms_index__": {
            "418": [
                {"day": "Понедельник", "week_code": "0", "start_time_raw": "10:40", "subject": "Базы данных"}
            ]
        }
    }

@pytest.fixture
def manager(sample_schedules):
    """Фикстура, создающая экземпляр TimetableManager."""
    mock_redis = MagicMock()
    return TimetableManager(all_schedules_data=sample_schedules, redis_client=mock_redis)

@pytest.mark.asyncio
async def test_create_manager_with_cache(mocker, sample_schedules):
    """Тест асинхронного конструктора с использованием кэша Redis."""
    mock_redis = AsyncMock()
    mock_redis.get.return_value = pytest.importorskip("json").dumps(sample_schedules)
    
    mock_parser = mocker.patch('core.parser.fetch_and_parse_all_schedules', new_callable=AsyncMock)
    
    manager_instance = await TimetableManager.create(redis_client=mock_redis)
    
    assert manager_instance is not None
    mock_parser.assert_not_called()
    manager_instance.redis.set.assert_not_called()

class TestTimetableManager:

    def test_get_week_type(self, manager):
        assert manager.get_week_type(date(2023, 9, 4)) == ('odd', 'Нечетная')
        assert manager.get_week_type(date(2023, 9, 11)) == ('even', 'Четная')
        assert "до начала семестра" in manager.get_week_type(date(2023, 8, 30))[1]

    def test_get_schedule_for_day_success(self, manager):
        schedule = manager.get_schedule_for_day("O735Б", date(2023, 9, 4))
        assert not schedule.get('error')
        assert len(schedule['lessons']) == 2
        
        schedule_even = manager.get_schedule_for_day("O735Б", date(2023, 9, 11))
        assert len(schedule_even['lessons']) == 1

    def test_get_schedule_for_day_no_lessons(self, manager):
        schedule = manager.get_schedule_for_day("O735Б", date(2023, 9, 5))
        assert not schedule.get('error')
        assert len(schedule['lessons']) == 0

    def test_get_schedule_for_day_group_not_found(self, manager):
        schedule = manager.get_schedule_for_day("XXXX", date(2023, 9, 4))
        assert "не найдена" in schedule['error']
        
    def test_find_teachers(self, manager):
        assert manager.find_teachers("Иван") == ["Иванов И.И."]
        assert manager.find_teachers("Сидоров") == []
        assert manager.find_teachers("Ив") == []

    def test_find_classrooms(self, manager):
        assert manager.find_classrooms("418") == ["418"]
        assert manager.find_classrooms("999") == []

    def test_get_teacher_schedule_success(self, manager):
        schedule = manager.get_teacher_schedule("Иванов И.И.", date(2023, 9, 4))
        assert len(schedule['lessons']) == 1
        assert schedule['lessons'][0]['subject'] == "Матан"

        schedule_tuesday = manager.get_teacher_schedule("Иванов И.И.", date(2023, 9, 5))
        assert len(schedule_tuesday['lessons']) == 1
        assert schedule_tuesday['lessons'][0]['subject'] == "Физика"

    def test_get_classroom_schedule_success(self, manager):
        schedule = manager.get_classroom_schedule("418", date(2023, 9, 4))
        assert len(schedule['lessons']) == 1
        assert schedule['lessons'][0]['subject'] == "Базы данных"

    def test_get_schedule_for_teacher_not_found(self, manager):
        schedule = manager.get_teacher_schedule("Неизвестный", date(2023, 9, 4))
        assert 'error' in schedule