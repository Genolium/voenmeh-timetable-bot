from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.audit_manager import AuditManager
from core.schedule_diff import ChangeType, LessonChange


@pytest.fixture
def mock_session_factory():
    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    session_factory.return_value.__aexit__.return_value = None
    return session_factory, session


@pytest.mark.asyncio
async def test_record_changes_success(mock_session_factory):
    session_factory, session = mock_session_factory
    manager = AuditManager(session_factory)

    changes = [
        LessonChange(
            change_type=ChangeType.ROOM_CHANGED,
            lesson_id="09:00_Math",
            subject="Математика",
            old_value="314",
            new_value="315",
            time="09:00 - 10:30",
        ),
        LessonChange(
            change_type=ChangeType.LESSON_ADDED,
            lesson_id="10:45_Physics",
            subject="Физика",
            new_value="Лекция",
            time="10:45 - 12:15",
        ),
    ]

    saved = await manager.record_changes("О735Б", date(2026, 9, 1), changes)

    assert saved == 2
    assert session.add.call_count == 2
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_record_changes_empty(mock_session_factory):
    session_factory, session = mock_session_factory
    manager = AuditManager(session_factory)

    saved = await manager.record_changes("О735Б", date(2026, 9, 1), [])
    assert saved == 0
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_record_changes_exception(mock_session_factory):
    session_factory, session = mock_session_factory
    session.commit.side_effect = Exception("DB error")
    manager = AuditManager(session_factory)

    changes = [
        LessonChange(
            change_type=ChangeType.LESSON_REMOVED,
            lesson_id="09:00_Math",
            subject="Математика",
        )
    ]

    saved = await manager.record_changes("О735Б", date(2026, 9, 1), changes)
    assert saved == 0
