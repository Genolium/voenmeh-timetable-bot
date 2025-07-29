import pytest
from unittest.mock import AsyncMock, MagicMock, ANY

from bot.dialogs.admin_menu import on_test_morning, on_test_evening, on_broadcast_received, get_stats_data
from bot.tasks import copy_message_task 
from core.metrics import TASKS_SENT_TO_QUEUE 

from bot.dialogs.states import Admin 

@pytest.fixture
def mock_manager(mocker):
    manager = AsyncMock()
    manager.middleware_data = {
        "bot": AsyncMock(),
        "user_data_manager": AsyncMock()
    }
    mocker.patch('bot.tasks.copy_message_task.send', new_callable=MagicMock) 
    mocker.patch('core.metrics.TASKS_SENT_TO_QUEUE.labels', return_value=MagicMock(inc=MagicMock()))

    return manager

@pytest.mark.asyncio
class TestAdminMenu:
    async def test_on_test_morning_click(self, mock_manager, mocker):
        # Мокируем функцию, которую будем вызывать
        mock_broadcast_func = mocker.patch('bot.dialogs.admin_menu.morning_summary_broadcast', new_callable=AsyncMock)
        mock_callback = AsyncMock()
        mock_callback.message.answer = AsyncMock()
        
        await on_test_morning(mock_callback, None, mock_manager)
        
        mock_callback.answer.assert_called_once_with("🚀 Запускаю постановку задач на утреннюю рассылку...")
        # Проверяем, что наша функция была вызвана
        mock_broadcast_func.assert_called_once()
        mock_callback.message.answer.assert_called_once_with("✅ Задачи для утренней рассылки поставлены в очередь.")

    async def test_on_test_evening_click(self, mock_manager, mocker):
        mock_broadcast_func = mocker.patch('bot.dialogs.admin_menu.evening_broadcast', new_callable=AsyncMock)
        mock_callback = AsyncMock()
        mock_callback.message.answer = AsyncMock()
        
        await on_test_evening(mock_callback, None, mock_manager)
        
        mock_callback.answer.assert_called_once_with("🚀 Запускаю постановку задач на вечернюю рассылку...")
        mock_broadcast_func.assert_called_once()
        mock_callback.message.answer.assert_called_once_with("✅ Задачи для вечерней рассылки поставлены в очередь.")


    async def test_get_stats_data(self, mock_manager):
        udm = mock_manager.middleware_data["user_data_manager"]
        udm.get_total_users_count.return_value = 100
        udm.get_new_users_count.side_effect = [10, 50]
        udm.get_active_users_by_period.side_effect = [15, 60, 90]
        udm.get_subscribed_users_count.return_value = 25
        udm.get_top_groups.return_value = [("О735Б", 20)]
        
        data = await get_stats_data(user_data_manager=udm)
        
        stats_text = data["stats_text"]
        assert "👤 Всего: <b>100</b>" in stats_text
        assert "Сегодня: <b>10</b>" in stats_text
        assert "Неделя: <b>50</b>" in stats_text
        assert "🔥 <b>15</b> / <b>60</b> / <b>90</b>" in stats_text
        assert "🔔 С подписками: <b>25</b>" in stats_text
        assert "- О735Б: 20" in stats_text

    async def test_on_broadcast_received(self, mock_manager):
        udm = mock_manager.middleware_data["user_data_manager"]
        bot = mock_manager.middleware_data["bot"] 
        udm.get_all_user_ids.return_value = [1, 2, 3]
        
        mock_message = AsyncMock()
        mock_message.from_user.id = 999
        mock_message.chat.id = 999
        mock_message.message_id = 12345
        
        await on_broadcast_received(mock_message, None, mock_manager)
        
        assert copy_message_task.send.call_count == 3
        
        copy_message_task.send.assert_any_call(1, 999, 12345)
        bot.send_message.assert_called_with(999, ANY)
        assert "Задачи рассылки поставлены в очередь!" in bot.send_message.call_args.args[1]