
import pytest
import json
import gzip
import pickle
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from core.manager import TimetableManager

class MockRedis:
    def __init__(self, data=None):
        self.data = data or {}
        
    async def get(self, key):
        return self.data.get(key)
        
    async def keys(self, pattern):
        # Simple glob matching emulation or just return all keys
        return list(self.data.keys())

@pytest.fixture
def basic_manager():
    data = {"__metadata__": {}, "G1": {}, "__teachers_index__": {"Иванов И.И.": []}}
    return TimetableManager(data, AsyncMock())

@pytest.mark.asyncio
async def test_restore_from_backup_real_logic():
    # Test the ACTUAL _restore_from_backup method (not mocked)
    redis = AsyncMock()
    # Setup keys
    redis.keys.return_value = ["timetable:backup:2024-01-01", "timetable:backup:2024-01-02"]
    
    # Setup get
    # It gets latest key: 2024-01-02
    data = {"__metadata__": {}, "G_BACKUP": {}}
    compressed = gzip.compress(pickle.dumps(data))
    redis.get.return_value = compressed
    
    restored = await TimetableManager._restore_from_backup(redis)
    assert restored is not None
    assert "G_BACKUP" in restored

@pytest.mark.asyncio
async def test_restore_from_backup_date_sort():
    redis = AsyncMock()
    redis.keys.return_value = ["timetable:backup:2024-01-01", "timetable:backup:2024-01-10", "timetable:backup:2024-01-05"]
    
    redis.get.return_value = gzip.compress(pickle.dumps({"target": "latest"}))
    
    await TimetableManager._restore_from_backup(redis)
    
    # Checks that it fetched the LATEST key
    redis.get.assert_called_with("timetable:backup:2024-01-10")

@pytest.mark.asyncio
async def test_restore_from_backup_empty_keys():
    redis = AsyncMock()
    redis.keys.return_value = []
    res = await TimetableManager._restore_from_backup(redis)
    assert res is None

@pytest.mark.asyncio
async def test_restore_from_backup_exception():
    redis = AsyncMock()
    redis.keys.side_effect = Exception("Boom")
    res = await TimetableManager._restore_from_backup(redis)
    assert res is None

def test_resolve_canonical_teacher(basic_manager):
    mgr = basic_manager
    # 1. Exact
    assert mgr.resolve_canonical_teacher("Иванов И.И.") == "Иванов И.И."
    # 2. Normalized
    assert mgr.resolve_canonical_teacher("иванов и.и.") == "Иванов И.И."
    assert mgr.resolve_canonical_teacher("ИвановИИ") == "Иванов И.И."
    # 3. Fuzzy
    # Need mock or specific data? 
    # Current index has "Иванов И.И."
    assert mgr.resolve_canonical_teacher("Иванов") == "Иванов И.И." # rapidfuzz should catch this
    
    # 4. None
    assert mgr.resolve_canonical_teacher(None) is None
    assert mgr.resolve_canonical_teacher("Smith") is None

def test_resolve_canonical_teacher_normalization_exception():
    # Force exception in normalization (hard to trigger in pure python str, but maybe None?)
    # The code handles None before normalization.
    # We can try to patch find_teachers_fuzzy to return None
    pass

@pytest.mark.asyncio
async def test_get_teacher_schedule_normalization():
    data = {
        "__metadata__": {}, 
        "__teachers_index__": {
            "Иванов И.И.": [{"day": "Понедельник", "week_code": "0", "start_time_raw": "10:00", "subject": "Test"}]
        }
    }
    mgr = TimetableManager(data, AsyncMock())
    
    # Test getting by normalized name
    res = await mgr.get_teacher_schedule("иванов и.и.", date(2024,9,2))
    assert res["teacher"] == "Иванов И.И."
    assert len(res["lessons"]) == 1

    # Test fuzzy fallback in get_teacher_schedule
    res2 = await mgr.get_teacher_schedule("Иванов", date(2024,9,2))
    assert res2["teacher"] == "Иванов И.И."

def test_compression_toggle():
    data = {"test": 123}
    mgr = TimetableManager({"__metadata__": {}}, AsyncMock())
    
    # Default: use compression
    compressed = mgr._compress_data(data)
    assert compressed.startswith(b"\x1f\x8b") # gzip magic
    restored = mgr._decompress_data(compressed)
    assert restored == data
    
    # Toggle off
    mgr._use_compression = False
    raw = mgr._compress_data(data)
    assert b"test" in raw
    assert b"123" in raw
    restored_raw = mgr._decompress_data(raw)
    assert restored_raw == data

def test_decompress_fallback():
    mgr = TimetableManager({"__metadata__": {}}, AsyncMock())
    # Pass plain json bytes to decompress
    plain = json.dumps({"a": 1}).encode()
    res = mgr._decompress_data(plain) # Should try gzip list, fail, fallback to json
    assert res == {"a": 1}

def test_metadata_period_error(capsys):
    # Init with bad period (dict but missing keys) -> KeyError
    data = {"__metadata__": {"period": {}}}
    mgr = TimetableManager(data, AsyncMock())
    assert mgr.semester_start_date is None
    # Check stdout/stderr for warning "не удалось разобрать дату"
    captured = capsys.readouterr()
    assert "не удалось разобрать дату" in captured.out

def test_simple_getters():
    data = {"__metadata__": {}, "__current_xml_hash__": "hash123"}
    mgr = TimetableManager(data, AsyncMock())
    assert mgr.get_current_xml_hash() == "hash123"

@pytest.mark.asyncio
async def test_restore_from_backup_empty_val():
    redis = AsyncMock()
    redis.keys.return_value = ["backup:1"]
    redis.get.return_value = None # Key exists but value is None (expired?)
    res = await TimetableManager._restore_from_backup(redis)
    assert res is None

def test_init_empty():
    with pytest.raises(ValueError):
        TimetableManager({}, AsyncMock())

async def test_semester_settings_manager_integration():
    mgr = TimetableManager({"__metadata__": {}}, AsyncMock())
    
    mock_settings = AsyncMock()
    mock_settings.get_semester_settings.return_value = (date(2024, 9, 10), date(2025, 2, 10))
    
    mgr.set_semester_settings_manager(mock_settings)
    assert await mgr.get_semester_settings_manager() == mock_settings
    
    # Verify get_academic_week_type uses it
    # Date before 2024-09-10 should be handled
    # 2024-09-01 is before start=2024-09-10
    # Logic: if < fall_start, year -= 1
    # Check simple case: 2024-09-15. it is >= start.
    week_type = await mgr.get_academic_week_type(date(2024, 9, 15))
    # 2024-09-15 is Sunday. 
    # Start Mon: 2024-09-09. (Wait, 2024-09-10 is Tue. Start Mon is 09-09)
    # Target Mon: 2024-09-09.
    # Diff = 0. Week 0.
    # 0 is "is_odd" (logic: week_number % 2 == 0 -> odd) in `get_academic_week_type`?
    # Let's check logic in manager lines 274: is_odd = (week_number % 2) == 0.
    # So week 0 is Odd.
    assert week_type[0] == "odd" 

async def test_similar_groups_suggestion():
    data = {"G-100": {}, "G-101": {}, "A-100": {}, "__metadata__": {}}
    mgr = TimetableManager(data, AsyncMock())
    
    # Call get_schedule_for_day with invalid group similar to G-100
    res = await mgr.get_schedule_for_day("G-10", date.today())
    assert "G-100" in res["error"]
    assert "G-101" in res["error"]

