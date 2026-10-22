"""Coverage tests for DB-backed managers: ``core.feedback_manager`` and
``core.semester_settings``.

Sessions are fully mocked, so no real database is touched.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.db.models import Feedback, SemesterSettings
from core.feedback_manager import FeedbackManager
from core.semester_settings import SemesterSettingsManager

# ---------------------------------------------------------------------------
# Fake SQLAlchemy plumbing
# ---------------------------------------------------------------------------


class _Scalars:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


class _Result:
    def __init__(self, scalar=None, items=None, rowcount=1, rows=None):
        self._scalar = scalar
        self._items = list(items) if items is not None else []
        self.rowcount = rowcount
        self._rows = rows

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _Scalars(self._items)

    def fetchall(self):
        return list(self._rows) if self._rows is not None else list(self._items)


def _make_session(execute_results):
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock(side_effect=list(execute_results))
    return session


def _make_factory(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


# ---------------------------------------------------------------------------
# core.feedback_manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_create():
    session = _make_session([])
    manager = FeedbackManager(_make_factory(session))
    feedback = await manager.create_feedback(1, "user", "Full Name", "hello", "text", None)
    assert isinstance(feedback, Feedback)
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_feedback_list_filters():
    session = _make_session([_Result(scalar=5), _Result(items=[Feedback(id=1)])])
    manager = FeedbackManager(_make_factory(session))
    feedbacks, total = await manager.list_feedback(only_unanswered=True, user_id=7)
    assert total == 5
    assert len(feedbacks) == 1

    session = _make_session([_Result(scalar=2), _Result(items=[])])
    manager = FeedbackManager(_make_factory(session))
    feedbacks, total = await manager.list_feedback(only_unanswered=False, limit=1, offset=2)
    assert total == 2

    session = _make_session([_Result(scalar=None), _Result(items=[])])
    manager = FeedbackManager(_make_factory(session))
    feedbacks, total = await manager.list_feedback()
    assert total == 0


@pytest.mark.asyncio
async def test_feedback_get():
    session = _make_session([_Result(scalar=Feedback(id=3))])
    manager = FeedbackManager(_make_factory(session))
    assert (await manager.get_feedback(3)).id == 3

    session = _make_session([_Result(scalar=None)])
    manager = FeedbackManager(_make_factory(session))
    assert await manager.get_feedback(4) is None


@pytest.mark.asyncio
async def test_feedback_answer_success_and_failure():
    session = _make_session([_Result(rowcount=1)])
    manager = FeedbackManager(_make_factory(session))
    assert await manager.answer_feedback(1, 99, "response") is True

    session = _make_session([_Result(rowcount=0)])
    manager = FeedbackManager(_make_factory(session))
    assert await manager.answer_feedback(2, 99, "response") is False


@pytest.mark.asyncio
async def test_feedback_delete():
    session = _make_session([_Result(rowcount=1)])
    manager = FeedbackManager(_make_factory(session))
    assert await manager.delete_feedback(1) is True

    session = _make_session([_Result(rowcount=0)])
    manager = FeedbackManager(_make_factory(session))
    assert await manager.delete_feedback(2) is False


@pytest.mark.asyncio
async def test_feedback_stats():
    session = _make_session(
        [
            _Result(items=[1, 2, 3]),
            _Result(items=[1]),
            _Result(items=[1, 2]),
        ]
    )
    manager = FeedbackManager(_make_factory(session))
    stats = await manager.get_feedback_stats()
    assert stats == {"total": 3, "unanswered": 1, "answered": 2, "recent_7_days": 2}


# ---------------------------------------------------------------------------
# core.semester_settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semester_settings_get_found_and_missing():
    settings = SemesterSettings(
        id=1,
        fall_semester_start=date(2026, 9, 1),
        spring_semester_start=date(2027, 2, 9),
        updated_by=1,
    )
    session = _make_session([_Result(scalar=settings)])
    manager = SemesterSettingsManager(_make_factory(session))
    assert await manager.get_semester_settings() == (date(2026, 9, 1), date(2027, 2, 9))

    session = _make_session([_Result(scalar=None)])
    manager = SemesterSettingsManager(_make_factory(session))
    assert await manager.get_semester_settings() is None


@pytest.mark.asyncio
async def test_semester_settings_update_existing_and_new():
    existing = SemesterSettings(
        id=1, fall_semester_start=date(2026, 9, 1), spring_semester_start=date(2027, 2, 9), updated_by=1
    )
    session = _make_session([_Result(scalar=existing), _Result()])
    manager = SemesterSettingsManager(_make_factory(session))
    assert await manager.update_semester_settings(date(2026, 9, 2), date(2027, 2, 10), 5) is True
    session.commit.assert_awaited()

    session = _make_session([_Result(scalar=None), _Result()])
    manager = SemesterSettingsManager(_make_factory(session))
    assert await manager.update_semester_settings(date(2026, 9, 2), date(2027, 2, 10), 5) is True
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_semester_settings_update_error():
    factory = MagicMock(side_effect=RuntimeError("db down"))
    manager = SemesterSettingsManager(factory)
    assert await manager.update_semester_settings(date(2026, 9, 2), date(2027, 2, 10), 5) is False


@pytest.mark.asyncio
async def test_semester_settings_formatted():
    settings = SemesterSettings(
        id=1,
        fall_semester_start=date(2026, 9, 1),
        spring_semester_start=date(2027, 2, 9),
        updated_by=1,
    )
    session = _make_session([_Result(scalar=settings)])
    manager = SemesterSettingsManager(_make_factory(session))
    text = await manager.get_formatted_settings()
    assert "01.09.2026" in text

    session = _make_session([_Result(scalar=None)])
    manager = SemesterSettingsManager(_make_factory(session))
    text = await manager.get_formatted_settings()
    assert "не установлены" in text
