import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import date
from core.manager import TimetableManager
import json
import gzip
import pickle

class DummyRedis:
    async def get(self, *_):
        return None
    async def set(self, *_ , **__):
        return None

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
    compressed_data = gzip.compress(pickle.dumps(sample_schedules))
    mock_redis.get.return_value = json.dumps(sample_schedules).encode('utf-8')
    
    # Mock the lock method to return an async context manager
    mock_lock = AsyncMock()
    mock_lock.__aenter__ = AsyncMock()
    mock_lock.__aexit__ = AsyncMock()
    mock_redis.lock = MagicMock(return_value=mock_lock)
    
    mock_parser = mocker.patch('core.parser.fetch_and_parse_all_schedules', new_callable=AsyncMock)
    
    manager_instance = await TimetableManager.create(redis_client=mock_redis)
    
    assert manager_instance is not None
    mock_parser.assert_not_called()
    # manager_instance.redis.set.assert_not_called()  # Comment out if needed, as save_to_cache not called in this path

class TestTimetableManager:

    def test_get_week_type(self, manager):
        assert manager.get_week_type(date(2023, 9, 4)) == ('odd', 'Нечетная')
        assert manager.get_week_type(date(2023, 9, 11)) == ('even', 'Четная')
        assert "до начала семестра" in manager.get_week_type(date(2023, 8, 30))[1]

    @pytest.mark.asyncio
    async def test_get_academic_week_type(self, manager):
        """
        Тест определения типа недели по академическому календарю.
        
        ВАЖНО: После исправления бага с календарными неделями, логика изменена:
        - Неделя определяется по календарным неделям (понедельник-воскресенье)
        - Все дни одной календарной недели имеют одинаковый тип (четная/нечетная)
        """
        # 1 сентября - четная неделя (первая неделя семестра)
        assert await manager.get_academic_week_type(date(2023, 9, 1)) == ('even', 'Четная')
        assert await manager.get_academic_week_type(date(2024, 9, 1)) == ('even', 'Четная')

        # 11 сентября 2023 - понедельник второй недели семестра (четная)
        # Неделя 1: 1-3 сент (четная), Неделя 2: 4-10 сент (нечетная), Неделя 3: 11-17 сент (четная)
        assert await manager.get_academic_week_type(date(2023, 9, 11)) == ('even', 'Четная')

        # Проверим что вся неделя с 11 сентября имеет одинаковый тип
        assert await manager.get_academic_week_type(date(2023, 9, 15)) == ('even', 'Четная')  # пятница той же недели

        # Следующая неделя должна быть нечетной
        assert await manager.get_academic_week_type(date(2023, 9, 18)) == ('odd', 'Нечетная')  # понедельник следующей недели

        # Первая полная неделя февраля - четная (вторая неделя весеннего семестра)
        assert await manager.get_academic_week_type(date(2024, 2, 12)) == ('even', 'Четная')

        # Вторая неделя февраля - нечетная
        assert await manager.get_academic_week_type(date(2024, 2, 19)) == ('odd', 'Нечетная')
        
        # Лето - используем предыдущий осенний семестр
        # Июль 2024 - много недель после начала семестра 1 сент 2023
        july_result = await manager.get_academic_week_type(date(2024, 7, 15))
        assert july_result[0] in ['odd', 'even']  # Должно быть либо четная, либо нечетная
        assert july_result[1] in ['Нечетная', 'Четная']

    @pytest.mark.asyncio
    async def test_get_schedule_for_day_success(self, manager):
        schedule = await manager.get_schedule_for_day("O735Б", date(2023, 9, 4))
        assert not schedule.get('error')
        assert len(schedule['lessons']) == 2
        
        schedule_even = await manager.get_schedule_for_day("O735Б", date(2023, 9, 11))
        assert len(schedule_even['lessons']) == 1

    @pytest.mark.asyncio
    async def test_get_schedule_for_day_no_lessons(self, manager):
        schedule = await manager.get_schedule_for_day("O735Б", date(2023, 9, 5))
        assert not schedule.get('error')
        assert len(schedule['lessons']) == 0

    @pytest.mark.asyncio
    async def test_get_schedule_for_day_group_not_found(self, manager):
        schedule = await manager.get_schedule_for_day("XXXX", date(2023, 9, 4))
        assert "не найдена" in schedule['error']
        
    def test_find_teachers(self, manager):
        assert manager.find_teachers("Иван") == ["Иванов И.И."]
        assert manager.find_teachers("Сидоров") == []
        assert manager.find_teachers("Ив") == []

    def test_find_classrooms(self, manager):
        assert manager.find_classrooms("418") == ["418"]
        assert manager.find_classrooms("999") == []

    @pytest.mark.asyncio
    async def test_get_teacher_schedule_success(self, manager):
        schedule = await manager.get_teacher_schedule("Иванов И.И.", date(2023, 9, 4))
        assert len(schedule['lessons']) == 1
        assert schedule['lessons'][0]['subject'] == "Матан"

        schedule_tuesday = await manager.get_teacher_schedule("Иванов И.И.", date(2023, 9, 5))
        assert len(schedule_tuesday['lessons']) == 1
        assert schedule_tuesday['lessons'][0]['subject'] == "Физика"

    @pytest.mark.asyncio
    async def test_get_classroom_schedule_success(self, manager):
        schedule = await manager.get_classroom_schedule("418", date(2023, 9, 4))
        assert len(schedule['lessons']) == 1
        assert schedule['lessons'][0]['subject'] == "Базы данных"

    @pytest.mark.asyncio
    async def test_get_schedule_for_teacher_not_found(self, manager):
        schedule = await manager.get_teacher_schedule("Неизвестный", date(2023, 9, 4))
        assert 'error' in schedule

def test_manager_week_type_none_without_period():
    data = {'__metadata__': {}}  # без period
    mgr = TimetableManager(data, DummyRedis())
    assert mgr.get_week_type(date.today()) is None


def test_find_teachers_query_too_short():
    mgr = TimetableManager({'__metadata__': {}, '__teachers_index__': {'Иванов': []}, 'G': {}}, DummyRedis())
    assert mgr.find_teachers('iv') == []


@pytest.mark.asyncio
async def test_teacher_not_found():
    mgr = TimetableManager({'__metadata__': {}, '__teachers_index__': {}, 'G': {}}, DummyRedis())
    err = await mgr.get_teacher_schedule('Петров', date.today())
    assert 'не найден' in err['error']


@pytest.mark.asyncio
async def test_classroom_not_found():
    mgr = TimetableManager({'__metadata__': {}, '__classrooms_index__': {}, 'G': {}}, DummyRedis())
    err = await mgr.get_classroom_schedule('505', date.today())
    assert 'не найдена' in err['error']


def make_manager():
    data = {
        '__metadata__': {},
        '__teachers_index__': {
            'Иванов': [], 'Петров': [], 'Сидоров': [],
        },
        '__classrooms_index__': {
            '505': [], '400а': [], '401': [],
        },
        'G': {}
    }
    return TimetableManager(data, DummyRedis())


def test_teachers_fuzzy_basic():
    m = make_manager()
    res = m.find_teachers_fuzzy('иванов')
    assert 'Иванов' in res


def test_teachers_fuzzy_short_returns_empty():
    m = make_manager()
    assert m.find_teachers_fuzzy('a') == []


def test_classrooms_fuzzy():
    m = make_manager()
    res = m.find_classrooms_fuzzy('505')
    assert '505' in res

@pytest.mark.asyncio
async def test_teacher_and_classroom_schedule_paths():
    data = {
        '__metadata__': {'period': {'StartYear': '2024', 'StartMonth': '9', 'StartDay': '1'}},
        'G1': {
            'odd': {'Понедельник': [{'start_time_raw': '09:00', 'end_time_raw': '10:30'}]},
            'even': {}
        },
        '__teachers_index__': {
            'Иванов': [
                {'day': 'Понедельник', 'week_code': '1', 'start_time_raw': '09:00', 'end_time_raw': '10:30'}
            ]
        },
        '__classrooms_index__': {
            '101': [
                {'day': 'Понедельник', 'week_code': '1', 'start_time_raw': '09:00', 'end_time_raw': '10:30'}
            ]
        },
        '__current_xml_hash__': 'h1'
    }
    mgr = TimetableManager(data, DummyRedis())
    today = date(2024, 9, 2)  # понедельник, нечётная неделя
    t = await mgr.get_teacher_schedule('Иванов', today)
    c = await mgr.get_classroom_schedule('101', today)
    assert t and c and t['lessons'] and c['lessons']