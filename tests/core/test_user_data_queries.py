import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from core.db import Base
from core.user_data import UserDataManager


@pytest.fixture
async def manager_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    manager = UserDataManager("sqlite+aiosqlite:///:memory:")
    manager.engine = engine
    manager.async_session_maker.configure(bind=engine)
    try:
        yield manager
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_users_for_evening_morning_and_lesson_reminders(manager_db: UserDataManager):
    # по умолчанию все подписки включены и reminder_time_minutes = 20
    await manager_db.register_user(1, "u1"); await manager_db.set_user_group(1, "G")
    await manager_db.register_user(2, "u2"); await manager_db.set_user_group(2, "G")
    # отключим утреннюю сводку для юзера 2
    await manager_db.update_setting(2, "morning_summary", False)

    ev = await manager_db.get_users_for_evening_notify()
    mrn = await manager_db.get_users_for_morning_summary()
    rem = await manager_db.get_users_for_lesson_reminders()

    assert sorted(ev) == [(1, 'G'), (2, 'G')]
    assert mrn == [(1, 'G')]
    assert rem == [(1, 'G', 20), (2, 'G', 20)]


