"""Coverage tests for ``core.manager.TimetableManager``.

Redis is faked, the parser is patched. No network/DB access happens.
"""

import gzip
import json
import pickle
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.manager import TimetableManager

# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------


class _FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeRedis:
    def __init__(self, store=None, keys_list=None):
        self.store = dict(store or {})
        self._keys = list(keys_list or [])
        self.set_calls = []
        self.raise_on_set = False
        self.raise_on_get = False

    def lock(self, *args, **kwargs):
        return _FakeLock()

    async def get(self, key):
        if self.raise_on_get:
            raise RuntimeError("get failed")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.raise_on_set:
            raise RuntimeError("set failed")
        self.set_calls.append((key, value, ex))
        self.store[key] = value

    async def keys(self, pattern):
        return list(self._keys)


def sample_data():
    return {
        "__metadata__": {"period": {"StartYear": "2026", "StartMonth": "9", "StartDay": "1"}},
        "__teachers_index__": {
            "Иванов И.И.": [
                {"day": "Понедельник", "week_code": "0", "start_time_raw": "09:00", "subject": "Математика"},
                {"day": "Понедельник", "week_code": "1", "start_time_raw": "10:00", "subject": "Физика"},
                {"day": "Понедельник", "week_code": "2", "start_time_raw": "11:00", "subject": "Химия"},
            ]
        },
        "__classrooms_index__": {
            "101": [
                {"day": "Понедельник", "week_code": "0", "start_time_raw": "09:00", "subject": "Математика"},
                {"day": "Понедельник", "week_code": "1", "start_time_raw": "10:00", "subject": "Физика"},
            ]
        },
        "__current_xml_hash__": "hash-1",
        "ГРУППА1": {
            "odd": {"Понедельник": [{"start_time_raw": "09:00", "subject": "Математика"}]},
            "even": {},
        },
    }


def compress(data):
    temp = TimetableManager.__new__(TimetableManager)
    temp._use_compression = True
    return temp._compress_data(data)


@pytest.fixture
def redis_client():
    return FakeRedis()


# ---------------------------------------------------------------------------
# __init__ / compression
# ---------------------------------------------------------------------------


def test_init_empty_raises():
    with pytest.raises(ValueError):
        TimetableManager({}, FakeRedis())


def test_init_bad_period_warns(capsys):
    TimetableManager({"__metadata__": {"period": {"StartYear": "bad"}}, "G": {}}, FakeRedis())
    assert "не удалось разобрать" in capsys.readouterr().out


def test_compression_roundtrip():
    manager = TimetableManager(sample_data(), FakeRedis())
    payload = {"a": 1, "b": "текст"}
    assert manager._decompress_data(manager._compress_data(payload)) == payload

    manager._use_compression = False
    plain = manager._compress_data(payload)
    assert manager._decompress_data(plain) == payload


def test_decompress_json_fallback():
    manager = TimetableManager(sample_data(), FakeRedis())
    raw = json.dumps({"legacy": True}).encode("utf-8")
    assert manager._decompress_data(raw) == {"legacy": True}


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_from_cache(redis_client):
    redis_client.store["timetable:schedule_cache"] = compress(sample_data())
    with patch("core.parser.save_fallback_schedule") as save:
        manager = await TimetableManager.create(redis_client)
    assert manager is not None
    assert "ГРУППА1" in manager._schedules
    save.assert_called_once()


@pytest.mark.asyncio
async def test_create_from_cache_save_failure(redis_client, capsys):
    redis_client.store["timetable:schedule_cache"] = compress(sample_data())
    with patch("core.parser.save_fallback_schedule", side_effect=RuntimeError("boom")):
        manager = await TimetableManager.create(redis_client)
    assert manager is not None
    assert "Failed to update fallback schedule" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_create_from_network(redis_client):
    with patch("core.parser.fetch_and_parse_all_schedules", AsyncMock(return_value=sample_data())):
        manager = await TimetableManager.create(redis_client)
    assert manager is not None
    assert redis_client.set_calls  # saved to cache


@pytest.mark.asyncio
async def test_create_from_backup(redis_client):
    redis_client._keys = ["timetable:backup:2", "timetable:backup:1"]
    redis_client.store["timetable:backup:2"] = compress(sample_data())
    with (
        patch("core.parser.fetch_and_parse_all_schedules", AsyncMock(return_value=None)),
        patch("core.parser.save_fallback_schedule") as save,
    ):
        manager = await TimetableManager.create(redis_client)
    assert manager is not None
    save.assert_called_once()


@pytest.mark.asyncio
async def test_create_from_backup_save_failure(redis_client, capsys):
    redis_client._keys = ["timetable:backup:1"]
    redis_client.store["timetable:backup:1"] = compress(sample_data())
    with (
        patch("core.parser.fetch_and_parse_all_schedules", AsyncMock(return_value=None)),
        patch("core.parser.save_fallback_schedule", side_effect=RuntimeError("boom")),
    ):
        manager = await TimetableManager.create(redis_client)
    assert manager is not None
    assert "Failed to update fallback schedule from backup" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_create_from_fallback_file(redis_client):
    with (
        patch("core.parser.fetch_and_parse_all_schedules", AsyncMock(return_value=None)),
        patch("core.parser.load_fallback_schedule", return_value=sample_data()),
        patch("core.parser.save_fallback_schedule") as save,
    ):
        manager = await TimetableManager.create(redis_client)
    assert manager is not None
    save.assert_called_once()


@pytest.mark.asyncio
async def test_create_from_fallback_file_save_failure(redis_client, capsys):
    with (
        patch("core.parser.fetch_and_parse_all_schedules", AsyncMock(return_value=None)),
        patch("core.parser.load_fallback_schedule", return_value=sample_data()),
        patch("core.parser.save_fallback_schedule", side_effect=RuntimeError("boom")),
    ):
        manager = await TimetableManager.create(redis_client)
    assert manager is not None
    assert "Failed to update fallback schedule file" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_create_no_data_returns_none(redis_client):
    with (
        patch("core.parser.fetch_and_parse_all_schedules", AsyncMock(return_value=None)),
        patch("core.parser.load_fallback_schedule", return_value=None),
    ):
        manager = await TimetableManager.create(redis_client)
    assert manager is None


# ---------------------------------------------------------------------------
# _restore_from_backup / cache indexes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_from_backup_no_keys(redis_client):
    assert await TimetableManager._restore_from_backup(redis_client) is None


@pytest.mark.asyncio
async def test_restore_from_backup_empty_payload(redis_client):
    redis_client._keys = ["timetable:backup:1"]
    assert await TimetableManager._restore_from_backup(redis_client) is None


@pytest.mark.asyncio
async def test_restore_from_backup_exception(redis_client):
    redis_client.keys = AsyncMock(side_effect=RuntimeError("boom"))
    assert await TimetableManager._restore_from_backup(redis_client) is None


@pytest.mark.asyncio
async def test_save_to_cache_and_indexes(redis_client):
    manager = TimetableManager(sample_data(), redis_client)
    await manager.save_to_cache()
    keys = {call[0] for call in redis_client.set_calls}
    assert "timetable:index:teachers" in keys
    assert "timetable:index:classrooms" in keys


@pytest.mark.asyncio
async def test_cache_indexes_error(redis_client, capsys):
    manager = TimetableManager(sample_data(), redis_client)
    redis_client.raise_on_set = True
    await manager.cache_indexes()
    assert "Ошибка кэширования индексов" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_load_indexes_from_cache(redis_client):
    manager = TimetableManager(sample_data(), redis_client)
    redis_client.store["timetable:index:teachers"] = compress({"T": []})
    redis_client.store["timetable:index:classrooms"] = compress({"C": []})
    assert await manager.load_indexes_from_cache() is True

    redis_client.store.clear()
    assert await manager.load_indexes_from_cache() is False

    redis_client.raise_on_get = True
    assert await manager.load_indexes_from_cache() is False


# ---------------------------------------------------------------------------
# semester info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_academic_week_type():
    manager = TimetableManager(sample_data(), FakeRedis())
    week_key, week_name = await manager.get_academic_week_type(date(2026, 9, 7))
    assert week_key in {"odd", "even"}
    assert week_name in {"Нечетная", "Четная"}

    manager._semester_settings_manager = MagicMock()
    manager._semester_settings_manager.get_semester_settings = AsyncMock(return_value=(date(2026, 9, 1), date(2027, 2, 9)))
    await manager.get_academic_week_type(date(2026, 9, 7))

    manager._semester_settings_manager.get_semester_settings = AsyncMock(return_value=None)
    await manager.get_academic_week_type(date(2026, 9, 7))

    manager._semester_settings_manager.get_semester_settings = AsyncMock(side_effect=RuntimeError("boom"))
    await manager.get_academic_week_type(date(2026, 9, 7))


@pytest.mark.asyncio
async def test_get_semester_info_for():
    manager = TimetableManager(sample_data(), FakeRedis())
    info = await manager.get_semester_info_for(date(2026, 9, 7))
    assert info.semester == "fall"

    manager._semester_settings_manager = MagicMock()
    manager._semester_settings_manager.get_semester_settings = AsyncMock(return_value=(date(2026, 9, 1), date(2027, 2, 9)))
    assert await manager.get_semester_info_for(date(2026, 9, 7)) is not None

    manager._semester_settings_manager.get_semester_settings = AsyncMock(side_effect=RuntimeError("boom"))
    assert await manager.get_semester_info_for(date(2026, 9, 7)) is not None


# ---------------------------------------------------------------------------
# schedule getters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schedule_for_day_found_and_missing():
    manager = TimetableManager(sample_data(), FakeRedis())
    result = await manager.get_schedule_for_day("группа1", date(2026, 9, 7))
    assert result["group"] == "ГРУППА1"
    assert "lessons" in result

    # Unknown group with a similar name.
    similar = await manager.get_schedule_for_day("ГРУППА", date(2026, 9, 7))
    assert "Возможно, вы имели в виду" in similar["error"]

    # Unknown group with no similar name.
    missing = await manager.get_schedule_for_day("ZZZ", date(2026, 9, 7))
    assert "выпустилась" in missing["error"]

    # Sunday -> no lessons.
    sunday = await manager.get_schedule_for_day("ГРУППА1", date(2026, 9, 6))
    assert sunday["day_name"] == "Воскресенье"
    assert sunday["lessons"] == []


def test_find_teachers():
    manager = TimetableManager(sample_data(), FakeRedis())
    assert manager.find_teachers("Ив") == []
    assert manager.find_teachers("иванов") == ["Иванов И.И."]


def test_find_teachers_fuzzy():
    manager = TimetableManager(sample_data(), FakeRedis())
    assert manager.find_teachers_fuzzy("И") == []
    assert manager.find_teachers_fuzzy("Иванов") == ["Иванов И.И."]


def test_resolve_canonical_teacher():
    manager = TimetableManager(sample_data(), FakeRedis())
    assert manager.resolve_canonical_teacher("") is None
    assert manager.resolve_canonical_teacher("Иванов И.И.") == "Иванов И.И."
    assert manager.resolve_canonical_teacher("ивановии") == "Иванов И.И."
    assert manager.resolve_canonical_teacher("Иваноф И.И.") == "Иванов И.И."
    assert manager.resolve_canonical_teacher("Совсемдругое") is None


def test_resolve_canonical_teacher_normalize_exception():
    manager = TimetableManager(sample_data(), FakeRedis())

    class BadStr(str):
        def replace(self, *args, **kwargs):
            raise RuntimeError("boom")

    assert manager.resolve_canonical_teacher(BadStr("несуществующий")) is None


@pytest.mark.asyncio
async def test_get_teacher_schedule():
    manager = TimetableManager(sample_data(), FakeRedis())
    result = await manager.get_teacher_schedule("Иванов И.И.", date(2026, 9, 7))
    assert result["teacher"] == "Иванов И.И."
    assert len(result["lessons"]) >= 1

    # Normalized match.
    result = await manager.get_teacher_schedule("ивановии", date(2026, 9, 7))
    assert result["teacher"] == "Иванов И.И."

    # Fuzzy match.
    result = await manager.get_teacher_schedule("Иваноф И.И.", date(2026, 9, 7))
    assert result["teacher"] == "Иванов И.И."

    # Not found.
    missing = await manager.get_teacher_schedule("Совсемдругое", date(2026, 9, 7))
    assert "не найден" in missing["error"]

    # Sunday -> empty.
    sunday = await manager.get_teacher_schedule("Иванов И.И.", date(2026, 9, 6))
    assert sunday["day_name"] == "Воскресенье"
    assert sunday["lessons"] == []


@pytest.mark.asyncio
async def test_get_teacher_schedule_normalize_exception():
    manager = TimetableManager(sample_data(), FakeRedis())

    class BadStr(str):
        def replace(self, *args, **kwargs):
            raise RuntimeError("boom")

    result = await manager.get_teacher_schedule(BadStr("Совсемдругое"), date(2026, 9, 7))
    assert "не найден" in result["error"]


def test_find_classrooms():
    manager = TimetableManager(sample_data(), FakeRedis())
    assert manager.find_classrooms("") == []
    assert manager.find_classrooms("10") == ["101"]


def test_find_classrooms_fuzzy():
    manager = TimetableManager(sample_data(), FakeRedis())
    assert manager.find_classrooms_fuzzy("1") == []
    assert manager.find_classrooms_fuzzy("101") == ["101"]


@pytest.mark.asyncio
async def test_get_classroom_schedule():
    manager = TimetableManager(sample_data(), FakeRedis())
    result = await manager.get_classroom_schedule("101", date(2026, 9, 7))
    assert result["classroom"] == "101"
    assert len(result["lessons"]) >= 1

    missing = await manager.get_classroom_schedule("999", date(2026, 9, 7))
    assert "не найдена" in missing["error"]

    sunday = await manager.get_classroom_schedule("101", date(2026, 9, 6))
    assert sunday["lessons"] == []


def test_hash_and_settings_manager_accessors():
    manager = TimetableManager(sample_data(), FakeRedis())
    assert manager.get_current_xml_hash() == "hash-1"
    sentinel = MagicMock()
    manager.set_semester_settings_manager(sentinel)
    assert manager._semester_settings_manager is sentinel


@pytest.mark.asyncio
async def test_get_semester_settings_manager():
    manager = TimetableManager(sample_data(), FakeRedis())
    sentinel = MagicMock()
    manager.set_semester_settings_manager(sentinel)
    assert await manager.get_semester_settings_manager() is sentinel
