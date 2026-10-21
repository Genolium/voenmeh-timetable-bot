import inspect
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from bot import scheduler as sched
from bot.dialogs import schedule_view as sv
from bot.dialogs.states import Schedule
from core.config import MOSCOW_TZ
from core.db import Base, User
from core.user_data import UserDataManager


def test_teacher_sees_schedule_actions():
    source = inspect.getsource(sv)
    assert 'data.get("user_type") != "teacher"' not in source


def test_full_quality_gate_window_exists():
    states = {window.state for window in sv.schedul_dialog_windows}
    assert Schedule.full_quality_gate in states


@pytest.mark.asyncio
async def test_teacher_reminders_are_selected_with_type():
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
                    User(user_id=1, group="О735Б", user_type="student", lesson_reminders=True),
                    User(user_id=2, group="Иванов И.И.", user_type="teacher", lesson_reminders=True),
                ]
            )
            await session.commit()

        rows = await manager.get_users_for_lesson_reminders_with_type()
        assert (2, "Иванов И.И.", 20, "ru", "teacher") in rows
        assert any(row[0] == 1 and row[4] == "student" for row in rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_minutely_reminders_use_teacher_schedule(monkeypatch):
    now = datetime.now(MOSCOW_TZ).replace(hour=8, minute=40, second=0, microsecond=0)
    monkeypatch.setattr(
        "bot.scheduler.datetime",
        MagicMock(now=lambda tz: now, strptime=datetime.strptime, combine=datetime.combine),
    )
    udm = AsyncMock()
    udm.get_users_for_lesson_reminders_with_type.return_value = [(2, "Иванов И.И.", 20, "ru", "teacher")]
    tm = MagicMock()
    tm.get_teacher_schedule = AsyncMock(
        return_value={
            "date": now.date(),
            "lessons": [{"time": "09:00-10:30", "start_time_raw": "09:00", "end_time_raw": "10:30"}],
        }
    )

    with patch("bot.scheduler.send_lesson_reminder_task") as task:
        await sched.minutely_reminders_checker(udm, tm)

    tm.get_teacher_schedule.assert_awaited_once()
    task.send.assert_called_once()
