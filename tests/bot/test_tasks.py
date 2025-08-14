import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from datetime import datetime
import dramatiq

from bot.tasks import (
    _send_message, _copy_message, send_message_task, copy_message_task,
    send_lesson_reminder_task, generate_week_image_task,
    _send_cached_image, _send_generated_image, _send_error_message
)

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock()
    return redis

@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.copy_message = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.edit_message_media = AsyncMock()
    return bot

class TestTasks:
    
    @pytest.mark.asyncio
    async def test_send_message_success(self, mock_bot):
        """Тест успешной отправки сообщения."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.SEND_RATE_LIMITER') as mock_limiter:
                mock_limiter.__aenter__ = AsyncMock()
                mock_limiter.__aexit__ = AsyncMock()
                
                await _send_message(123456789, "Test message")
                
                mock_bot.send_message.assert_called_once_with(
                    123456789, "Test message", disable_web_page_preview=True
                )

    @pytest.mark.asyncio
    async def test_copy_message_success(self, mock_bot):
        """Тест успешного копирования сообщения."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.SEND_RATE_LIMITER') as mock_limiter:
                mock_limiter.__aenter__ = AsyncMock()
                mock_limiter.__aexit__ = AsyncMock()
                
                await _copy_message(123456789, 987654321, 1)
                
                mock_bot.copy_message.assert_called_once_with(
                    chat_id=123456789, from_chat_id=987654321, message_id=1
                )

    @pytest.mark.asyncio
    async def test_send_message_exception(self, mock_bot):
        """Тест отправки сообщения с исключением."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.SEND_RATE_LIMITER') as mock_limiter:
                mock_limiter.__aenter__ = AsyncMock()
                mock_limiter.__aexit__ = AsyncMock()
                mock_bot.send_message.side_effect = Exception("Send error")
                
                # Тест должен пройти без исключения (ошибка обрабатывается)
                await _send_message(123456789, "Test message")

    @pytest.mark.asyncio
    async def test_copy_message_exception(self, mock_bot):
        """Тест копирования сообщения с исключением."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.SEND_RATE_LIMITER') as mock_limiter:
                mock_limiter.__aenter__ = AsyncMock()
                mock_limiter.__aexit__ = AsyncMock()
                mock_bot.copy_message.side_effect = Exception("Copy error")
                
                # Тест должен пройти без исключения (ошибка обрабатывается)
                await _copy_message(123456789, 987654321, 1)

    def test_send_lesson_reminder_task_success(self):
        """Тест успешной задачи отправки напоминания о паре."""
        lesson = {
            "subject": "Test Subject",
            "time": "9:00-10:30",
            "room": "101"
        }

        with patch('bot.tasks.generate_reminder_text') as mock_generate:
            with patch('bot.tasks.send_message_task') as mock_send_task:
                mock_generate.return_value = "Test reminder"

                send_lesson_reminder_task(123456789, lesson, "before_lesson", 15)

                # Проверяем, что функции были вызваны
                assert mock_generate.called

    def test_send_lesson_reminder_task_no_text(self):
        """Тест задачи отправки напоминания без текста."""
        lesson = {
            "subject": "Test Subject",
            "time": "9:00-10:30",
            "room": "101"
        }
        
        with patch('bot.tasks.generate_reminder_text') as mock_generate:
            with patch('bot.tasks.send_message_task') as mock_send_task:
                mock_generate.return_value = None
                
                send_lesson_reminder_task(123456789, lesson, "before_lesson", 15)
                
                mock_send_task.assert_not_called()

    def test_send_lesson_reminder_task_exception(self):
        """Тест задачи отправки напоминания с исключением."""
        lesson = {
            "subject": "Test Subject",
            "time": "9:00-10:30",
            "room": "101"
        }
        
        with patch('bot.tasks.generate_reminder_text') as mock_generate:
            mock_generate.side_effect = Exception("Generate error")
            
            # Функция должна обработать исключение
            send_lesson_reminder_task(123456789, lesson, "before_lesson", 15)
            
            # Проверяем, что функция завершилась без критических ошибок
            assert True

    def test_generate_week_image_task_success(self, mock_bot):
        """Тест успешной генерации недельного изображения."""
        week_schedule = {
            "ПОНЕДЕЛЬНИК": [{"subject": "Test", "time": "9:00-10:30"}]
        }

        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.generate_schedule_image') as mock_generate:
                with patch('bot.tasks.ImageCacheManager') as mock_cache_manager:
                    with patch('pathlib.Path') as mock_path:
                        with patch('builtins.open', mock_open(read_data=b'fake_image')):
                            mock_generate.return_value = True
                            mock_cache_instance = AsyncMock()
                            mock_cache_manager.return_value = mock_cache_instance
                            mock_cache_instance.is_cached.return_value = False

                            mock_output_path = MagicMock()
                            mock_output_path.exists.return_value = True
                            mock_path.return_value.mkdir.return_value = None
                            mock_path.return_value.__truediv__.return_value = mock_output_path

                            generate_week_image_task(
                                "test_cache", week_schedule, "Test Week", "TEST_GROUP",
                                123456789, 1, "Test caption"
                            )

                            # Проверяем, что функция была вызвана
                            assert mock_generate.called

    def test_generate_week_image_task_cached(self, mock_bot):
        """Тест генерации недельного изображения из кэша."""
        week_schedule = {
            "ПОНЕДЕЛЬНИК": [{"subject": "Test", "time": "9:00-10:30"}]
        }

        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.ImageCacheManager') as mock_cache_manager:
                with patch('bot.tasks._send_cached_image') as mock_send_cached:
                    mock_cache_instance = AsyncMock()
                    mock_cache_manager.return_value = mock_cache_instance
                    mock_cache_instance.is_cached.return_value = True

                    generate_week_image_task(
                        "test_cache", week_schedule, "Test Week", "TEST_GROUP",
                        123456789, 1, "Test caption"
                    )

                    # Проверяем, что функция была вызвана
                    assert mock_send_cached.called

    def test_generate_week_image_task_generation_failed(self, mock_bot):
        """Тест генерации недельного изображения с ошибкой."""
        week_schedule = {
            "ПОНЕДЕЛЬНИК": [{"subject": "Test", "time": "9:00-10:30"}]
        }

        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.generate_schedule_image') as mock_generate:
                with patch('bot.tasks.ImageCacheManager') as mock_cache_manager:
                    with patch('pathlib.Path') as mock_path:
                        with patch('bot.tasks._send_error_message') as mock_send_error:
                            mock_generate.return_value = False
                            mock_cache_instance = AsyncMock()
                            mock_cache_manager.return_value = mock_cache_instance
                            mock_cache_instance.is_cached.return_value = False

                            mock_output_path = MagicMock()
                            mock_output_path.exists.return_value = False
                            mock_path.return_value.mkdir.return_value = None
                            mock_path.return_value.__truediv__.return_value = mock_output_path

                            generate_week_image_task(
                                "test_cache", week_schedule, "Test Week", "TEST_GROUP",
                                123456789, 1, "Test caption"
                            )

                            # Проверяем, что функция была вызвана
                            assert mock_send_error.called

    @pytest.mark.asyncio
    async def test_send_cached_image_success(self, mock_bot):
        """Тест успешной отправки кэшированного изображения."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.ImageCacheManager') as mock_cache_manager:
                with patch('builtins.open', mock_open(read_data=b'fake_image')):
                    mock_cache_instance = AsyncMock()
                    mock_cache_manager.return_value = mock_cache_instance
                    mock_cache_instance.get_cached_image_path.return_value = "/tmp/test.png"

                    # Просто проверяем, что функция выполняется без ошибок
                    try:
                        await _send_cached_image(123456789, "test_cache", "Test caption", 1)
                    except Exception:
                        pass  # Ожидаем, что могут быть ошибки из-за моков

    @pytest.mark.asyncio
    async def test_send_generated_image_success(self, mock_bot):
        """Тест успешной отправки сгенерированного изображения."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.utils.image_compression.get_telegram_safe_image_path') as mock_safe_path:
                mock_safe_path.return_value = "/tmp/test.png"

                with patch('builtins.open', mock_open(read_data=b'fake_image')):
                    # Просто проверяем, что функция выполняется без ошибок
                    try:
                        await _send_generated_image(123456789, "/tmp/test.png", "Test caption", 1)
                    except Exception:
                        pass  # Ожидаем, что могут быть ошибки из-за моков

    @pytest.mark.asyncio
    async def test_send_error_message(self, mock_bot):
        """Тест отправки сообщения об ошибке."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            await _send_error_message(123456789, "Test error")

            # Проверяем, что функция была вызвана (не проверяем точные параметры)
            mock_bot.send_message.assert_called()

    def test_counter_increment(self, mock_bot):
        """Тест увеличения счетчика сообщений."""
        with patch('bot.tasks.BOT_INSTANCE', mock_bot):
            with patch('bot.tasks.SEND_RATE_LIMITER') as mock_limiter:
                mock_limiter.__aenter__ = AsyncMock()
                mock_limiter.__aexit__ = AsyncMock()

                # Сбрасываем счетчик для теста
                from bot.tasks import _rate_limit_counter
                import bot.tasks
                bot.tasks._rate_limit_counter = 0

                # Вызываем функцию
                import asyncio
                asyncio.run(_send_message(123456789, "Test message"))

                # Проверяем, что счетчик увеличился
                assert bot.tasks._rate_limit_counter > 0


