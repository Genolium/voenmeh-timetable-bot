import pytest
from unittest.mock import AsyncMock, MagicMock
from core.user_data import UserDataManager

@pytest.fixture
def manager(mocker):
    """Фикстура, которая создает экземпляр UserDataManager и мокирует его внутренний метод _execute."""
    mocker.patch('core.user_data.UserDataManager._execute', new_callable=AsyncMock)
    return UserDataManager(db_path=':memory:')

@pytest.mark.asyncio
class TestUserDataManager:

    async def test_init_db(self, manager):
        await manager.init_db()
        call_args = manager._execute.call_args.args
        assert "CREATE TABLE IF NOT EXISTS users" in call_args[0]
        assert manager._execute.call_args.kwargs['commit'] is True

    async def test_register_user(self, manager):
        await manager.register_user(123, "testuser")
        assert manager._execute.call_count == 2
        update_call = manager._execute.call_args_list[0]
        assert "UPDATE users SET last_active_date" in update_call.args[0]
        insert_call = manager._execute.call_args_list[1]
        assert "INSERT OR IGNORE" in insert_call.args[0]

    async def test_set_user_group(self, manager):
        await manager.set_user_group(123, "O735Б")
        manager._execute.assert_called_once_with(
            'UPDATE users SET "group" = ? WHERE user_id = ?', ("O735Б", 123), commit=True
        )

    async def test_get_user_group(self, manager):
        manager._execute.return_value = {'group': 'O735Б'}
        assert await manager.get_user_group(123) == "O735Б"
        manager._execute.return_value = None
        assert await manager.get_user_group(404) is None

    async def test_get_user_settings(self, manager):
        # --- Сценарий 1: Пользователь найден ---
        
        # Arrange
        # Создаем мок, который ведет себя как sqlite3.Row
        mock_row = MagicMock()
        settings_data = {'evening_notify': 1, 'morning_summary': 0, 'lesson_reminders': 1}
        # Настраиваем доступ по ключу, как у dict(row)
        def get_item_by_key(key): return settings_data[key]
        mock_row.__getitem__.side_effect = get_item_by_key
        mock_row.keys.return_value = settings_data.keys()
        
        manager._execute.return_value = mock_row
        
        # Act
        settings = await manager.get_user_settings(123)
        
        # Assert
        assert settings == {"evening_notify": True, "morning_summary": False, "lesson_reminders": True}
        manager._execute.assert_called_once_with(
            "SELECT evening_notify, morning_summary, lesson_reminders FROM users WHERE user_id = ?",
            (123,), fetchone=True
        )

        # --- Сценарий 2: Пользователь не найден ---
        
        # Arrange
        manager._execute.reset_mock()
        manager._execute.return_value = None
        
        # Act
        settings_not_found = await manager.get_user_settings(404)
        
        # Assert
        assert settings_not_found == {"evening_notify": False, "morning_summary": False, "lesson_reminders": False}

    async def test_update_setting(self, manager):
        await manager.update_setting(123, "morning_summary", False)
        manager._execute.assert_called_once_with(
            'UPDATE users SET "morning_summary" = ? WHERE user_id = ?', (0, 123), commit=True
        )

    async def test_get_users_for_notify_methods(self, manager):
        """Универсальный тест для всех методов get_users_for..."""
        methods_to_test = {
            "get_users_for_evening_notify": "evening_notify",
            "get_users_for_morning_summary": "morning_summary",
            "get_users_for_lesson_reminders": "lesson_reminders",
        }
        
        for method_name, setting_name in methods_to_test.items():
            manager._execute.reset_mock()
            method_to_call = getattr(manager, method_name)
            await method_to_call()
            sql_query = manager._execute.call_args.args[0]
            assert f"WHERE {setting_name} = 1" in sql_query