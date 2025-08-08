import pytest
from unittest.mock import AsyncMock
from core.manager import TimetableManager


class DummyRedis:
    async def get(self, *_):
        return None
    async def set(self, *_ , **__):
        return None


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


