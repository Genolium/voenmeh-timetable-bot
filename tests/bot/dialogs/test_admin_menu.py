import pytest
from unittest.mock import AsyncMock, MagicMock, ANY
from bot.dialogs.admin_menu import on_test_morning, on_broadcast_received, get_stats_data

@pytest.fixture
def mock_manager(mocker):
    manager = AsyncMock()
    manager.middleware_data = {
        "bot": AsyncMock(),
        "user_data_manager": AsyncMock()
    }
    return manager

@pytest.mark.asyncio
class TestAdminMenu:
    async def test_on_test_broadcast_click(self, mock_manager, mocker):
        # Мокируем функции рассылок, чтобы не выполнять их логику
        mocker.patch('bot.dialogs.admin_menu.morning_summary_broadcast', new_callable=AsyncMock)
        mock_callback = AsyncMock()
        mock_callback.message.answer = AsyncMock()
        
        await on_test_morning(mock_callback, None, mock_manager)
        
        mock_callback.answer.assert_called_once_with("🚀 Запускаю утреннюю рассылку...")
        mock_callback.message.answer.assert_called_once_with("✅ Утренняя рассылка завершена.")

    async def test_get_stats_data(self, mock_manager):
        udm = mock_manager.middleware_data["user_data_manager"]
        udm.get_total_users_count.return_value = 100
        udm.get_new_users_count.side_effect = [10, 50] # today, week
        udm.get_active_users_count.return_value = 25
        udm.get_top_groups.return_value = [("GROUP1", 20)]
        
        data = await get_stats_data(user_data_manager=udm)
        
        assert "Всего пользователей: <b>100</b>" in data["stats_text"]
        assert "Новых за сегодня: <b>10</b>" in data["stats_text"]
        assert "Активных (с подписками): <b>25</b>" in data["stats_text"]
        assert "- GROUP1: 20" in data["stats_text"]

    async def test_on_broadcast_received(self, mock_manager):
        udm = mock_manager.middleware_data["user_data_manager"]
        bot = mock_manager.middleware_data["bot"]
        udm.get_all_user_ids.return_value = [1, 2, 3]
        
        mock_message = AsyncMock()
        mock_message.from_user.id = 999 # Admin ID
        
        await on_broadcast_received(mock_message, None, mock_manager)
        
        # Проверяем, что бот попытался отправить 3 копии сообщения
        assert mock_message.copy_to.call_count == 3
        # Проверяем отчет администратору
        bot.send_message.assert_called_with(999, ANY)
        assert "Рассылка завершена!" in bot.send_message.call_args.args[1]