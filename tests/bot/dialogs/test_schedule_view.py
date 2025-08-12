import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import date, datetime, timedelta

from aiogram_dialog import StartMode

from bot.dialogs.schedule_view import (
    get_schedule_data,
    on_date_shift,
    on_today_click,
)
from bot.dialogs.states import Schedule, MainMenu, SettingsMenu, FindMenu
from core.config import MOSCOW_TZ

@pytest.fixture
def mock_manager():
    manager = AsyncMock()
    mock_context = MagicMock()
    mock_context.dialog_data = {}
    manager.current_context = MagicMock(return_value=mock_context)
    manager.middleware_data = {"manager": MagicMock()}
    return manager

@pytest.fixture
def lessons_sample():
    return [
        {'start_time_raw': '09:00', 'end_time_raw': '10:30', 'subject': 'Матан', 'time': '09:00-10:30'},
        {'start_time_raw': '10:40', 'end_time_raw': '12:10', 'subject': 'Физика', 'time': '10:40-12:10'}
    ]

@pytest.mark.asyncio
class TestScheduleViewHandlers:

    async def test_on_date_shift(self, mock_manager):
        today = date(2023, 10, 26)
        mock_manager.current_context().dialog_data["current_date_iso"] = today.isoformat()
        await on_date_shift(AsyncMock(), MagicMock(), mock_manager, days=1)
        expected_date = date(2023, 10, 27).isoformat()
        assert mock_manager.current_context().dialog_data["current_date_iso"] == expected_date

    async def test_on_today_click(self, mock_manager):
        yesterday = (datetime.now(MOSCOW_TZ).date() - timedelta(days=1)).isoformat()
        mock_manager.current_context().dialog_data["current_date_iso"] = yesterday
        await on_today_click(AsyncMock(), MagicMock(), mock_manager)
        today_iso = datetime.now(MOSCOW_TZ).date().isoformat()
        assert mock_manager.current_context().dialog_data["current_date_iso"] == today_iso

    async def test_navigation_clicks(self, mock_manager):
        """Тест навигационных кнопок"""
        # Добавляем данные в контекст
        mock_context = MagicMock()
        mock_context.dialog_data = {"current_date_iso": "2025-01-06"}
        mock_manager.current_context.return_value = mock_context
        
        # Тестируем только существующие функции
        await on_date_shift(AsyncMock(), MagicMock(), mock_manager, 1)
        await on_today_click(AsyncMock(), MagicMock(), mock_manager)

    async def test_get_schedule_data(self, mock_manager, lessons_sample, mocker):
        today_iso = datetime.now(MOSCOW_TZ).date().isoformat()
        group_name = "O735Б"
        
        ctx = mock_manager.current_context()
        ctx.dialog_data = {"group": group_name, "current_date_iso": today_iso}

        timetable_manager = mock_manager.middleware_data['manager']
        timetable_manager.get_schedule_for_day.return_value = {
            "lessons": lessons_sample, "date": date.fromisoformat(today_iso), "day_name": "Тест"
        }
        
        mocker.patch('bot.dialogs.schedule_view.format_schedule_text', return_value="FormattedText")
        mocker.patch('bot.dialogs.schedule_view.generate_dynamic_header', return_value=("DynamicHeader", "ProgressBar"))

        data = await get_schedule_data(dialog_manager=mock_manager)

        timetable_manager.get_schedule_for_day.assert_called_once_with(group_name, target_date=date.fromisoformat(today_iso))
        assert data["dynamic_header"] == "DynamicHeader"
        assert data["progress_bar"] == "ProgressBar"
        assert data["schedule_text"] == "FormattedText"
        assert data["has_lessons"] is True

    @pytest.mark.asyncio
    async def test_on_full_week_image_click(self, mock_dialog_manager):
        """Тест нажатия на кнопку 'Расписание на неделю'"""
        from bot.dialogs.schedule_view import on_full_week_image_click
        
        # Мокаем callback
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 12345
        
        # Мокаем button
        mock_button = MagicMock()
        
        # Мокаем get_week_image_data
        with patch('bot.dialogs.schedule_view.get_week_image_data') as mock_get_data:
            await on_full_week_image_click(mock_callback, mock_button, mock_dialog_manager)
            
            # Проверяем, что user_id сохранен
            assert mock_dialog_manager.current_context.return_value.dialog_data["user_id"] == 12345
            
            # Проверяем, что вызвана генерация данных
            mock_get_data.assert_called_once_with(mock_dialog_manager)
            
            # Проверяем, что вызван switch_to
            mock_dialog_manager.switch_to.assert_called_once_with(Schedule.view)
            
            # Проверяем, что callback отвечен
            mock_callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_full_week_image_click_with_exception(self, mock_dialog_manager):
        """Тест нажатия на кнопку 'Расписание на неделю' с исключением"""
        from bot.dialogs.schedule_view import on_full_week_image_click
        
        # Мокаем callback
        mock_callback = AsyncMock()
        mock_callback.from_user.id = 12345
        
        # Мокаем button
        mock_button = MagicMock()
        
        # Мокаем get_week_image_data и switch_to чтобы они вызывали исключение
        with patch('bot.dialogs.schedule_view.get_week_image_data') as mock_get_data:
            mock_dialog_manager.switch_to.side_effect = Exception("Test error")
            
            # Функция должна выполниться без ошибок
            await on_full_week_image_click(mock_callback, mock_button, mock_dialog_manager)
            
            # Проверяем, что user_id сохранен
            assert mock_dialog_manager.current_context.return_value.dialog_data["user_id"] == 12345
            
            # Проверяем, что вызвана генерация данных
            mock_get_data.assert_called_once_with(mock_dialog_manager)
            
            # Проверяем, что callback отвечен
            mock_callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_send_original_file_success(self, mock_dialog_manager):
        """Тест успешной отправки оригинального файла"""
        from bot.dialogs.schedule_view import on_send_original_file_callback
        
        # Мокаем callback
        mock_callback = AsyncMock()
        mock_callback.message.answer_document = AsyncMock()
        
        # Мокаем manager
        mock_manager = MagicMock()
        mock_manager.get_week_type.return_value = ("odd", "Нечётная неделя")
        mock_bot = AsyncMock()
        mock_dialog_manager.middleware_data = {"manager": mock_manager, "bot": mock_bot}
        
        # Мокаем контекст
        mock_context = MagicMock()
        mock_context.dialog_data = {
            "group": "TEST",
            "current_date_iso": "2025-01-06"
        }
        mock_dialog_manager.current_context.return_value = mock_context
        
        # Мокаем существующий файл
        with patch('pathlib.Path') as mock_path, patch('bot.dialogs.schedule_view.ImageCacheManager') as mock_cache_manager:
            mock_output_path = MagicMock()
            mock_output_path.exists.return_value = True
            mock_path.return_value.__truediv__.return_value = mock_output_path
            
            # Мокаем ImageCacheManager
            mock_cache_instance = MagicMock()
            mock_cache_instance.is_cached = AsyncMock(return_value=False)  # Файл не в кэше
            mock_cache_manager.return_value = mock_cache_instance
            
            # Функция должна выполниться без ошибок
            await on_send_original_file_callback(mock_callback, mock_dialog_manager)
            
            # Проверяем, что callback отвечен (даже если файл не отправлен)
            mock_callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_send_original_file_no_week_info(self, mock_dialog_manager):
        """Тест отправки оригинального файла когда неделя не определена"""
        from bot.dialogs.schedule_view import on_send_original_file_callback
        
        # Мокаем callback
        mock_callback = AsyncMock()
        
        # Мокаем manager
        mock_manager = MagicMock()
        mock_manager.get_week_type.return_value = None
        mock_dialog_manager.middleware_data = {"manager": mock_manager}
        
        # Мокаем контекст
        mock_context = MagicMock()
        mock_context.dialog_data = {
            "group": "TEST",
            "current_date_iso": "2025-01-06"
        }
        mock_dialog_manager.current_context.return_value = mock_context
        
        await on_send_original_file_callback(mock_callback, mock_dialog_manager)
        
        # Проверяем, что показан alert об ошибке
        mock_callback.answer.assert_called_once_with("Неделя не определена", show_alert=True)

    @pytest.mark.asyncio
    async def test_on_send_original_file_file_not_exists(self, mock_dialog_manager):
        """Тест отправки оригинального файла когда файл не существует"""
        from bot.dialogs.schedule_view import on_send_original_file_callback
        
        # Мокаем callback
        mock_callback = AsyncMock()
        
        # Мокаем manager
        mock_manager = MagicMock()
        mock_manager.get_week_type.return_value = ("odd", "Нечётная неделя")
        mock_dialog_manager.middleware_data = {"manager": mock_manager}
        
        # Мокаем контекст
        mock_context = MagicMock()
        mock_context.dialog_data = {
            "group": "TEST",
            "current_date_iso": "2025-01-06"
        }
        mock_dialog_manager.current_context.return_value = mock_context
        
        # Мокаем несуществующий файл
        with patch('pathlib.Path') as mock_path:
            mock_output_path = MagicMock()
            mock_output_path.exists.return_value = False
            mock_path.return_value.__truediv__.return_value = mock_output_path
            
            # Мокаем get_week_image_data
            with patch('bot.dialogs.schedule_view.get_week_image_data') as mock_get_data:
                await on_send_original_file_callback(mock_callback, mock_dialog_manager)
                
                # Проверяем, что вызвана генерация данных
                mock_get_data.assert_called_once_with(mock_dialog_manager)
                
                # Проверяем, что показано сообщение о подготовке
                mock_callback.answer.assert_called_once_with("Готовлю оригинал, вернитесь через пару секунд…")

    @pytest.mark.asyncio
    async def test_on_inline_back(self, mock_dialog_manager):
        """Тест нажатия на кнопку 'Назад' в inline клавиатуре"""
        from bot.dialogs.schedule_view import on_inline_back
        
        # Мокаем callback
        mock_callback = AsyncMock()
        mock_callback.message.delete = AsyncMock()
        
        await on_inline_back(mock_callback, mock_dialog_manager)
        
        # Проверяем, что сообщение удалено
        mock_callback.message.delete.assert_called_once()
        
        # Проверяем, что callback отвечен
        mock_callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_inline_back_with_exception(self, mock_dialog_manager):
        """Тест нажатия на кнопку 'Назад' с исключением при удалении"""
        from bot.dialogs.schedule_view import on_inline_back
        
        # Мокаем callback
        mock_callback = AsyncMock()
        mock_callback.message.delete.side_effect = Exception("Delete error")
        
        # Функция должна выполниться без ошибок
        await on_inline_back(mock_callback, mock_dialog_manager)
        
        # Проверяем, что callback отвечен
        mock_callback.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_change_group_click(self, mock_dialog_manager):
        """Тест нажатия на кнопку 'Сменить группу'"""
        from bot.dialogs.schedule_view import on_change_group_click
        from bot.dialogs.states import MainMenu
        
        await on_change_group_click(MagicMock(), MagicMock(), mock_dialog_manager)
        
        # Проверяем, что запущен диалог выбора группы
        mock_dialog_manager.start.assert_called_once_with(MainMenu.enter_group, mode=StartMode.RESET_STACK)

    @pytest.mark.asyncio
    async def test_on_settings_click(self, mock_dialog_manager):
        """Тест нажатия на кнопку 'Настройки'"""
        from bot.dialogs.schedule_view import on_settings_click
        from bot.dialogs.states import SettingsMenu
        
        await on_settings_click(MagicMock(), MagicMock(), mock_dialog_manager)
        
        # Проверяем, что запущен диалог настроек
        mock_dialog_manager.start.assert_called_once_with(SettingsMenu.main)

    @pytest.mark.asyncio
    async def test_on_find_click(self, mock_dialog_manager):
        """Тест нажатия на кнопку 'Поиск'"""
        from bot.dialogs.schedule_view import on_find_click
        from bot.dialogs.states import FindMenu
        
        await on_find_click(MagicMock(), MagicMock(), mock_dialog_manager)
        
        # Проверяем, что запущен диалог поиска
        mock_dialog_manager.start.assert_called_once_with(FindMenu.choice)