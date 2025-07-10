import pytest
from unittest.mock import AsyncMock, MagicMock, ANY

# Импортируем тестируемые функции
from bot.dialogs.admin_menu import on_test_morning, on_broadcast_received, get_stats_data
# Импортируем актор, который теперь будет мокироваться
from bot.tasks import copy_message_task 
# Импортируем метрику для мокирования
from core.metrics import TASKS_SENT_TO_QUEUE 

# Импортируем состояние, чтобы Admin.stats был доступен
from bot.dialogs.states import Admin 

@pytest.fixture
def mock_manager(mocker):
    manager = AsyncMock()
    manager.middleware_data = {
        "bot": AsyncMock(),
        "user_data_manager": AsyncMock()
    }
    # МОКИРОВАНИЕ АКТОРА: Мы мокируем *метод `send`* актора Dramatiq,
    # чтобы не выполнять реальную отправку в очередь во время теста.
    mocker.patch('bot.tasks.copy_message_task.send', new_callable=MagicMock) 
    
    # МОКИРОВАНИЕ МЕТРИКИ: Мокируем вызовы `labels().inc()` для метрики,
    # чтобы избежать ошибок, если Prometheus-клиент не полностью инициализирован в тестовой среде.
    mocker.patch('core.metrics.TASKS_SENT_TO_QUEUE.labels', return_value=MagicMock(inc=MagicMock()))

    return manager

@pytest.mark.asyncio
class TestAdminMenu:
    async def test_on_test_broadcast_click(self, mock_manager, mocker):
        # Мокируем функцию планировщика, чтобы она не выполнялась
        mocker.patch('bot.dialogs.admin_menu.morning_summary_broadcast', new_callable=AsyncMock)
        mock_callback = AsyncMock()
        mock_callback.message.answer = AsyncMock()
        
        await on_test_morning(mock_callback, None, mock_manager)
        
        mock_callback.answer.assert_called_once_with("🚀 Запускаю постановку задач на утреннюю рассылку...")
        mock_callback.message.answer.assert_called_once_with("✅ Задачи для утренней рассылки поставлены в очередь.")

    async def test_get_stats_data(self, mock_manager):
        udm = mock_manager.middleware_data["user_data_manager"]
        udm.get_total_users_count.return_value = 100
        udm.get_new_users_count.side_effect = [10, 50]
        udm.get_subscribed_users_count.return_value = 25
        udm.get_active_users_by_period.side_effect = [15, 60, 90]
        udm.get_top_groups.return_value = [("О735Б", 20)]
        
        data = await get_stats_data(user_data_manager=udm)
        
        stats_text = data["stats_text"]
        assert "Всего пользователей: <b>100</b>" in stats_text
        assert "Новых за сегодня: <b>10</b>" in stats_text
        assert "Активных за день: <b>15</b>" in stats_text
        assert "Активных за неделю: <b>60</b>" in stats_text
        assert "Активных за месяц: <b>90</b>" in stats_text
        assert "С подписками: <b>25</b>" in stats_text
        assert "- О735Б: 20" in stats_text

    async def test_on_broadcast_received(self, mock_manager):
        udm = mock_manager.middleware_data["user_data_manager"]
        bot = mock_manager.middleware_data["bot"] 
        udm.get_all_user_ids.return_value = [1, 2, 3] # Три тестовых пользователя для рассылки
        
        mock_message = AsyncMock()
        mock_message.from_user.id = 999 # ID админа, который отправляет рассылку
        mock_message.chat.id = 999 # Chat ID, откуда пришло сообщение (обычно тот же, что и from_user.id для личного чата с ботом)
        mock_message.message_id = 12345 # ID сообщения, которое нужно скопировать
        
        await on_broadcast_received(mock_message, None, mock_manager)
        
        assert copy_message_task.send.call_count == 3
        
        # Проверяем аргументы каждого вызова актора
        copy_message_task.send.assert_any_call(1, 999, 12345)
        copy_message_task.send.assert_any_call(2, 999, 12345)
        copy_message_task.send.assert_any_call(3, 999, 12345)

        # Проверяем, что бот отправил отчет администратору о статусе постановки задач в очередь
        bot.send_message.assert_called_with(999, ANY)
        assert "Задачи рассылки поставлены в очередь!" in bot.send_message.call_args.args[1]