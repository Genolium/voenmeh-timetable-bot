from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from bot import scheduler as sched
from core.db import Base, User
from core.user_data import UserDataManager


@pytest.mark.asyncio
async def test_scheduler_uses_students_only(monkeypatch):
    """The nightly cleanup must never even load teacher profiles."""
    udm = AsyncMock()
    udm.get_students_with_groups.return_value = [(1, "CURRENT")]
    udm.get_all_users_with_groups.side_effect = AssertionError("all users must not be used")
    redis = AsyncMock()
    tm = MagicMock(_schedules={"CURRENT": {}})
    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)

    await sched.handle_graduated_groups(udm, tm, redis)

    udm.get_students_with_groups.assert_awaited_once()
    udm.set_user_group.assert_not_awaited()
    send.send.assert_not_called()


@pytest.mark.asyncio
async def test_single_miss_does_not_clear_group(monkeypatch):
    """A group missing for one snapshot is kept and no user is notified."""
    udm = AsyncMock()
    udm.get_students_with_groups.return_value = [(1, "OLD_GROUP")]
    redis = AsyncMock()
    redis.incr.return_value = 1
    tm = MagicMock(_schedules={"CURRENT": {}})
    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)

    await sched.handle_graduated_groups(udm, tm, redis)

    redis.incr.assert_awaited_once()
    udm.set_user_group.assert_not_awaited()
    send.send.assert_not_called()


@pytest.mark.asyncio
async def test_second_consecutive_miss_clears_group(monkeypatch):
    """Only a repeated miss is treated as a graduation/rename."""
    udm = AsyncMock()
    udm.get_students_with_groups.return_value = [(1, "OLD_GROUP")]
    redis = AsyncMock()
    redis.incr.return_value = 2
    tm = MagicMock(_schedules={"CURRENT": {}})
    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)

    await sched.handle_graduated_groups(udm, tm, redis)

    udm.set_user_group.assert_awaited_once_with(1, None)
    send.send.assert_called_once()
    redis.delete.assert_awaited()


@pytest.mark.asyncio
async def test_existing_group_resets_miss_counter(monkeypatch):
    udm = AsyncMock()
    udm.get_students_with_groups.return_value = [(1, "CURRENT")]
    redis = AsyncMock()
    tm = MagicMock(_schedules={"CURRENT": {}})
    monkeypatch.setattr(sched, "send_message_task", MagicMock())

    await sched.handle_graduated_groups(udm, tm, redis)

    redis.delete.assert_awaited_once()
    udm.set_user_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_students_with_groups_excludes_teachers():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    manager = UserDataManager("sqlite+aiosqlite:///:memory:", redis_url=None)
    manager.engine = engine
    manager.async_session_maker.configure(bind=engine)
    try:
        async with manager.async_session_maker() as session:
            session.add_all(
                [
                    User(user_id=1, group="О735Б", user_type="student"),
                    User(user_id=2, group="Иванов И.И.", user_type="teacher"),
                ]
            )
            await session.commit()

        assert await manager.get_students_with_groups() == [(1, "О735Б")]
        assert await manager.get_students_with_groups_and_language() == [(1, "О735Б", "ru")]
    finally:
        await engine.dispose()
