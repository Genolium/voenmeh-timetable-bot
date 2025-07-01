import pytest
from unittest.mock import AsyncMock, MagicMock

# Импортируем все необходимые объекты для тестов
from bot.dialogs.settings_menu import (
    get_status_text,
    get_button_text,
    get_settings_data,
    on_toggle_setting,
    on_back_click,
)
from bot.dialogs.states import SettingsMenu

# --- Тесты для "чистых" хелпер-функций ---

def test_get_status_text():
    """Тестирует хелпер для получения текстового статуса."""
    assert get_status_text(True) == "✅ Включена"
    assert get_status_text(False) == "❌ Отключена"

def test_get_button_text():
    """Тестирует хелпер для получения текста кнопки."""
    assert get_button_text(True, "уведомления") == "Отключить уведомления"
    assert get_button_text(False, "рассылку") == "Включить рассылку"


# --- Тесты для асинхронных обработчиков и геттеров ---

@pytest.fixture
def mock_manager():
    """
    Создает полный мок DialogManager с вложенными моками
    для UserDataManager и события (callback/message).
    """
    # Создаем мок для менеджера данных
    mock_udm = AsyncMock()
    
    # Создаем основной мок DialogManager
    manager = AsyncMock()
    
    # Настраиваем middleware_data, как это делает aiogram
    manager.middleware_data = {"user_data_manager": mock_udm}
    
    # Настраиваем объект события с ID пользователя
    # Это важно, чтобы `callback.from_user.id` возвращал число, а не мок
    manager.event = MagicMock()
    manager.event.from_user.id = 12345
    
    # Добавляем удобный доступ к моку UDM для тестов
    manager.user_data_manager = mock_udm
    
    return manager

@pytest.mark.asyncio
class TestSettingsDialog:

    async def test_get_settings_data_all_enabled(self, mock_manager):
        """
        Тест геттера: все настройки включены.
        Проверяет, что тексты статусов и кнопок формируются корректно.
        """
        # Arrange
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": True,
            "morning_summary": True,
            "lesson_reminders": True
        }
        
        # Act
        data = await get_settings_data(mock_manager)

        # Assert
        assert data["evening_status_text"] == "✅ Включена"
        assert data["morning_status_text"] == "✅ Включена"
        assert data["reminders_status_text"] == "✅ Включена"
        assert data["evening_button_text"] == "Отключить сводку на завтра"
        assert data["morning_button_text"] == "Отключить утреннюю сводку"
        assert data["reminders_button_text"] == "Отключить напоминания о парах"

    async def test_get_settings_data_all_disabled(self, mock_manager):
        """
        Тест геттера: все настройки выключены.
        """
        # Arrange
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": False,
            "morning_summary": False,
            "lesson_reminders": False
        }
        
        # Act
        data = await get_settings_data(mock_manager)

        # Assert
        assert data["evening_status_text"] == "❌ Отключена"
        assert data["reminders_status_text"] == "❌ Отключена"
        assert data["evening_button_text"] == "Включить сводку на завтра"

    async def test_get_settings_data_user_not_found(self, mock_manager):
        """
        Тест геттера (граничный случай): пользователь не найден в БД.
        Функция должна вернуть значения по умолчанию.
        """
        # Arrange
        mock_manager.user_data_manager.get_user_settings.return_value = {} # get_user_settings возвращает {}
        
        # Act
        data = await get_settings_data(mock_manager)

        # Assert
        assert data["evening_status_text"] == "❌ Отключена"
        assert data["morning_status_text"] == "❌ Отключена"
        assert data["reminders_status_text"] == "❌ Отключена"


    async def test_on_toggle_setting_turns_off(self, mock_manager):
        """
        Тест обработчика клика: выключение настройки.
        """
        # Arrange
        # Имитируем, что настройка сейчас включена
        mock_manager.user_data_manager.get_user_settings.return_value = {"evening_notify": True}
        
        # Создаем моки для аргументов функции
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 12345
        mock_button = MagicMock(widget_id="evening_notify")

        # Act
        await on_toggle_setting(mock_callback, mock_button, mock_manager)

        # Assert
        # 1. Проверяем, что в БД была отправлена команда на ВЫКЛЮЧЕНИЕ (False)
        mock_manager.user_data_manager.update_setting.assert_called_once_with(12345, "evening_notify", False)
        # 2. Проверяем, что пользователю отправлен pop-up ответ
        mock_callback.answer.assert_called_once_with("Настройка обновлена.")
        # 3. Проверяем, что диалог был перезагружен для отображения изменений
        mock_manager.switch_to.assert_called_once_with(SettingsMenu.main)

    async def test_on_toggle_setting_turns_on(self, mock_manager):
        """
        Тест обработчика клика: включение настройки.
        """
        # Arrange
        mock_manager.user_data_manager.get_user_settings.return_value = {"morning_summary": False}
        
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 12345
        mock_button = MagicMock(widget_id="morning_summary")
        
        # Act
        await on_toggle_setting(mock_callback, mock_button, mock_manager)

        # Assert
        # Проверяем, что в БД была отправлена команда на ВКЛЮЧЕНИЕ (True)
        mock_manager.user_data_manager.update_setting.assert_called_once_with(12345, "morning_summary", True)

    async def test_on_back_click(self, mock_manager):
        """
        Тест нажатия на кнопку "Назад".
        Проверяет, что диалог просто завершается.
        """
        # Arrange
        mock_callback = AsyncMock()
        mock_button = MagicMock()
        
        # Act
        await on_back_click(mock_callback, mock_button, mock_manager)

        # Assert
        mock_manager.done.assert_called_once()