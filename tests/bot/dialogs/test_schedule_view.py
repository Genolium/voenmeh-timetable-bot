import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime
from aiogram.types import CallbackQuery, User

from bot.dialogs.schedule_view import (
    on_send_original_file_callback, on_send_original_file, on_date_shift, on_news_clicked,
    cleanup_old_cache, get_cache_info, get_schedule_data, on_full_week_image_click,
    get_week_image_data, on_today_click, on_change_group_click, on_settings_click,
    on_find_click, on_inline_back
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
        # Мокаем get_academic_week_type как асинхронную функцию
        manager_obj.get_academic_week_type = AsyncMock(return_value=("odd", "Нечётная неделя"))
        
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
        # Мокаем get_academic_week_type как асинхронную функцию
        manager_obj.get_academic_week_type = AsyncMock(return_value=("odd", "Нечётная неделя"))
        
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

    @pytest.mark.asyncio
    async def test_cleanup_old_cache(self):
        """Тест очистки старого кэша."""
        with patch('core.config.get_redis_client') as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis.return_value = mock_redis_instance

            # Мокаем ImageCacheManager
            with patch('core.image_cache_manager.ImageCacheManager') as mock_cache_manager:
                mock_cache_instance = AsyncMock()
                mock_cache_manager.return_value = mock_cache_instance
                mock_cache_instance.get_cache_stats.return_value = {"total_size": 100}

                await cleanup_old_cache()

                # Проверяем, что методы были вызваны
                mock_redis_instance.keys.assert_called()
                mock_cache_instance.get_cache_stats.assert_called()

    @pytest.mark.asyncio
    async def test_get_cache_info(self):
        """Тест получения информации о кэше."""
        with patch('core.config.get_redis_client') as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis.return_value = mock_redis_instance

            # Мокаем ImageCacheManager
            with patch('core.image_cache_manager.ImageCacheManager') as mock_cache_manager:
                mock_cache_instance = AsyncMock()
                mock_cache_manager.return_value = mock_cache_instance
                mock_cache_instance.get_cache_stats.return_value = {"total_size": 100}

                result = await get_cache_info()

                assert isinstance(result, dict)
                assert "total_size_mb" in result
                assert "redis_keys" in result

    @pytest.mark.asyncio
    async def test_get_schedule_data(self, mock_manager):
        """Тест получения данных расписания."""
        # Настраиваем моки
        ctx = mock_manager.current_context()
        ctx.dialog_data = {
            DialogDataKeys.GROUP: "TEST_GROUP",
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"
        }

        manager_obj = mock_manager.middleware_data["manager"]
        manager_obj.get_schedule_for_day.return_value = {
            "lessons": [{"subject": "Test Subject", "time": "9:00-10:30"}],
            "date": date(2024, 1, 15)
        }

        # Мокаем UserDataManager
        user_data_manager = mock_manager.middleware_data["user_data_manager"]
        user_data_manager.get_users_for_lesson_reminders.return_value = []

        result = await get_schedule_data(mock_manager)

        assert "dynamic_header" in result
        assert "progress_bar" in result
        assert "schedule_text" in result
        assert "has_lessons" in result
        assert result["has_lessons"] is True

    @pytest.mark.asyncio
    async def test_on_full_week_image_click(self, mock_callback, mock_manager):
        """Тест клика по изображению недели."""
        button = MagicMock()

        # Настраиваем моки
        ctx = mock_manager.current_context()
        ctx.dialog_data = {
            DialogDataKeys.GROUP: "TEST_GROUP",
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15",
            "user_id": 123456789
        }

        manager_obj = mock_manager.middleware_data["manager"]
        manager_obj.redis = AsyncMock()
        manager_obj.redis.get.return_value = None
        manager_obj.redis.set.return_value = True

        # Мокаем get_week_image_data
        with patch('bot.dialogs.schedule_view.get_week_image_data') as mock_get_week_data:
            mock_get_week_data.return_value = {"week_name": "Test Week"}

            await on_full_week_image_click(mock_callback, button, mock_manager)

            mock_callback.answer.assert_called()
            mock_manager.switch_to.assert_called()

    @pytest.mark.asyncio
    async def test_get_week_image_data(self, mock_manager):
        """Тест получения данных изображения недели."""
        # Настраиваем моки
        ctx = mock_manager.current_context()
        ctx.dialog_data = {
            DialogDataKeys.GROUP: "TEST_GROUP",
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15",
            "user_id": 123456789
        }

        manager_obj = mock_manager.middleware_data["manager"]
        manager_obj.get_academic_week_type.return_value = ("odd", "Нечётная неделя")
        manager_obj._schedules = {
            "TEST_GROUP": {
                "odd": {"lessons": [{"subject": "Test"}]}
            }
        }

        # Мокаем ImageCacheManager
        with patch('core.image_cache_manager.ImageCacheManager') as mock_cache_manager:
            mock_cache_instance = AsyncMock()
            mock_cache_manager.return_value = mock_cache_instance

            # Мокаем ImageService
            with patch('core.image_service.ImageService') as mock_image_service:
                mock_service_instance = AsyncMock()
                mock_image_service.return_value = mock_service_instance
                mock_service_instance.get_or_generate_week_image.return_value = (True, "/path/to/image.png")

                result = await get_week_image_data(mock_manager)

                assert "week_name" in result
                assert "group" in result
                assert "start_date" in result
                assert "end_date" in result

    @pytest.mark.asyncio
    async def test_on_today_click(self, mock_callback, mock_manager):
        """Тест клика по кнопке 'сегодня'."""
        button = MagicMock()

        # Настраиваем моки
        ctx = mock_manager.current_context()
        ctx.dialog_data = {
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"
        }

        await on_today_click(mock_callback, button, mock_manager)

        # Проверяем, что дата была обновлена на сегодняшнюю
        from datetime import datetime
        today_iso = datetime.now().date().isoformat()
        assert ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == today_iso

    @pytest.mark.asyncio
    async def test_on_change_group_click(self, mock_callback, mock_manager):
        """Тест клика по кнопке смены группы."""
        button = MagicMock()

        await on_change_group_click(mock_callback, button, mock_manager)

        mock_manager.start.assert_called()

    @pytest.mark.asyncio
    async def test_on_settings_click(self, mock_callback, mock_manager):
        """Тест клика по кнопке настроек."""
        button = MagicMock()

        await on_settings_click(mock_callback, button, mock_manager)

        mock_manager.start.assert_called()

    @pytest.mark.asyncio
    async def test_on_find_click(self, mock_callback, mock_manager):
        """Тест клика по кнопке поиска."""
        button = MagicMock()

        await on_find_click(mock_callback, button, mock_manager)

        mock_manager.start.assert_called()

    @pytest.mark.asyncio
    async def test_on_inline_back(self, mock_callback, mock_manager):
        """Тест клика по кнопке 'назад'."""
        # Настраиваем мок для message
        mock_callback.message = AsyncMock()

        await on_inline_back(mock_callback, mock_manager)

        mock_callback.message.delete.assert_called()
        mock_callback.answer.assert_called()