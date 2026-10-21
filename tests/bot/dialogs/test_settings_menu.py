from unittest.mock import AsyncMock, MagicMock

import pytest

# Импортируем все необходимые объекты для тестов
from bot.dialogs.settings_menu import (
    get_button_text,
    get_settings_data,
    get_status_text,
    on_back_click,
    on_theme_button_click,
    on_toggle_setting,
)
from bot.dialogs.states import SettingsMenu

# --- Тесты для "чистых" хелпер-функций ---


def test_get_status_text():
    """Тестирует хелпер для получения текстового статуса."""
    assert get_status_text(True, "ru") == "✅ Включена"
    assert get_status_text(False, "ru") == "❌ Отключена"


def test_get_button_text():
    """Тестирует хелпер для получения текста кнопки."""
    assert get_button_text(True, "evening", "ru") == "Отключить сводку на завтра"
    assert get_button_text(False, "morning", "ru") == "Включить утреннюю сводку"
    assert get_button_text(True, "reminders", "ru") == "Отключить напоминания о парах"
    assert get_button_text(False, "reminders", "ru") == "Включить напоминания о парах"


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
            "evening_notify": True,
            "morning_summary": True,
            "lesson_reminders": True,
            "theme": "standard",
        }

        data = await get_settings_data(mock_manager)

        assert data["evening_status_text"] == "✅ Включена"
        assert data["morning_button_text"] == "Отключить утреннюю сводку"

    async def test_get_settings_data_all_disabled(self, mock_manager):
        """
        Тест геттера: все настройки выключены.
        """
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": False,
            "morning_summary": False,
            "lesson_reminders": False,
            "theme": "standard",
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
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": True,
            "theme": "standard",
        }

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
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": False,
            "theme": "standard",
        }

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

    async def test_on_theme_button_click_subscribed(self, mock_manager):
        """Подписанный пользователь переходит к выбору темы."""
        from unittest.mock import patch

        mock_bot = AsyncMock()
        member = MagicMock(status="member")
        mock_bot.get_chat_member = AsyncMock(return_value=member)
        mock_manager.middleware_data["bot"] = mock_bot
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 123

        with patch("core.config.SUBSCRIPTION_CHANNEL", "@test_channel"):
            await on_theme_button_click(mock_callback, MagicMock(), mock_manager)

        mock_manager.switch_to.assert_called_with(SettingsMenu.choose_theme)

    async def test_on_theme_button_click_not_subscribed(self, mock_manager):
        """Не подписанный / ошибка проверки — fail-closed, показываем гейт."""
        from unittest.mock import patch

        mock_bot = AsyncMock()
        mock_bot.get_chat_member = AsyncMock(side_effect=Exception("Subscription check failed"))
        mock_manager.middleware_data["bot"] = mock_bot
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 123

        with patch("core.config.SUBSCRIPTION_CHANNEL", "@test_channel"):
            await on_theme_button_click(mock_callback, MagicMock(), mock_manager)

        mock_manager.switch_to.assert_called_with(SettingsMenu.theme_subscription_gate)
