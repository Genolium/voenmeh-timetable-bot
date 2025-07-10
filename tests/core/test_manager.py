import pytest
from unittest.mock import MagicMock
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
            "Иванов И.И.": [{"day": "Понедельник", "week_code": "1", "start_time_raw": "09:00"}],
            "Петров П.П.": []
        },
        "__classrooms_index__": {
            "418": [{"day": "Понедельник", "week_code": "0", "start_time_raw": "10:40"}]
        }
    }

@pytest.fixture
def manager(sample_schedules):
    """Фикстура, создающая экземпляр TimetableManager."""
    mock_redis = MagicMock()
    return TimetableManager(all_schedules_data=sample_schedules, redis_client=mock_redis)

class TestTimetableManager:

    def test_get_week_type(self, manager):
        # Нечетная неделя (первая неделя семестра)
        assert manager.get_week_type(date(2023, 9, 4)) == ('odd', 'Нечетная')
        # Четная неделя
        assert manager.get_week_type(date(2023, 9, 11)) == ('even', 'Четная')
        # До начала семестра (считается нечетной)
        assert "до начала семестра" in manager.get_week_type(date(2023, 8, 30))[1]

    def test_get_schedule_for_day_success(self, manager):
        # Запрос на нечетный понедельник
        schedule = manager.get_schedule_for_day("O735Б", date(2023, 9, 4))
        assert not schedule.get('error')
        assert len(schedule['lessons']) == 2
        assert schedule['lessons'][0]['subject'] == 'Матан'

        # Запрос на четный понедельник
        schedule_even = manager.get_schedule_for_day("O735Б", date(2023, 9, 11))
        assert len(schedule_even['lessons']) == 1
        assert schedule_even['lessons'][0]['subject'] == 'Программирование'

    def test_get_schedule_for_day_no_lessons(self, manager):
        # Запрос на вторник, когда пар нет
        schedule = manager.get_schedule_for_day("O735Б", date(2023, 9, 5))
        assert not schedule.get('error')
        assert len(schedule['lessons']) == 0

    def test_get_schedule_for_day_group_not_found(self, manager):
        schedule = manager.get_schedule_for_day("XXXX", date(2023, 9, 4))
        assert schedule.get('error')
        assert "не найдена" in schedule['error']
        
    def test_find_teachers(self, manager):
        assert manager.find_teachers("Иван") == ["Иванов И.И."]
        assert manager.find_teachers("Сидоров") == []
        # Проверка на минимальную длину запроса
        assert manager.find_teachers("Ив") == []

    def test_find_classrooms(self, manager):
        assert manager.find_classrooms("418") == ["418"]
        assert manager.find_classrooms("999") == []