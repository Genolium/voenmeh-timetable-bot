import pytest
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine

from core.db import Base, User
from core.user_data import UserDataManager

# Используем асинхронную фикстуру, чтобы база данных создавалась для каждого теста
@pytest.fixture
async def manager_with_db():
    """
    Создает реальный UserDataManager с in-memory SQLite базой данных для тестов.
    Таблицы создаются и удаляются для каждого теста.
    """
    # Создаем один движок для всех операций в рамках теста
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    
    # Создаем все таблицы, используя этот движок
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Создаем экземпляр менеджера. Он внутри создаст свой движок, но мы его заменим
    user_data_manager = UserDataManager(db_url="sqlite+aiosqlite:///:memory:")
    user_data_manager.engine = engine
    user_data_manager.async_session_maker.configure(bind=engine)


    yield user_data_manager

    # Удаляем все таблицы после теста
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    # Закрываем соединение
    await engine.dispose()


@pytest.mark.asyncio
class TestUserDataManagerWithSQLAlchemy:

    async def test_register_new_user(self, manager_with_db: UserDataManager):
        # Act
        await manager_with_db.register_user(123, "testuser")
        
        # Assert
        async with manager_with_db.async_session_maker() as session:
            user = await session.get(User, 123)
            assert user is not None
            assert user.user_id == 123
            assert user.username == "testuser"

    async def test_register_existing_user_updates_date(self, manager_with_db: UserDataManager):
        # Arrange: создаем пользователя
        await manager_with_db.register_user(123, "testuser")
        async with manager_with_db.async_session_maker() as session:
            user_before = await session.get(User, 123)
            first_active_date = user_before.last_active_date
        
        # Act: вызываем регистрацию снова
        await manager_with_db.register_user(123, "testuser_new_name")
        
        # Assert: проверяем, что дата обновилась
        async with manager_with_db.async_session_maker() as session:
            user_after = await session.get(User, 123)
            assert user_after.last_active_date > first_active_date
            # Имя не должно меняться, так как мы сначала проверяем наличие пользователя
            assert user_after.username == "testuser"

    async def test_set_and_get_user_group(self, manager_with_db: UserDataManager):
        # Arrange
        await manager_with_db.register_user(123, "testuser")
        
        # Act
        await manager_with_db.set_user_group(123, "О735Б")
        
        # Assert
        group = await manager_with_db.get_user_group(123)
        assert group == "О735Б"
    
    async def test_get_user_settings(self, manager_with_db: UserDataManager):
        # Arrange
        await manager_with_db.register_user(456, "settings_user")
        
        # Act & Assert 1: Проверяем настройки по умолчанию
        settings = await manager_with_db.get_user_settings(456)
        assert settings == {
            "evening_notify": True,
            "morning_summary": True,
            "lesson_reminders": True
        }
        
        # Act & Assert 2: Обновляем настройку и проверяем снова
        await manager_with_db.update_setting(456, "morning_summary", False)
        new_settings = await manager_with_db.get_user_settings(456)
        assert new_settings["morning_summary"] is False
        assert new_settings["evening_notify"] is True

    async def test_get_top_groups(self, manager_with_db: UserDataManager):
        # Arrange
        await manager_with_db.register_user(1, "u1")
        await manager_with_db.set_user_group(1, "GROUP_A")
        await manager_with_db.register_user(2, "u2")
        await manager_with_db.set_user_group(2, "GROUP_B")
        await manager_with_db.register_user(3, "u3")
        await manager_with_db.set_user_group(3, "GROUP_A")
        
        # Act
        top_groups = await manager_with_db.get_top_groups(limit=5)
        
        # Assert
        assert len(top_groups) == 2
        assert top_groups[0] == ("GROUP_A", 2)
        assert top_groups[1] == ("GROUP_B", 1)