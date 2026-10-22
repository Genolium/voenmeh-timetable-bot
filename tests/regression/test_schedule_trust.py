from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.manager import TimetableManager
from core.schedule_diff import ChangeType, GroupScheduleDiff, LessonChange, ScheduleDiffFormatter


def _manager():
    data = {
        "__metadata__": {},
        "__teachers_index__": {},
        "__classrooms_index__": {},
        "G1": {"odd": {}, "even": {"Среда": [{"time": "09:00-10:30", "subject": "Math"}]}},
    }
    return TimetableManager(data, MagicMock())


@pytest.mark.asyncio
async def test_inactive_semester_returns_no_lessons():
    result = await _manager().get_schedule_for_day("G1", date(2026, 7, 15))
    assert result is not None
    assert result["lessons"] == []
    assert result["week_number"] == 0


@pytest.mark.asyncio
async def test_semester_settings_error_falls_back_to_defaults():
    manager = _manager()
    manager._semester_settings_manager = MagicMock()
    manager._semester_settings_manager.get_semester_settings = AsyncMock(side_effect=RuntimeError("db down"))
    info = await manager.get_semester_info_for(date(2026, 10, 22))
    assert info.semester in {"fall", "spring"}


@pytest.mark.asyncio
async def test_override_read_error_does_not_break_schedule():
    manager = _manager()
    manager.redis = MagicMock()
    with patch("core.schedule_overrides.get_group_override", side_effect=RuntimeError("redis down")):
        result = await manager.get_schedule_for_day("G1", date(2026, 10, 22))
    assert result is not None
    assert "lessons" in result


def test_group_search_helpers():
    manager = _manager()
    assert manager.find_groups("G") == ["G1"]
    assert manager.find_groups("") == []
    assert manager.find_groups_fuzzy("G1") == ["G1"]
    assert manager.find_groups_fuzzy("x") == []


def test_diff_formatter_is_localized():
    diff = GroupScheduleDiff(
        group="G1",
        date=date(2026, 10, 22),
        changes=[
            LessonChange(
                change_type=ChangeType.LESSON_ADDED,
                lesson_id="1",
                subject="Math",
                time="09:00-10:30",
                room="101",
                teacher="Ivanov",
            )
        ],
    )
    text = ScheduleDiffFormatter.format_group_diff(diff, lang="en")
    assert "Schedule changes for G1" in text
    assert "Lesson added" in text
    assert "Добавлена" not in text
