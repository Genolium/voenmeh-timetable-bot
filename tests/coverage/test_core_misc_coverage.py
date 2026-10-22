"""Coverage tests for small/self-contained ``core`` modules.

Modules covered here:

* ``core.themes``
* ``core.text_utils``
* ``core.semester_calendar``
* ``core.time_utils``
* ``core.config``
* ``core.db`` / ``core.db.models``
* ``core.i18n``
* ``core.transliteration``
* ``core.schedule_diff``

Nothing touches real infrastructure: no network, no DB, no Redis.
"""

import importlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import core.config as config_module
import core.db  # noqa: F401  (import covers module-level statements)
import core.db.models  # noqa: F401
from core import transliteration as translit_module
from core.i18n import I18n
from core.schedule_diff import ChangeType, GroupScheduleDiff, LessonChange, ScheduleDiffDetector, ScheduleDiffFormatter
from core.semester_calendar import SemesterInfo, get_semester_info
from core.text_utils import normalize_group_name, transliterate_failed_search
from core.themes import DEFAULT_THEME, get_all_theme_ids, get_theme_meta, is_valid_theme
from core.time_utils import now_msk_naive, to_msk_naive, utc_now_aware

# ---------------------------------------------------------------------------
# core.themes
# ---------------------------------------------------------------------------


def test_is_valid_theme_all_branches():
    assert is_valid_theme("standard") is True
    assert is_valid_theme("STANDARD") is True
    assert is_valid_theme("does-not-exist") is False
    assert is_valid_theme(None) is False
    assert is_valid_theme(123) is False


def test_get_theme_meta_all_branches():
    assert get_theme_meta("dark").id == "dark"
    assert get_theme_meta("DARK").id == "dark"
    assert get_theme_meta(None).id == DEFAULT_THEME
    assert get_theme_meta(123).id == DEFAULT_THEME
    assert get_theme_meta("unknown-theme").id == DEFAULT_THEME


def test_get_all_theme_ids():
    ids = get_all_theme_ids()
    assert "standard" in ids
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# core.text_utils
# ---------------------------------------------------------------------------


def test_normalize_group_name():
    assert normalize_group_name("") == ""
    assert normalize_group_name("o735b") == "О735Б"
    assert normalize_group_name("  и-123!@#  ") == "И-123"


def test_transliterate_failed_search():
    assert transliterate_failed_search("Ivanov") == "Иванов"
    assert transliterate_failed_search("") == ""


# ---------------------------------------------------------------------------
# core.semester_calendar
# ---------------------------------------------------------------------------


def test_semester_info_is_active():
    active = SemesterInfo(semester="fall", semester_start=date(2026, 9, 1), week_number=3, week_key="odd")
    assert active.is_active is True
    inactive = SemesterInfo(semester="fall", semester_start=date(2026, 9, 1), week_number=0, week_key="even")
    assert inactive.is_active is False


def test_get_semester_info_fall_and_spring():
    fall = get_semester_info(date(2026, 9, 7))
    assert fall.semester == "fall"
    assert fall.week_key in {"odd", "even"}

    spring = get_semester_info(date(2027, 2, 15))
    assert spring.semester == "spring"

    # Outside the 18-week spring window -> week_number is forced to 0.
    after_spring = get_semester_info(date(2027, 7, 1))
    assert after_spring.week_number == 0
    assert after_spring.week_key == "even"


def test_get_semester_info_custom_starts():
    info = get_semester_info(
        date(2026, 10, 1),
        fall_start=date(2026, 9, 1),
        spring_start=date(2027, 2, 9),
    )
    assert info.semester == "fall"
    assert info.week_number >= 1


# ---------------------------------------------------------------------------
# core.time_utils
# ---------------------------------------------------------------------------


def test_to_msk_naive_naive_and_aware():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert to_msk_naive(naive) == naive

    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    converted = to_msk_naive(aware)
    assert converted.tzinfo is None
    assert converted.hour == 15  # UTC+3


def test_now_msk_naive_and_utc_now_aware():
    assert now_msk_naive().tzinfo is None
    assert utc_now_aware().tzinfo is not None


# ---------------------------------------------------------------------------
# core.metrics (system metrics collector)
# ---------------------------------------------------------------------------


def test_collect_system_metrics(monkeypatch):
    from core import metrics

    metrics.collect_system_metrics()

    def _boom():
        raise RuntimeError("no time")

    monkeypatch.setattr(metrics.time, "time", _boom)
    metrics.collect_system_metrics()  # exception swallowed


# ---------------------------------------------------------------------------
# core.config
# ---------------------------------------------------------------------------


def test_config_admin_ids_invalid(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "not-a-number,123")
    try:
        importlib.reload(config_module)
        assert config_module.ADMIN_IDS == []
    finally:
        monkeypatch.delenv("ADMIN_ID", raising=False)
        importlib.reload(config_module)


def test_config_admin_ids_valid_after_restore():
    # Sanity check that the module still parses the .env value after reload.
    assert all(isinstance(i, int) for i in config_module.ADMIN_IDS)


def test_config_get_redis_client(monkeypatch):
    import redis.asyncio as redis_asyncio

    captured = {}

    def fake_from_url(url, **kwargs):
        captured["url"] = url
        return "redis-client"

    monkeypatch.setattr(redis_asyncio.Redis, "from_url", fake_from_url)
    assert config_module.get_redis_client() == "redis-client"
    assert captured["url"] == config_module.settings.REDIS_URL


# ---------------------------------------------------------------------------
# core.i18n
# ---------------------------------------------------------------------------


def test_i18n_missing_dir_is_created(tmp_path):
    target = tmp_path / "missing_locales"
    assert not target.exists()
    inst = I18n(locales_dir=str(target))
    assert target.exists()
    assert inst.translations == {}


def test_i18n_load_error_and_missing_files(tmp_path):
    d = tmp_path / "locales"
    d.mkdir()
    (d / "ru.json").write_text("{ this is not json", encoding="utf-8")
    # en/zh files are missing entirely.

    inst = I18n(locales_dir=str(d))
    assert inst.translations.get("en") == {}
    assert inst.translations.get("zh") == {}
    # ru failed to load -> falls back to returning the key
    assert inst.get("unknown_key") == "unknown_key"


def test_i18n_get_fallback_and_format(tmp_path):
    d = tmp_path / "locales"
    d.mkdir()
    (d / "ru.json").write_text(
        json.dumps({"greeting": "Привет {name}", "broken": "Привет {missing}"}),
        encoding="utf-8",
    )
    (d / "en.json").write_text(json.dumps({}), encoding="utf-8")
    (d / "zh.json").write_text(json.dumps({}), encoding="utf-8")

    inst = I18n(locales_dir=str(d))

    # Unknown language falls back to default.
    assert inst.get("greeting", "de", name="X") == "Привет X"
    # Known language, missing key -> fallback to default language value.
    assert inst.get("greeting", "en", name="X") == "Привет X"
    # Formatting error returns the raw text.
    assert inst.get("broken", "ru", other="x") == "Привет {missing}"
    # Key missing everywhere returns the key itself.
    assert inst.get("nope", "ru") == "nope"
    # No kwargs -> raw value returned as-is.
    assert inst.get("greeting", "ru") == "Привет {name}"


# ---------------------------------------------------------------------------
# core.transliteration
# ---------------------------------------------------------------------------


def test_transliterate_none_and_ru_and_latin():
    assert translit_module.transliterate(None) is None
    assert translit_module.transliterate("Привет", "ru") == "Привет"
    assert translit_module.transliterate("Hello", "en") == "Hello"


def test_transliterate_with_cyrtranslit():
    result = translit_module.transliterate("Привет", "en")
    assert result != "Привет"
    assert result.isascii()


def test_transliterate_fallback_when_lib_unavailable(monkeypatch):
    monkeypatch.setattr(translit_module, "CYRTRANSLIT_AVAILABLE", False)
    assert translit_module.transliterate("Привет", "en") == "Privet"


def test_transliterate_exception_falls_back(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(translit_module, "to_latin", _boom)
    assert translit_module.transliterate("Привет", "en") == "Privet"


def test_transliteration_import_error_branch(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "cyrtranslit", None)
    try:
        importlib.reload(translit_module)
        assert translit_module.CYRTRANSLIT_AVAILABLE is False
    finally:
        monkeypatch.undo()
        importlib.reload(translit_module)
    assert translit_module.CYRTRANSLIT_AVAILABLE is True


def test_transliterate_schedule_data():
    data = {
        "Понедельник": [
            {"subject": "Математика", "title": "T", "teacher": "Иванов", "room": "101", "type": "лек", "name": "N"},
            "not-a-dict",
        ],
        "Вторник": "not-a-list",
    }
    result = translit_module.transliterate_schedule_data(data, "en")
    assert len(result) == 2
    assert "Понедельник" not in result
    assert "not-a-dict" in list(result.values())[0]
    assert "not-a-list" in result.values()

    # Russian is returned unchanged (same object).
    assert translit_module.transliterate_schedule_data(data, "ru") is data


def test_get_day_name():
    assert translit_module.get_day_name("Понедельник", "ru") == "Понедельник"
    assert translit_module.get_day_name("Понедельник", "zh") == "星期一"
    assert translit_module.get_day_name("Понедельник", "en") == "Monday"
    # Unknown day -> transliteration fallback.
    assert translit_module.get_day_name("Праздник", "zh") not in (None, "")
    assert translit_module.get_day_name("Праздник", "en") not in (None, "")


# ---------------------------------------------------------------------------
# core.schedule_diff
# ---------------------------------------------------------------------------


def test_compare_day_schedules_all_change_types():
    old = {"lessons": [{"time": "09:00-10:30", "subject": "Математика", "type": "лек", "room": "101", "teachers": "Иванов"}]}
    new = {
        "lessons": [
            {"time": "09:00-10:30", "subject": "Математика", "type": "пр", "room": "202", "teachers": "Петров"},
            {"time": "11:00-12:30", "subject": "Физика", "type": "лек", "room": "303", "teachers": "Сидоров"},
        ]
    }
    changes = ScheduleDiffDetector.compare_day_schedules(old, new)
    types = {c.change_type for c in changes}
    assert ChangeType.LESSON_ADDED in types
    assert ChangeType.ROOM_CHANGED in types
    assert ChangeType.TEACHER_CHANGED in types
    assert ChangeType.TYPE_CHANGED in types


def test_compare_day_schedules_removed_and_errors():
    removed = ScheduleDiffDetector.compare_day_schedules(
        {"lessons": [{"time": "09:00-10:30", "subject": "X", "type": "", "room": "", "teachers": ""}]},
        None,
    )
    assert len(removed) == 1
    assert removed[0].change_type == ChangeType.LESSON_REMOVED

    # new_schedule carries an error -> treated as empty (line: new_lessons = []).
    assert ScheduleDiffDetector.compare_day_schedules(None, {"error": "nope"}) == []
    # old_schedule carries an error -> treated as empty.
    assert ScheduleDiffDetector.compare_day_schedules({"error": "nope"}, None) == []
    # Missing lesson keys use defaults.
    assert ScheduleDiffDetector.compare_day_schedules({}, {}) == []


def test_compare_group_schedules_no_changes():
    diff = ScheduleDiffDetector.compare_group_schedules("G1", date(2026, 9, 1), None, None)
    assert diff.has_changes() is False
    assert diff.group == "G1"


def test_format_change_variants():
    full_added = LessonChange(ChangeType.LESSON_ADDED, "id", "Math", time="09:00", room="101", teacher="Ivanov")
    assert "101" in ScheduleDiffFormatter.format_change(full_added)

    minimal_added = LessonChange(ChangeType.LESSON_ADDED, "id", "Math")
    assert "Math" in ScheduleDiffFormatter.format_change(minimal_added)

    removed = LessonChange(ChangeType.LESSON_REMOVED, "id", "Math", time="09:00")
    assert "09:00" in ScheduleDiffFormatter.format_change(removed)

    removed_min = LessonChange(ChangeType.LESSON_REMOVED, "id", "Math")
    assert "Math" in ScheduleDiffFormatter.format_change(removed_min)

    room = LessonChange(ChangeType.ROOM_CHANGED, "id", "Math", old_value="101", new_value="202", time="09:00")
    assert "202" in ScheduleDiffFormatter.format_change(room)

    empty_values = LessonChange(ChangeType.TIME_CHANGED, "id", "Math", old_value="", new_value="", time=None)
    assert "Math" in ScheduleDiffFormatter.format_change(empty_values)


def test_format_group_diff_branches():
    empty = GroupScheduleDiff("G1", date(2026, 9, 1), [])
    assert ScheduleDiffFormatter.format_group_diff(empty) is None

    # Only SUBJECT_CHANGED: has_changes() is True but no formatter branch -> None.
    subject_only = GroupScheduleDiff("G1", date(2026, 9, 1), [LessonChange(ChangeType.SUBJECT_CHANGED, "id", "Math")])
    assert ScheduleDiffFormatter.format_group_diff(subject_only) is None

    rich = GroupScheduleDiff(
        "G1",
        date(2026, 9, 1),
        [
            LessonChange(ChangeType.LESSON_ADDED, "id", "Math", time="09:00", room="101", teacher="Ivanov"),
            LessonChange(ChangeType.LESSON_REMOVED, "id", "Phys", time="11:00"),
            LessonChange(ChangeType.TIME_CHANGED, "id", "Math", old_value="09:00", new_value="10:00"),
            LessonChange(ChangeType.ROOM_CHANGED, "id", "Math", old_value="101", new_value="202"),
            LessonChange(ChangeType.TEACHER_CHANGED, "id", "Math", old_value="A", new_value="B"),
            LessonChange(ChangeType.TYPE_CHANGED, "id", "Math", old_value="лек", new_value="пр"),
        ],
    )
    message = ScheduleDiffFormatter.format_group_diff(rich)
    assert "Изменения" in message
    assert "Math" in message
