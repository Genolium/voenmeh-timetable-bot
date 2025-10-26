import pytest
from unittest.mock import AsyncMock, MagicMock
from core.user_data import UserDataManager, cached
from core.db import Base, User
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def user_data_manager():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    manager = UserDataManager(TEST_DB_URL, redis_url=None)
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
    manager = UserDataManager("sqlite+aiosqlite:///:memory:", redis_url=None)
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
    user_data_manager = UserDataManager(db_url="sqlite+aiosqlite:///:memory:", redis_url=None)
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


class TestUserDataManagerCaching:
    """Тесты для кэширования в UserDataManager."""

    @pytest.mark.asyncio
    async def test_cached_decorator_basic(self, manager_with_db):
        """Тест базовой работы декоратора кэширования."""
        # Создаем мок Redis клиента
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # Кэш пустой
        mock_redis.setex = AsyncMock()
        manager_with_db._redis_client = mock_redis

        # Создаем тестовую функцию с декоратором
        @cached(ttl=60)
        async def test_function(self, x: int) -> int:
            return x * 2

        # Вызываем функцию
        result = await test_function(manager_with_db, 5)

        # Проверяем, что функция выполнилась
        assert result == 10

        # Проверяем, что кэш был проверен и сохранен
        mock_redis.get.assert_called_once()
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_cached_decorator_cache_hit(self, manager_with_db):
        """Тест попадания в кэш."""
        import json

        # Создаем мок Redis клиента
        mock_redis = AsyncMock()
        cached_data = json.dumps([1, 2, 3]).encode('utf-8')
        mock_redis.get.return_value = cached_data
        mock_redis.setex = AsyncMock()
        manager_with_db._redis_client = mock_redis

        # Создаем тестовую функцию с декоратором
        @cached(ttl=60)
        async def test_function(self, x: int) -> list:
            return [x, x+1, x+2]  # Эта строка не должна выполниться

        # Вызываем функцию
        result = await test_function(manager_with_db, 1)

        # Проверяем, что результат взят из кэша
        assert result == [1, 2, 3]

        # Проверяем, что setex НЕ был вызван (не сохраняем в кэш)
        mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_clear_user_cache(self, manager_with_db):
        """Тест очистки кэша пользователей."""
        # Создаем мок Redis клиента
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock()
        mock_redis.delete = AsyncMock()
        manager_with_db._redis_client = mock_redis

        # Настраиваем scan для возврата ключей
        mock_redis.scan.side_effect = [
            (0, [b'cache:get_users_for_evening_notify:123', b'cache:get_users_for_morning_summary:456']),
            (0, [])  # Конец сканирования
        ]

        # Вызываем очистку кэша
        await manager_with_db.clear_user_cache()

        # Проверяем, что ключи были удалены
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_user_cache_no_redis(self, manager_with_db):
        """Тест очистки кэша без Redis клиента."""
        manager_with_db._redis_client = None

        # Не должно падать
        await manager_with_db.clear_user_cache()

        # Ничего не должно быть вызвано
        assert True

    @pytest.mark.asyncio
    async def test_get_redis_client_from_config(self, manager_with_db):
        """Тест получения Redis клиента из конфигурации."""
        # Убираем текущий Redis клиент
        manager_with_db._redis_client = None
        manager_with_db._redis_url = None

        # Мокаем get_redis_client
        with pytest.mock.patch('core.config.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            result = await manager_with_db._get_redis_client()

            assert result == mock_redis
            mock_get_redis.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_user_update(self, manager_with_db):
        """Тест инвалидации кэша при обновлении данных пользователя."""
        # Создаем мок Redis клиента
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock()
        mock_redis.delete = AsyncMock()
        manager_with_db._redis_client = mock_redis

        # Настраиваем scan для возврата ключей
        mock_redis.scan.side_effect = [
            (0, [b'cache:get_users_for_evening_notify:123']),
            (0, [])  # Конец сканирования
        ]

        # Регистрируем пользователя
        await manager_with_db.register_user(123, "testuser")

        # Проверяем, что кэш был очищен
        mock_redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_group_update(self, manager_with_db):
        """Тест инвалидации кэша при обновлении группы пользователя."""
        # Создаем мок Redis клиента
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock()
        mock_redis.delete = AsyncMock()
        manager_with_db._redis_client = mock_redis

        # Настраиваем scan для возврата ключей
        mock_redis.scan.side_effect = [
            (0, [b'cache:get_users_for_morning_summary:456']),
            (0, [])  # Конец сканирования
        ]

        # Регистрируем и устанавливаем группу
        await manager_with_db.register_user(123, "testuser")
        await manager_with_db.set_user_group(123, "О735Б")

        # Проверяем, что кэш был очищен
        mock_redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_settings_update(self, manager_with_db):
        """Тест инвалидации кэша при обновлении настроек пользователя."""
        # Создаем мок Redis клиента
        mock_redis = AsyncMock()
        mock_redis.scan = AsyncMock()
        mock_redis.delete = AsyncMock()
        manager_with_db._redis_client = mock_redis

        # Настраиваем scan для возврата ключей
        mock_redis.scan.side_effect = [
            (0, [b'cache:get_users_for_lesson_reminders:789']),
            (0, [])  # Конец сканирования
        ]

        # Регистрируем пользователя и обновляем настройки
        await manager_with_db.register_user(123, "testuser")
        await manager_with_db.update_setting(123, "lesson_reminders", True)

        # Проверяем, что кэш был очищен
        mock_redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_cache_methods_with_redis_url(self):
        """Тест создания UserDataManager с Redis URL."""
        manager = UserDataManager("sqlite+aiosqlite:///:memory:", redis_url="redis://localhost:6379")

        # Проверяем, что Redis URL сохранен
        assert manager._redis_url == "redis://localhost:6379"
        assert manager._redis_client is None  # Должен быть None до первого вызова

    @pytest.mark.asyncio
    async def test_get_user_theme_default(self, user_data_manager):
        """Тест получения темы пользователя по умолчанию."""
        # Регистрируем пользователя
        await user_data_manager.register_user(12345, "test_user")

        # Получаем тему (должна быть standard по умолчанию)
        theme = await user_data_manager.get_user_theme(12345)
        assert theme == "standard"

    @pytest.mark.asyncio
    async def test_get_user_theme_nonexistent_user(self, user_data_manager):
        """Тест получения темы несуществующего пользователя."""
        # Получаем тему несуществующего пользователя (должна быть standard по умолчанию)
        theme = await user_data_manager.get_user_theme(99999)
        assert theme == "standard"

    @pytest.mark.asyncio
    async def test_set_user_theme_valid(self, user_data_manager):
        """Тест установки валидной темы."""
        # Регистрируем пользователя
        await user_data_manager.register_user(12345, "test_user")

        # Устанавливаем тему
        await user_data_manager.set_user_theme(12345, "light")

        # Проверяем, что тема установилась
        theme = await user_data_manager.get_user_theme(12345)
        assert theme == "light"

    @pytest.mark.asyncio
    async def test_set_user_theme_invalid(self, user_data_manager):
        """Тест установки невалидной темы."""
        # Регистрируем пользователя
        await user_data_manager.register_user(12345, "test_user")

        # Пытаемся установить невалидную тему (должна установиться standard)
        await user_data_manager.set_user_theme(12345, "invalid_theme")

        # Проверяем, что установилась standard
        theme = await user_data_manager.get_user_theme(12345)
        assert theme == "standard"

    @pytest.mark.asyncio
    async def test_set_user_theme_all_valid(self, user_data_manager):
        """Тест установки всех валидных тем."""
        # Регистрируем пользователя
        await user_data_manager.register_user(12345, "test_user")

        valid_themes = ["standard", "light", "dark", "classic", "coffee"]

        for theme in valid_themes:
            # Устанавливаем тему
            await user_data_manager.set_user_theme(12345, theme)

            # Проверяем, что тема установилась
            current_theme = await user_data_manager.get_user_theme(12345)
            assert current_theme == theme

    @pytest.mark.asyncio
    async def test_get_user_settings_includes_theme(self, user_data_manager):
        """Тест получения настроек пользователя включает тему."""
        # Регистрируем пользователя
        await user_data_manager.register_user(12345, "test_user")

        # Устанавливаем тему
        await user_data_manager.set_user_theme(12345, "dark")

        # Получаем настройки
        settings = await user_data_manager.get_user_settings(12345)

        # Проверяем, что тема включена в настройки
        assert "theme" in settings
        assert settings["theme"] == "dark"