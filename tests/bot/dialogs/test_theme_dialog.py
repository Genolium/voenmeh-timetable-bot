import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.dialogs.theme_dialog import get_theme_data, on_theme_selected, on_check_subscription
from bot.dialogs.states import SettingsMenu
from core.config import SUBSCRIPTION_CHANNEL


@pytest.fixture
def mock_manager():
    """Создает мок DialogManager для тестов."""
    mock_udm = AsyncMock()
    mock_bot = AsyncMock()
    manager = AsyncMock()

    manager.middleware_data = {
        "user_data_manager": mock_udm,
        "bot": mock_bot
    }
    manager.event = MagicMock()
    manager.event.from_user.id = 123

    return manager


@pytest.mark.asyncio
class TestThemeDialog:

    async def test_get_theme_data_subscribed(self, mock_manager):
        """Тест получения данных темы для подписанного пользователя."""
        # Мокаем проверку подписки
        mock_manager.middleware_data["bot"].get_chat_member.return_value = AsyncMock()
        mock_manager.middleware_data["bot"].get_chat_member.return_value.status = "member"

        # Мокаем данные пользователя
        mock_manager.middleware_data["user_data_manager"].get_user_theme.return_value = "light"

        # Мокаем Redis
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # Нет кэша

        # Заменяем get_redis_client
        import bot.dialogs.theme_dialog as theme_module
        original_get_redis = theme_module.get_redis_client
        theme_module.get_redis_client = AsyncMock(return_value=mock_redis)

        try:
            data = await get_theme_data(mock_manager)

            # Должны вернуться данные для окна выбора темы (пользователь подписан)
            assert "themes" in data
            assert "current_theme" in data
            assert len(data["themes"]) == 5  # 5 тем

            # Проверяем, что тема пользователя правильно определена
            assert data["current_theme"] == "☀️ Светлая"

        finally:
            # Восстанавливаем оригинальную функцию
            theme_module.get_redis_client = original_get_redis

    async def test_get_theme_data_not_subscribed(self, mock_manager):
        """Тест получения данных темы для неподписанного пользователя."""
        # Мокаем проверку подписки (пользователь не подписан)
        mock_manager.middleware_data["bot"].get_chat_member.return_value = AsyncMock()
        mock_manager.middleware_data["bot"].get_chat_member.return_value.status = "left"

        # Мокаем Redis
        mock_redis = AsyncMock()
        mock_redis.get.return_value = '0'  # Кэш показывает, что не подписан

        # Заменяем get_redis_client
        import bot.dialogs.theme_dialog as theme_module
        original_get_redis = theme_module.get_redis_client
        theme_module.get_redis_client = AsyncMock(return_value=mock_redis)

        try:
            data = await get_theme_data(mock_manager)

            # Должны вернуться пустые данные (пользователь будет переключен на экран блокировки)
            assert data == {}

            # Проверяем, что был вызван switch_to для блокировки
            mock_manager.switch_to.assert_called_with(SettingsMenu.theme_subscription_gate)

        finally:
            theme_module.get_redis_client = original_get_redis

    async def test_on_theme_selected_subscribed(self, mock_manager):
        """Тест выбора темы подписанным пользователем."""
        # Мокаем проверку подписки (подписан)
        mock_manager.middleware_data["bot"].get_chat_member.return_value = AsyncMock()
        mock_manager.middleware_data["bot"].get_chat_member.return_value.status = "member"

        # Мокаем Redis
        mock_redis = AsyncMock()
        mock_redis.get.return_value = '1'  # Подписан

        # Заменяем get_redis_client
        import bot.dialogs.theme_dialog as theme_module
        original_get_redis = theme_module.get_redis_client
        theme_module.get_redis_client = AsyncMock(return_value=mock_redis)

        try:
            # Мокаем callback
            mock_callback = AsyncMock()
            mock_callback.from_user.id = 123

            # Мокаем данные пользователя
            mock_manager.middleware_data["user_data_manager"].set_user_theme = AsyncMock()

            await on_theme_selected(mock_callback, None, mock_manager, "dark")

            # Проверяем, что тема была установлена
            mock_manager.middleware_data["user_data_manager"].set_user_theme.assert_called_with(123, "dark")

            # Проверяем, что пользователь получил подтверждение
            mock_callback.answer.assert_called_with("✅ Тема изменена на 🌙 Тёмная!")

            # Проверяем, что пользователь вернулся в настройки
            mock_manager.switch_to.assert_called_with(SettingsMenu.main)

        finally:
            theme_module.get_redis_client = original_get_redis

    async def test_on_theme_selected_not_subscribed(self, mock_manager):
        """Тест попытки выбора темы неподписанным пользователем."""
        # Мокаем проверку подписки (не подписан)
        mock_manager.middleware_data["bot"].get_chat_member.return_value = AsyncMock()
        mock_manager.middleware_data["bot"].get_chat_member.return_value.status = "left"

        # Мокаем Redis
        mock_redis = AsyncMock()
        mock_redis.get.return_value = '0'  # Не подписан

        # Заменяем get_redis_client
        import bot.dialogs.theme_dialog as theme_module
        original_get_redis = theme_module.get_redis_client
        theme_module.get_redis_client = AsyncMock(return_value=mock_redis)

        try:
            # Мокаем callback
            mock_callback = AsyncMock()
            mock_callback.from_user.id = 123

            await on_theme_selected(mock_callback, None, mock_manager, "dark")

            # Проверяем, что пользователь получил ошибку
            mock_callback.answer.assert_called_with("❌ Требуется подписка на канал для доступа к темам", show_alert=True)

            # Проверяем, что тема НЕ была установлена
            mock_manager.middleware_data["user_data_manager"].set_user_theme.assert_not_called()

        finally:
            theme_module.get_redis_client = original_get_redis

    async def test_on_check_subscription(self, mock_manager):
        """Тест проверки подписки."""
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 123

        # Мокаем задачу проверки подписки
        import bot.dialogs.theme_dialog as theme_module
        original_task = theme_module.check_theme_subscription_task
        theme_module.check_theme_subscription_task = AsyncMock()

        try:
            await on_check_subscription(mock_callback, None, mock_manager)

            # Проверяем, что задача была вызвана
            theme_module.check_theme_subscription_task.send.assert_called_with(123, mock_callback.id)

            # Проверяем, что пользователь вернулся к выбору темы
            mock_manager.switch_to.assert_called_with(SettingsMenu.choose_theme)

        finally:
            theme_module.check_theme_subscription_task = original_task
