"""Coverage tests for ``core.audit_manager``."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.audit_manager import AuditManager
from core.schedule_diff import ChangeType, LessonChange


@pytest.fixture
def session_factory():
    session = AsyncMock()
    session.add = MagicMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session


def _change(change_type=ChangeType.ROOM_CHANGED, subject="Математика"):
    return LessonChange(
        change_type=change_type,
        lesson_id="09:00_Math",
        subject=subject,
        old_value="314",
        new_value="315",
        time="09:00 - 10:30",
    )


async def test_record_changes_empty(session_factory):
    factory, session = session_factory
    manager = AuditManager(factory)
    assert await manager.record_changes("G", date(2026, 9, 1), []) == 0
    session.add.assert_not_called()


async def test_record_changes_success(session_factory):
    factory, session = session_factory
    manager = AuditManager(factory)
    saved = await manager.record_changes("G", date(2026, 9, 1), [_change(), _change()], target_type="teacher")
    assert saved == 2
    assert session.add.call_count == 2
    session.commit.assert_awaited_once()


async def test_record_changes_plain_string_change_type(session_factory):
    factory, session = session_factory
    manager = AuditManager(factory)
    change = LessonChange(change_type="custom_type", lesson_id="id", subject="", time=None)
    saved = await manager.record_changes("G", date(2026, 9, 1), [change])
    assert saved == 1


async def test_record_changes_exception(session_factory):
    factory, session = session_factory
    session.commit.side_effect = Exception("db down")
    manager = AuditManager(factory)
    assert await manager.record_changes("G", date(2026, 9, 1), [_change()]) == 0


async def test_get_recent_changes(session_factory):
    factory, session = session_factory
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["a", "b"]
    session.execute = AsyncMock(return_value=result)
    manager = AuditManager(factory)
    assert await manager.get_recent_changes() == ["a", "b"]
    assert await manager.get_recent_changes(target_name="G", limit=5, days=3) == ["a", "b"]


async def test_get_frequently_changing_targets(session_factory):
    factory, session = session_factory
    result = MagicMock()
    result.all.return_value = [("G1", 10), ("G2", 5)]
    session.execute = AsyncMock(return_value=result)
    manager = AuditManager(factory)
    assert await manager.get_frequently_changing_targets() == [("G1", 10), ("G2", 5)]
