import pytest
from datetime import datetime, UTC

from sqlalchemy.ext.asyncio import create_async_engine

from core.db import Base, User
from core.user_data import UserDataManager

@pytest.fixture
async def manager_with_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    user_data_manager = UserDataManager(db_url="sqlite+aiosqlite:///:memory:")
    user_data_manager.engine = engine
    user_data_manager.async_session_maker.configure(bind=engine)
    yield user_data_manager
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
class TestUserDataManagerWithSQLAlchemy:

    async def test_register_new_user(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(123, "testuser")
        
        async with manager_with_db.async_session_maker() as session:
            user = await session.get(User, 123)
            assert user is not None
            assert user.user_id == 123
            assert user.username == "testuser"

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

    async def test_set_and_get_user_group(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(123, "testuser")
        await manager_with_db.set_user_group(123, "О735Б")
        group = await manager_with_db.get_user_group(123)
        assert group == "О735Б"
    
    async def test_get_user_settings(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(456, "settings_user")
        
        settings = await manager_with_db.get_user_settings(456)
        assert settings == {
            "evening_notify": True,
            "morning_summary": True,
            "lesson_reminders": True,
            "reminder_time_minutes": 20
        }
        
        await manager_with_db.update_setting(456, "morning_summary", False)
        new_settings = await manager_with_db.get_user_settings(456)
        assert new_settings["morning_summary"] is False
        assert new_settings["evening_notify"] is True

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

    async def test_get_full_user_info(self, manager_with_db: UserDataManager):
        await manager_with_db.register_user(777, "full_info_user")
        user_info = await manager_with_db.get_full_user_info(777)
        assert user_info is not None
        assert user_info.user_id == 777
        assert user_info.username == "full_info_user"

        not_found_user = await manager_with_db.get_full_user_info(12345)
        assert not_found_user is None

    async def test_get_subscription_stats(self, manager_with_db: UserDataManager):
        # Пользователь 1: подписан на все
        await manager_with_db.register_user(1, "u1")
        # Пользователь 2: подписан только на вечер
        await manager_with_db.register_user(2, "u2")
        await manager_with_db.update_setting(2, "morning_summary", False)
        await manager_with_db.update_setting(2, "lesson_reminders", False)
        # Пользователь 3: отписан от всего
        await manager_with_db.register_user(3, "u3")
        await manager_with_db.update_setting(3, "evening_notify", False)
        await manager_with_db.update_setting(3, "morning_summary", False)
        await manager_with_db.update_setting(3, "lesson_reminders", False)
        # Пользователь 4: без подписок
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

    async def test_get_group_distribution(self, manager_with_db: UserDataManager):
        # Группа A: 3 студента
        await manager_with_db.register_user(1, "u1"); await manager_with_db.set_user_group(1, "A")
        await manager_with_db.register_user(2, "u2"); await manager_with_db.set_user_group(2, "A")
        await manager_with_db.register_user(3, "u3"); await manager_with_db.set_user_group(3, "A")
        # Группа B: 1 студент
        await manager_with_db.register_user(4, "u4"); await manager_with_db.set_user_group(4, "B")
        # Группа C: 7 студентов
        for i in range(5, 12):
            await manager_with_db.register_user(i, f"u{i}"); await manager_with_db.set_user_group(i, "C")
        
        dist = await manager_with_db.get_group_distribution()
        
        assert dist["1 студент"] == 1
        assert dist["2-5 студентов"] == 1
        assert dist["6-10 студентов"] == 1