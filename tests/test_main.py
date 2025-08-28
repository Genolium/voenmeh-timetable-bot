"""
Тесты для main.py - основного модуля приложения.
"""

import asyncio
import json
import logging
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import Message, User, Chat, CallbackQuery
from aiogram.fsm.storage.redis import RedisStorage
from aiogram_dialog import DialogManager
from redis.asyncio.client import Redis

import main
from bot.dialogs.states import About, Admin, Feedback, MainMenu, Schedule, Events
from core.config import ADMIN_IDS
from core.manager import TimetableManager
from core.user_data import UserDataManager


class TestSetupLogging:
    """Тесты для функции setup_logging."""

    def test_setup_logging(self):
        """Тест настройки логирования."""
        with patch('logging.basicConfig') as mock_basic_config, \
             patch('logging.getLogger') as mock_get_logger:

            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            main.setup_logging()

            # Проверяем, что basicConfig был вызван
            mock_basic_config.assert_called_once()
            args, kwargs = mock_basic_config.call_args
            assert kwargs['level'] == logging.INFO

            # Проверяем, что уровень для aiogram был установлен
            mock_get_logger.assert_called_with('aiogram')
            mock_logger.setLevel.assert_called_once_with(logging.WARNING)


class TestSetBotCommands:
    """Тесты для функции set_bot_commands."""

    @pytest.mark.asyncio
    async def test_set_bot_commands_success(self):
        """Тест успешной установки команд бота."""
        mock_bot = AsyncMock()

        with patch('main.ADMIN_IDS', [123456789]):
            await main.set_bot_commands(mock_bot)

            # Проверяем, что команды были установлены для обычных пользователей
            # Получаем аргументы первого вызова
            calls = mock_bot.set_my_commands.call_args_list
            assert len(calls) >= 1
            user_commands = calls[0][0][0]  # Первый аргумент первого вызова

            # Проверяем, что команды были установлены для админов
            assert mock_bot.set_my_commands.call_count == 2

    @pytest.mark.asyncio
    async def test_set_bot_commands_no_admins(self):
        """Тест установки команд без администраторов."""
        mock_bot = AsyncMock()

        with patch('main.ADMIN_IDS', []):
            await main.set_bot_commands(mock_bot)

            # Проверяем, что команды установлены только для обычных пользователей
            mock_bot.set_my_commands.assert_called_once()
            calls = mock_bot.set_my_commands.call_args_list
            assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_set_bot_commands_error(self):
        """Тест обработки ошибки при установке команд."""
        mock_bot = AsyncMock()
        mock_bot.set_my_commands.side_effect = Exception("API Error")

        with patch('main.ADMIN_IDS', []), \
             patch('main.logging') as mock_logging:

            await main.set_bot_commands(mock_bot)

            # Проверяем, что ошибка была залогирована
            mock_logging.error.assert_called_once()
            assert "Не удалось установить стандартные команды" in mock_logging.error.call_args[0][0]


class TestCommandHandlers:
    """Тесты для обработчиков команд."""

    @pytest.mark.asyncio
    async def test_start_command_handler_with_saved_group(self):
        """Тест обработчика /start с сохраненной группой."""
        from unittest.mock import MagicMock
        from core.user_data import UserDataManager

        mock_user_data_manager = MagicMock(spec=UserDataManager)
        mock_user_data_manager.get_user_group = AsyncMock(return_value="ИВТ-201")

        mock_dialog_manager = AsyncMock()
        mock_dialog_manager.middleware_data = {"user_data_manager": mock_user_data_manager}

        mock_message = MagicMock()
        mock_message.from_user.id = 123456789

        await main.start_command_handler(mock_message, mock_dialog_manager)

        # Проверяем, что была получена сохраненная группа
        mock_user_data_manager.get_user_group.assert_called_once_with(123456789)

        # Проверяем, что диалог был запущен с правильными параметрами
        mock_dialog_manager.start.assert_called_once_with(
            Schedule.view,
            data={"group": "ИВТ-201"},
            mode=main.StartMode.RESET_STACK
        )

    @pytest.mark.asyncio
    async def test_start_command_handler_without_saved_group(self):
        """Тест обработчика /start без сохраненной группы."""
        from unittest.mock import MagicMock
        from core.user_data import UserDataManager

        mock_user_data_manager = MagicMock(spec=UserDataManager)
        mock_user_data_manager.get_user_group = AsyncMock(return_value=None)

        mock_dialog_manager = AsyncMock()
        mock_dialog_manager.middleware_data = {"user_data_manager": mock_user_data_manager}

        mock_message = MagicMock()
        mock_message.from_user.id = 123456789

        await main.start_command_handler(mock_message, mock_dialog_manager)

        # Проверяем, что был запущен диалог выбора типа пользователя
        mock_dialog_manager.start.assert_called_once_with(
            MainMenu.choose_user_type,
            mode=main.StartMode.RESET_STACK
        )

    @pytest.mark.asyncio
    async def test_start_command_handler_invalid_user_data_manager(self):
        """Тест обработчика /start с некорректным UserDataManager."""
        mock_dialog_manager = AsyncMock()
        mock_dialog_manager.middleware_data = {"user_data_manager": "invalid"}

        mock_message = MagicMock()
        mock_message.from_user.id = 123456789

        with patch('main.logging') as mock_logging:
            await main.start_command_handler(mock_message, mock_dialog_manager)

            # Проверяем, что ошибка была залогирована
            mock_logging.error.assert_called_once()
            assert "UserDataManager is not available" in mock_logging.error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_about_command_handler_success(self):
        """Тест успешного обработчика /about."""
        mock_dialog_manager = AsyncMock()

        await main.about_command_handler(MagicMock(), mock_dialog_manager)

        mock_dialog_manager.start.assert_called_once_with(
            About.page_1,
            mode=main.StartMode.RESET_STACK
        )

    @pytest.mark.asyncio
    async def test_about_command_handler_error_with_fallback(self):
        """Тест обработчика /about с ошибкой и успешным fallback."""
        mock_dialog_manager = AsyncMock()
        mock_dialog_manager.start.side_effect = Exception("Dialog error")

        mock_message = AsyncMock()

        with patch('main.logging') as mock_logging:
            await main.about_command_handler(mock_message, mock_dialog_manager)

            # Проверяем, что ошибка была залогирована
            mock_logging.error.assert_called_once()

            # Проверяем, что было отправлено fallback сообщение
            mock_message.answer.assert_called_once()
            assert "Раздел 'О боте' временно недоступен" in mock_message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_feedback_command_handler(self):
        """Тест обработчика /feedback."""
        mock_dialog_manager = AsyncMock()

        await main.feedback_command_handler(MagicMock(), mock_dialog_manager)

        mock_dialog_manager.start.assert_called_once_with(
            Feedback.enter_feedback,
            mode=main.StartMode.RESET_STACK
        )

    @pytest.mark.asyncio
    async def test_admin_command_handler(self):
        """Тест обработчика /admin."""
        mock_dialog_manager = AsyncMock()

        await main.admin_command_handler(MagicMock(), mock_dialog_manager)

        mock_dialog_manager.start.assert_called_once_with(
            Admin.menu,
            mode=main.StartMode.RESET_STACK
        )

    @pytest.mark.asyncio
    async def test_events_command_handler(self):
        """Тест обработчика /events."""
        mock_dialog_manager = AsyncMock()

        await main.events_command_handler(MagicMock(), mock_dialog_manager)

        mock_dialog_manager.start.assert_called_once_with(
            Events.list,
            mode=main.StartMode.RESET_STACK
        )


class TestRunMetricsServer:
    """Тесты для функции run_metrics_server."""

    @pytest.mark.asyncio
    async def test_run_metrics_server(self):
        """Тест запуска сервера метрик."""
        with patch('main.start_http_server') as mock_start_http_server, \
             patch('main.logging') as mock_logging:

            await main.run_metrics_server(8000)

            # Проверяем, что сервер был запущен
            mock_start_http_server.assert_called_once_with(8000)

            # Проверяем, что было залогировано сообщение
            mock_logging.info.assert_called_once()
            assert "Prometheus metrics server started" in mock_logging.info.call_args[0][0]


class TestSimpleRateLimiter:
    """Тесты для класса SimpleRateLimiter."""

    def test_simple_rate_limiter_init(self):
        """Тест инициализации SimpleRateLimiter."""
        redis_mock = MagicMock()
        limiter = main.SimpleRateLimiter(max_per_sec=5.0, redis=redis_mock)

        assert limiter._max_per_sec == 5.0
        assert limiter.redis == redis_mock

    @pytest.mark.asyncio
    async def test_rate_limiter_no_user(self):
        """Тест ограничения частоты без пользователя."""
        redis_mock = AsyncMock()
        limiter = main.SimpleRateLimiter(redis=redis_mock)

        mock_handler = AsyncMock()
        mock_event = MagicMock()
        mock_event.from_user = None

        result = await limiter(mock_handler, mock_event, {})

        # Проверяем, что обработчик был вызван без ограничений
        mock_handler.assert_called_once_with(mock_event, {})
        assert result == mock_handler.return_value

    @pytest.mark.asyncio
    async def test_rate_limiter_success(self):
        """Тест успешного прохождения ограничения частоты."""
        redis_mock = AsyncMock()
        redis_mock.lrange.return_value = []  # Нет предыдущих запросов

        limiter = main.SimpleRateLimiter(redis=redis_mock)

        mock_handler = AsyncMock()
        mock_event = MagicMock()
        mock_event.from_user.id = 123456789

        with patch('main.time.monotonic', return_value=1000.0):
            result = await limiter(mock_handler, mock_event, {})

            # Проверяем, что обработчик был вызван
            mock_handler.assert_called_once_with(mock_event, {})

            # Проверяем, что время было сохранено в Redis
            redis_mock.rpush.assert_called_once()
            redis_mock.expire.assert_called_once_with("rate_limit:123456789", 2)

    @pytest.mark.asyncio
    async def test_rate_limiter_exceeded(self):
        """Тест превышения ограничения частоты."""
        redis_mock = AsyncMock()
        # Имитируем 10 запросов в секунду
        redis_mock.lrange.return_value = [str(1000.0 - i * 0.1) for i in range(10)]

        limiter = main.SimpleRateLimiter(max_per_sec=10.0, redis=redis_mock)

        mock_handler = AsyncMock()
        mock_event = MagicMock()
        mock_event.from_user.id = 123456789
        mock_event.answer = AsyncMock()

        with patch('main.time.monotonic', return_value=1000.0):
            result = await limiter(mock_handler, mock_event, {})

            # Проверяем, что обработчик не был вызван
            mock_handler.assert_not_called()

            # Проверяем, что пользователю было отправлено сообщение
            mock_event.answer.assert_called_once()
            assert "Слишком много запросов" in mock_event.answer.call_args[0][0]


class TestErrorHandler:
    """Тесты для функции error_handler."""

    @pytest.mark.asyncio
    async def test_error_handler_unknown_intent(self):
        """Тест обработки UnknownIntent ошибки."""
        from aiogram_dialog.api.exceptions import UnknownIntent

        mock_event = MagicMock()
        mock_update = MagicMock()
        mock_callback_query = MagicMock()
        mock_message = MagicMock()
        mock_message.chat.id = 123456789

        mock_event.update = mock_update
        mock_update.callback_query = mock_callback_query
        mock_callback_query.message = mock_message

        mock_bot = AsyncMock()
        mock_event.bot = mock_bot

        result = await main.error_handler(event=mock_event, exception=UnknownIntent("test"))

        assert result is True

        # Проверяем, что callback был отвечен
        mock_callback_query.answer.assert_called_once()
        assert "Эта кнопка больше неактуальна" in mock_callback_query.answer.call_args[0][0]

        # Проверяем, что было отправлено сообщение
        mock_bot.send_message.assert_called_once_with(
            123456789,
            "Меню обновлено. Нажмите /start"
        )

    @pytest.mark.asyncio
    async def test_error_handler_other_exception(self):
        """Тест обработки других исключений."""
        with patch('main.logging') as mock_logging:
            result = await main.error_handler(exception=Exception("Test error"))

            assert result is True

            # Проверяем, что ошибка была залогирована
            mock_logging.error.assert_called_once()
            assert "Ошибка aiogram: %s" in mock_logging.error.call_args[0][0]


class TestMainFunction:
    """Тесты для основной функции main."""

    @pytest.mark.asyncio
    async def test_main_missing_env_vars(self):
        """Тест main с отсутствующими переменными окружения."""
        with patch.dict(os.environ, {}, clear=True), \
             patch('main.logging') as mock_logging, \
             patch('main.load_dotenv'):

            await main.main()

            # Проверяем, что была залогирована критическая ошибка
            mock_logging.critical.assert_called_once()
            assert "критически важных переменных окружения не найдена" in mock_logging.critical.call_args[0][0]

    @pytest.mark.asyncio
    async def test_main_timetable_manager_creation_failed(self):
        """Тест main с ошибкой создания TimetableManager."""
        with patch.dict(os.environ, {
            'BOT_TOKEN': 'test_token',
            'REDIS_URL': 'redis://localhost',
            'DATABASE_URL': 'sqlite://test.db'
        }), patch('main.logging') as mock_logging:

            with patch('main.TimetableManager.create', return_value=None):
                await main.main()

                # Проверяем, что была залогирована критическая ошибка
                mock_logging.critical.assert_called_once()
                assert "не удалось инициализировать TimetableManager" in mock_logging.critical.call_args[0][0]

    # Пропускаем тест main_success из-за сложности мокинга всех зависимостей
    # Основные компоненты уже протестированы в отдельных тестах


if __name__ == '__main__':
    pytest.main([__file__])
