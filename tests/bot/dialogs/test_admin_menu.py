import pytest
from unittest.mock import AsyncMock, MagicMock, ANY
from datetime import datetime 

from bot.dialogs.admin_menu import (
    on_test_morning, on_test_evening, on_broadcast_received, get_stats_data, 
    on_period_selected, on_user_id_input, on_new_group_input
)
from bot.tasks import copy_message_task 
from core.metrics import TASKS_SENT_TO_QUEUE 
from core.db import User

from bot.dialogs.states import Admin 

@pytest.fixture
def mock_manager(mocker):
    manager = AsyncMock()
    manager.middleware_data = {
        "bot": AsyncMock(),
        "user_data_manager": AsyncMock(),
        "manager": MagicMock(_schedules={"О735Б": {}})
    }
    manager.dialog_data = {}
    mocker.patch('bot.tasks.copy_message_task.send', new_callable=MagicMock) 
    mocker.patch('core.metrics.TASKS_SENT_TO_QUEUE.labels', return_value=MagicMock(inc=MagicMock()))

    return manager

@pytest.mark.asyncio
class TestAdminMenu:
    async def test_on_test_morning_click(self, mock_manager, mocker):
        mock_broadcast_func = mocker.patch('bot.dialogs.admin_menu.morning_summary_broadcast', new_callable=AsyncMock)
        mock_callback = AsyncMock()
        mock_callback.message.answer = AsyncMock()
        
        await on_test_morning(mock_callback, None, mock_manager)
        
        mock_callback.answer.assert_called_once_with("🚀 Запускаю постановку задач на утреннюю рассылку...")
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
        udm.get_new_users_count.return_value = 7
        udm.get_active_users_by_period.side_effect = [15, 60, 90, 60]
        udm.get_subscribed_users_count.return_value = 80
        udm.get_unsubscribed_count.return_value = 5
        udm.get_subscription_breakdown.return_value = {"evening": 70, "morning": 60, "reminders": 50}
        udm.get_top_groups.return_value = [("О735Б", 20)]
        udm.get_group_distribution.return_value = {"2-5 студентов": 10}
        
        data = await get_stats_data(dialog_manager=mock_manager)
        
        stats_text = data["stats_text"]
        assert "Период: <b>Неделя</b>" in stats_text
        assert "Всего пользователей: <b>100</b>" in stats_text
        assert "Новых за период: <b>7</b>" in stats_text
        assert "DAU / WAU / MAU: <b>15 / 60 / 90</b>" in stats_text
        assert "С подписками: <b>80</b>" in stats_text
        assert "Отписались от всего: <b>5</b>" in stats_text
        assert "Вечер: 70" in stats_text
        assert "- О735Б: 20" in stats_text
        assert "2-5 студентов: 10" in stats_text

    async def test_on_period_selected(self, mock_manager):
        assert 'stats_period' not in mock_manager.dialog_data
        
        await on_period_selected(AsyncMock(), MagicMock(), mock_manager, item_id="30")
        
        assert mock_manager.dialog_data['stats_period'] == 30

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

    async def test_on_user_id_input_success(self, mock_manager):
        mock_message = AsyncMock(text="12345")
        udm = mock_manager.middleware_data["user_data_manager"]
        udm.get_full_user_info.return_value = User(
            user_id=12345, username="test", group="TEST", 
            registration_date=datetime.now(), last_active_date=datetime.now()
        )
        
        await on_user_id_input(mock_message, None, mock_manager, "12345")
        
        udm.get_full_user_info.assert_called_once_with(12345)
        assert 'found_user_info' in mock_manager.dialog_data
        mock_manager.switch_to.assert_called_once_with(Admin.user_manage)

    async def test_on_user_id_input_not_found(self, mock_manager):
        mock_message = AsyncMock(text="54321")
        udm = mock_manager.middleware_data["user_data_manager"]
        udm.get_full_user_info.return_value = None
        
        await on_user_id_input(mock_message, None, mock_manager, "54321")
        
        mock_message.answer.assert_called_once_with("❌ Пользователь с ID <code>54321</code> не найден в базе.")
        mock_manager.switch_to.assert_not_called()

    async def test_on_new_group_input_success(self, mock_manager):
        mock_message = AsyncMock(text="О735Б")
        mock_manager.dialog_data['found_user_info'] = {'user_id': 12345}
        udm = mock_manager.middleware_data["user_data_manager"]

        await on_new_group_input(mock_message, None, mock_manager, "О735Б")
        
        udm.set_user_group.assert_called_once_with(12345, "О735Б")
        mock_message.answer.assert_called_once()
        mock_manager.switch_to.assert_called_once_with(Admin.user_manage)