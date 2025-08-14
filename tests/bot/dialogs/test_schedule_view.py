import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime
from aiogram.types import CallbackQuery, User

from bot.dialogs.schedule_view import (
    on_send_original_file_callback, on_send_original_file, on_date_shift, on_news_clicked
)
from bot.dialogs.constants import DialogDataKeys

@pytest.fixture
def mock_callback():
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = AsyncMock(spec=User)
    callback.from_user.id = 123456789
    callback.answer = AsyncMock()
    callback.message = AsyncMock()
    callback.message.answer = AsyncMock()
    return callback

@pytest.fixture
def mock_manager():
    manager = AsyncMock()
    # Правильно мокаем current_context как синхронную функцию
    ctx = AsyncMock()
    ctx.dialog_data = {
        DialogDataKeys.GROUP: "TEST_GROUP",
        DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"
    }
    # Мокаем current_context как синхронную функцию, которая возвращает ctx
    manager.current_context = MagicMock(return_value=ctx)
    manager.middleware_data = {
        "manager": AsyncMock(),
        "user_data_manager": AsyncMock(),
        "bot": AsyncMock()  # Добавляем мок для bot
    }
    return manager

class TestScheduleViewHandlers:
    
    @pytest.mark.asyncio
    async def test_on_send_original_file_callback(self, mock_callback, mock_manager):
        """Тест отправки оригинального файла."""
        # Настраиваем моки
        ctx = mock_manager.current_context()
        ctx.dialog_data = {
            DialogDataKeys.GROUP: "TEST_GROUP",
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"
        }
        
        manager_obj = mock_manager.middleware_data["manager"]
        # Мокаем get_week_type как синхронную функцию
        manager_obj.get_week_type = MagicMock(return_value=("odd", "Нечётная неделя"))
        
        await on_send_original_file_callback(mock_callback, mock_manager)
        
        mock_callback.answer.assert_called()

    @pytest.mark.asyncio
    async def test_on_send_original_file(self, mock_callback, mock_manager):
        """Тест отправки оригинального файла через кнопку."""
        button = MagicMock()
        
        # Настраиваем моки
        ctx = mock_manager.current_context()
        ctx.dialog_data = {
            DialogDataKeys.GROUP: "TEST_GROUP",
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"
        }
        
        manager_obj = mock_manager.middleware_data["manager"]
        # Мокаем get_week_type как синхронную функцию
        manager_obj.get_week_type = MagicMock(return_value=("odd", "Нечётная неделя"))
        
        await on_send_original_file(mock_callback, button, mock_manager)
        
        mock_callback.answer.assert_called()

    @pytest.mark.asyncio
    async def test_on_date_shift(self, mock_callback, mock_manager):
        """Тест сдвига даты."""
        button = MagicMock()
        
        # Настраиваем моки
        ctx = mock_manager.current_context()
        ctx.dialog_data = {
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"
        }
        
        await on_date_shift(mock_callback, button, mock_manager, days=1)
        
        # Проверяем, что дата была обновлена
        assert ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == "2024-01-16"

    @pytest.mark.asyncio
    async def test_on_news_clicked(self, mock_callback, mock_manager):
        """Тест нажатия на новости."""
        button = MagicMock()
        
        await on_news_clicked(mock_callback, button, mock_manager)
        
        mock_callback.message.answer.assert_called()