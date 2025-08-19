import pytest
from core.user_data import UserDataManager
from core.db import Base, User
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def user_data_manager():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    manager = UserDataManager(TEST_DB_URL)
    manager.engine = engine
    manager.async_session_maker.configure(bind=engine)
    try:
        yield manager
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

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

@pytest.fixture
async def manager_with_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    user_data_manager = UserDataManager(db_url="sqlite+aiosqlite:///:memory:")
    user_data_manager.engine = engine
    user_data_manager.async_session_maker.configure(bind=engine)
    try:
        yield user_data_manager
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

@pytest.mark.asyncio
async def test_register_user_and_get_group(user_data_manager):
    await user_data_manager.register_user(123, "testuser")
    group = await user_data_manager.get_user_group(123)
    assert group is None

    await user_data_manager.set_user_group(123, "О735Б")
    group = await user_data_manager.get_user_group(123)
    assert group == "О735Б"

@pytest.mark.asyncio
async def test_get_user_settings_for_unknown_user_returns_defaults(manager_db: UserDataManager):
    settings = await manager_db.get_user_settings(999)
    assert settings == {
        "evening_notify": False,
        "morning_summary": False,
        "lesson_reminders": False,
        "reminder_time_minutes": 60,
    }

@pytest.mark.asyncio
async def test_update_setting_invalid_name_is_ignored(manager_db: UserDataManager):
    await manager_db.register_user(1, "u1")
    await manager_db.update_setting(1, "invalid_toggle", False)
    settings = await manager_db.get_user_settings(1)
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
    assert subscribed == 2
    ids = await manager_db.get_all_user_ids()
    assert sorted(ids) == [10, 11]

@pytest.mark.asyncio
async def test_users_for_evening_morning_and_lesson_reminders(manager_db: UserDataManager):
    await manager_db.register_user(1, "u1"); await manager_db.set_user_group(1, "G")
    await manager_db.register_user(2, "u2"); await manager_db.set_user_group(2, "G")
    await manager_db.update_setting(2, "morning_summary", False)
    ev = await manager_db.get_users_for_evening_notify()
    mrn = await manager_db.get_users_for_morning_summary()
    rem = await manager_db.get_users_for_lesson_reminders()
    assert sorted(ev) == [(1, 'G'), (2, 'G')]
    assert mrn == [(1, 'G')]
    assert rem == [(1, 'G', 20), (2, 'G', 20)]

class TestUserDataManagerWithSQLAlchemy:
    @pytest.mark.asyncio
    async def test_register_new_user(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(123, "testuser")
        async with manager_with_db.async_session_maker() as session:
            user = await session.get(User, 123)
            assert user is not None
            assert user.user_id == 123
            assert user.username == "testuser"

    @pytest.mark.asyncio
    async def test_register_existing_user_updates_date(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(123, "testuser")
        async with manager_with_db.async_session_maker() as session:
            user_before = await session.get(User, 123)
            first_active_date = user_before.last_active_date
        await manager_with_db.register_user(123, "testuser_new_name")
        async with manager_with_db.async_session_maker() as session:
            user_after = await session.get(User, 123)
            assert user_after.last_active_date > first_active_date
            assert user_after.username == "testuser"

    @pytest.mark.asyncio
    async def test_set_and_get_user_group(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(123, "testuser")
        await manager_with_db.set_user_group(123, "О735Б")
        group = await manager_with_db.get_user_group(123)
        assert group == "О735Б"

    @pytest.mark.asyncio
    async def test_get_user_settings(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(456, "settings_user")
        settings = await manager_with_db.get_user_settings(456)
        assert settings == {
            "evening_notify": True,
            "morning_summary": True,
            "lesson_reminders": True,
            "reminder_time_minutes": 60
        }
        await manager_with_db.update_setting(456, "morning_summary", False)
        new_settings = await manager_with_db.get_user_settings(456)
        assert new_settings["morning_summary"] is False
        assert new_settings["evening_notify"] is True

    @pytest.mark.asyncio
    async def test_get_top_groups(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(1, "u1")
        await manager_with_db.set_user_group(1, "GROUP_A")
        await manager_with_db.register_user(2, "u2")
        await manager_with_db.set_user_group(2, "GROUP_B")
        await manager_with_db.register_user(3, "u3")
        await manager_with_db.set_user_group(3, "GROUP_A")
        top_groups = await manager_with_db.get_top_groups(limit=5)
        assert len(top_groups) == 2
        assert top_groups[0] == ("GROUP_A", 2)
        assert top_groups[1] == ("GROUP_B", 1)

    @pytest.mark.asyncio
    async def test_get_full_user_info(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(777, "full_info_user")
        user_info = await manager_with_db.get_full_user_info(777)
        assert user_info is not None
        assert user_info.user_id == 777
        assert user_info.username == "full_info_user"
        not_found_user = await manager_with_db.get_full_user_info(12345)
        assert not_found_user is None

    @pytest.mark.asyncio
    async def test_get_subscription_stats(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(1, "u1")
        await manager_with_db.register_user(2, "u2")
        await manager_with_db.update_setting(2, "morning_summary", False)
        await manager_with_db.update_setting(2, "lesson_reminders", False)
        await manager_with_db.register_user(3, "u3")
        await manager_with_db.update_setting(3, "evening_notify", False)
        await manager_with_db.update_setting(3, "morning_summary", False)
        await manager_with_db.update_setting(3, "lesson_reminders", False)
        await manager_with_db.register_user(4, "u4")
        await manager_with_db.update_setting(4, "evening_notify", False)
        await manager_with_db.update_setting(4, "morning_summary", False)
        await manager_with_db.update_setting(4, "lesson_reminders", False)
        unsubscribed = await manager_with_db.get_unsubscribed_count()
        assert unsubscribed == 2
        breakdown = await manager_with_db.get_subscription_breakdown()
        assert breakdown['evening'] == 2
        assert breakdown['morning'] == 1
        assert breakdown['reminders'] == 1

    @pytest.mark.asyncio
    async def test_get_group_distribution(self, manager_with_db: UserDataManager):
        for i in range(1, 4):
            await manager_with_db.register_user(i, f"u{i}"); await manager_with_db.set_user_group(i, "A")
        await manager_with_db.register_user(4, "u4"); await manager_with_db.set_user_group(4, "B")
        for i in range(5, 12):
            await manager_with_db.register_user(i, f"u{i}"); await manager_with_db.set_user_group(i, "C")
        dist = await manager_with_db.get_group_distribution()
        assert dist["1 студент"] == 1
        assert dist["2-5 студентов"] == 1
        assert dist["6-10 студентов"] == 1