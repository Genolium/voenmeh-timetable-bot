import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
from core.user_data import UserDataManager

@pytest.fixture
def manager(mocker):
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

    async def test_set_user_group(self, manager):
        await manager.set_user_group(123, "О735Б")
        manager._execute.assert_called_once_with(
            'UPDATE users SET "group" = ? WHERE user_id = ?', ("О735Б", 123), commit=True
        )

    async def test_get_user_group(self, manager):
        manager._execute.return_value = {'group': 'О735Б'}
        assert await manager.get_user_group(123) == "О735Б"
        manager._execute.return_value = None
        assert await manager.get_user_group(404) is None

    async def test_get_user_settings(self, manager):
        mock_row = MagicMock()
        settings_data = {'evening_notify': 1, 'morning_summary': 0, 'lesson_reminders': 1}
        def get_item_by_key(key): return settings_data[key]
        mock_row.__getitem__.side_effect = get_item_by_key
        mock_row.keys.return_value = settings_data.keys()
        manager._execute.return_value = mock_row
        
        settings = await manager.get_user_settings(123)
        
        assert settings == {"evening_notify": True, "morning_summary": False, "lesson_reminders": True}
        manager._execute.assert_called_once_with(
            "SELECT evening_notify, morning_summary, lesson_reminders FROM users WHERE user_id = ?",
            (123,), fetchone=True
        )

    async def test_get_active_users_by_period(self, manager, mocker):
        fixed_now = datetime(2024, 10, 31, 12, 0, 0)
        mocker.patch('core.user_data.datetime', MagicMock(utcnow=lambda: fixed_now))
        
        await manager.get_active_users_by_period(days=7)
        
        expected_start_date = fixed_now - timedelta(days=7)
        
        manager._execute.assert_called_once_with(
            "SELECT COUNT(user_id) FROM users WHERE last_active_date >= ?",
            (expected_start_date,),
            fetchone=True
        )

    async def test_get_subscribed_users_count(self, manager):
        await manager.get_subscribed_users_count()
        expected_query = "SELECT COUNT(user_id) FROM users WHERE evening_notify = 1 OR morning_summary = 1 OR lesson_reminders = 1"
        manager._execute.assert_called_once_with(expected_query, fetchone=True)