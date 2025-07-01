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
    mock_udm = AsyncMock()
    manager = AsyncMock()
    
    manager.middleware_data = {"user_data_manager": mock_udm}
    
    # Настраиваем объект события с ID пользователя, который будет использоваться геттером
    manager.event = MagicMock()
    manager.event.from_user.id = 123
    
    # Добавляем удобный доступ к моку UDM для тестов
    manager.user_data_manager = mock_udm
    
    return manager

@pytest.mark.asyncio
class TestSettingsDialog:

    async def test_get_settings_data_all_enabled(self, mock_manager):
        """
        Тест геттера: все настройки включены.
        """
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": True, "morning_summary": True, "lesson_reminders": True
        }
        
        data = await get_settings_data(mock_manager)

        assert data["evening_status_text"] == "✅ Включена"
        assert data["morning_button_text"] == "Отключить утреннюю сводку"

    async def test_get_settings_data_all_disabled(self, mock_manager):
        """
        Тест геттера: все настройки выключены.
        """
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": False, "morning_summary": False, "lesson_reminders": False
        }
        
        data = await get_settings_data(mock_manager)

        assert data["evening_status_text"] == "❌ Отключена"
        assert data["morning_button_text"] == "Включить утреннюю сводку"

    async def test_on_toggle_setting(self, mock_manager):
        """
        Тест обработчика клика: проверяет и включение, и выключение.
        """
        # --- СЦЕНАРИЙ ВЫКЛЮЧЕНИЯ ---
        
        # Arrange
        mock_manager.user_data_manager.get_user_settings.return_value = {"evening_notify": True}
        
        # ИСПРАВЛЕНИЕ: Создаем мок callback и ЯВНО задаем значение атрибута .id
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 123
        mock_button = MagicMock(widget_id="evening_notify")

        # Act
        await on_toggle_setting(mock_callback, mock_button, mock_manager)

        # Assert
        mock_manager.user_data_manager.update_setting.assert_called_with(123, "evening_notify", False)
        mock_callback.answer.assert_called_with("Настройка обновлена.")
        mock_manager.switch_to.assert_called_with(SettingsMenu.main)

        # --- СЦЕНАРИЙ ВКЛЮЧЕНИЯ ---
        
        # Arrange
        mock_manager.user_data_manager.get_user_settings.return_value = {"evening_notify": False}

        # Act
        await on_toggle_setting(mock_callback, mock_button, mock_manager)

        # Assert (проверяем последний вызов)
        mock_manager.user_data_manager.update_setting.assert_called_with(123, "evening_notify", True)

    async def test_on_back_click(self, mock_manager):
        """
        Тест нажатия на кнопку "Назад".
        """
        await on_back_click(AsyncMock(), MagicMock(), mock_manager)
        mock_manager.done.assert_called_once()