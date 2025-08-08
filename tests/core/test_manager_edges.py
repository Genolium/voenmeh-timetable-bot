import pytest
from datetime import date
from unittest.mock import AsyncMock

from core.manager import TimetableManager


class DummyRedis:
    def __init__(self):
        self.store = {}
    async def get(self, key):
        return self.store.get(key)
    async def set(self, key, value, ex=None):
        self.store[key] = value


@pytest.mark.asyncio
async def test_manager_week_type_none_without_period():
    data = {'__metadata__': {}}  # без period
    mgr = TimetableManager(data, DummyRedis())
    assert mgr.get_week_type(date.today()) is None


def test_find_teachers_query_too_short():
    mgr = TimetableManager({'__metadata__': {}, '__teachers_index__': {'Иванов': []}, 'G': {}}, DummyRedis())
    assert mgr.find_teachers('iv') == []


def test_teacher_not_found():
    mgr = TimetableManager({'__metadata__': {}, '__teachers_index__': {}, 'G': {}}, DummyRedis())
    err = mgr.get_teacher_schedule('Петров', date.today())
    assert 'не найден' in err['error']


def test_classroom_not_found():
    mgr = TimetableManager({'__metadata__': {}, '__classrooms_index__': {}, 'G': {}}, DummyRedis())
    err = mgr.get_classroom_schedule('505', date.today())
    assert 'не найдена' in err['error']


