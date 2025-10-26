import pytest
from unittest.mock import AsyncMock, MagicMock

# Импортируем все необходимые объекты для тестов
from bot.dialogs.settings_menu import (
    get_status_text,
    get_button_text,
    get_settings_data,
    on_toggle_setting,
    on_back_click,
    on_theme_button_click,
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
            "evening_notify": True, "morning_summary": True, "lesson_reminders": True, "theme": "standard"
        }
        
        data = await get_settings_data(mock_manager)

        assert data["evening_status_text"] == "✅ Включена"
        assert data["morning_button_text"] == "Отключить утреннюю сводку"

    async def test_get_settings_data_all_disabled(self, mock_manager):
        """
        Тест геттера: все настройки выключены.
        """
        mock_manager.user_data_manager.get_user_settings.return_value = {
            "evening_notify": False, "morning_summary": False, "lesson_reminders": False, "theme": "standard"
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
        mock_manager.user_data_manager.get_user_settings.return_value = {"evening_notify": True, "theme": "standard"}
        
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
        mock_manager.user_data_manager.get_user_settings.return_value = {"evening_notify": False, "theme": "standard"}

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
        """
        Тест нажатия на кнопку темы подписанным пользователем.
        """
        # Мокаем проверку подписки (подписан)
        mock_redis = AsyncMock()
        mock_redis.get.return_value = '1'  # Подписан

        # Мокаем get_redis_client
        import bot.dialogs.settings_menu as settings_module
        settings_module.get_redis_client = AsyncMock(return_value=mock_redis)

        # Мокаем SUBSCRIPTION_CHANNEL
        from core.config import SUBSCRIPTION_CHANNEL
        original_channel = SUBSCRIPTION_CHANNEL
        settings_module.SUBSCRIPTION_CHANNEL = "@test_channel"

        try:
            mock_callback = AsyncMock()
            mock_callback.from_user.id = 123

            await on_theme_button_click(mock_callback, MagicMock(), mock_manager)

            # Проверяем, что пользователь перешел к выбору темы
            mock_manager.switch_to.assert_called_with(SettingsMenu.choose_theme)

        finally:
            # Восстанавливаем оригинальные функции
            from core.config import get_redis_client, SUBSCRIPTION_CHANNEL
            settings_module.get_redis_client = get_redis_client
            settings_module.SUBSCRIPTION_CHANNEL = original_channel

    async def test_on_theme_button_click_not_subscribed(self, mock_manager):
        """
        Тест нажатия на кнопку темы неподписанным пользователем.
        """
        # Мокаем проверку подписки (не подписан)
        mock_redis = AsyncMock()
        mock_redis.get.return_value = '0'  # Не подписан

        # Мокаем задачу проверки подписки
        import bot.dialogs.settings_menu as settings_module
        settings_module.check_theme_subscription_task = AsyncMock()

        # Мокаем get_redis_client
        settings_module.get_redis_client = AsyncMock(return_value=mock_redis)

        # Мокаем bot для API проверки (поскольку Redis недоступен, код перейдет к API)
        mock_bot = AsyncMock()
        mock_member = AsyncMock()
        mock_member.status = "left"  # Не подписан
        mock_bot.get_chat_member.return_value = mock_member
        mock_manager.middleware_data["bot"] = mock_bot

        # Мокаем SUBSCRIPTION_CHANNEL
        from core.config import SUBSCRIPTION_CHANNEL
        original_channel = SUBSCRIPTION_CHANNEL
        import bot.dialogs.settings_menu as settings_module
        settings_module.SUBSCRIPTION_CHANNEL = "@test_channel"

        try:
            mock_callback = AsyncMock()
            mock_callback.from_user.id = 123

            await on_theme_button_click(mock_callback, MagicMock(), mock_manager)

            # Проверяем, что задача проверки подписки была вызвана (через API fallback)
            settings_module.check_theme_subscription_task.send.assert_called_with(123, mock_callback.id)

            # Проверяем, что пользователь получил ошибку
            mock_callback.answer.assert_called_with("❌ Требуется подписка на канал для доступа к темам", show_alert=True)

        finally:
            # Восстанавливаем оригинальные функции
            from core.config import get_redis_client, SUBSCRIPTION_CHANNEL
            from bot.tasks import check_theme_subscription_task
            settings_module.get_redis_client = get_redis_client
            settings_module.SUBSCRIPTION_CHANNEL = original_channel
            settings_module.check_theme_subscription_task = check_theme_subscription_task