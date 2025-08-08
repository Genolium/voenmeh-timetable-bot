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
async def test_get_user_settings_for_unknown_user_returns_defaults(manager_db: UserDataManager):
    settings = await manager_db.get_user_settings(999)
    assert settings == {
        "evening_notify": False,
        "morning_summary": False,
        "lesson_reminders": False,
        "reminder_time_minutes": 20,
    }


@pytest.mark.asyncio
async def test_update_setting_invalid_name_is_ignored(manager_db: UserDataManager):
    await manager_db.register_user(1, "u1")
    # недопустимое имя настройки — должно игнорироваться без исключений
    await manager_db.update_setting(1, "invalid_toggle", False)
    settings = await manager_db.get_user_settings(1)
    # дефолтные значения не изменились
    assert settings["evening_notify"] is True
    assert settings["morning_summary"] is True
    assert settings["lesson_reminders"] is True


@pytest.mark.asyncio
async def test_set_reminder_time_and_retrieve(manager_db: UserDataManager):
    await manager_db.register_user(2, "u2")
    await manager_db.set_user_group(2, "G")
    await manager_db.set_reminder_time(2, 45)
    reminders = await manager_db.get_users_for_lesson_reminders()
    assert (2, "G", 45) in reminders


@pytest.mark.asyncio
async def test_counts_methods(manager_db: UserDataManager):
    await manager_db.register_user(10, "a"); await manager_db.set_user_group(10, "X")
    await manager_db.register_user(11, "b"); await manager_db.set_user_group(11, "Y")

    total = await manager_db.get_total_users_count()
    assert total == 2

    new_100 = await manager_db.get_new_users_count(100)
    assert new_100 == 2

    active_1 = await manager_db.get_active_users_by_period(1)
    assert active_1 == 2

    subscribed = await manager_db.get_subscribed_users_count()
    assert subscribed == 2  # по умолчанию все подписки включены

    ids = await manager_db.get_all_user_ids()
    assert sorted(ids) == [10, 11]


