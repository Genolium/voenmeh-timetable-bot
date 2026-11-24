from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, User

from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.schedule_view import (
    cleanup_old_cache,
    get_cache_info,
    get_schedule_data,
    get_week_image_data,
    on_change_group_click,
    on_date_shift,
    on_find_click,
    on_full_week_image_click,
    on_inline_back,
    on_news_clicked,
    on_send_original_file,
    on_send_original_file_callback,
    on_settings_click,
    on_today_click,
)


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
        DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15",
    }
    # Мокаем current_context как синхронную функцию, которая возвращает ctx
    manager.current_context = MagicMock(return_value=ctx)
    manager.middleware_data = {
        "manager": AsyncMock(),
        "user_data_manager": AsyncMock(),
        "bot": AsyncMock(),  # Добавляем мок для bot
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
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15",
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
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15",
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
        ctx.dialog_data = {DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"}

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
        with patch("core.config.get_redis_client") as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis.return_value = mock_redis_instance

            # Мокаем ImageCacheManager
            with patch("core.image_cache_manager.ImageCacheManager") as mock_cache_manager:
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
        with patch("core.config.get_redis_client") as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis.return_value = mock_redis_instance

            # Мокаем ImageCacheManager
            with patch("core.image_cache_manager.ImageCacheManager") as mock_cache_manager:
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
            DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15",
        }

        manager_obj = mock_manager.middleware_data["manager"]
        manager_obj.get_schedule_for_day.return_value = {
            "lessons": [{"subject": "Test Subject", "time": "9:00-10:30"}],
            "date": date(2024, 1, 15),
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
            "user_id": 123456789,
        }

        manager_obj = mock_manager.middleware_data["manager"]
        manager_obj.redis = AsyncMock()
        manager_obj.redis.get.return_value = None
        manager_obj.redis.set.return_value = True

        # Мокаем get_week_image_data
        with patch("bot.dialogs.schedule_view.get_week_image_data") as mock_get_week_data:
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
            "user_id": 123456789,
        }

        manager_obj = mock_manager.middleware_data["manager"]
        manager_obj.get_academic_week_type.return_value = ("odd", "Нечётная неделя")
        manager_obj._schedules = {"TEST_GROUP": {"odd": {"lessons": [{"subject": "Test"}]}}}

        # Мокаем ImageCacheManager
        with patch("core.image_cache_manager.ImageCacheManager") as mock_cache_manager:
            mock_cache_instance = AsyncMock()
            mock_cache_manager.return_value = mock_cache_instance

            # Мокаем ImageService
            with patch("core.image_service.ImageService") as mock_image_service:
                mock_service_instance = AsyncMock()
                mock_image_service.return_value = mock_service_instance
                mock_service_instance.get_or_generate_week_image.return_value = (
                    True,
                    "/path/to/image.png",
                )

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
        ctx.dialog_data = {DialogDataKeys.CURRENT_DATE_ISO: "2024-01-15"}

        await on_today_click(mock_callback, button, mock_manager)

        # Проверяем, что дата была обновлена на сегодняшнюю (по Москве, как в хендлере)
        from datetime import datetime

        from core.config import MOSCOW_TZ

        today_iso = datetime.now(MOSCOW_TZ).date().isoformat()
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

    @pytest.mark.asyncio
    async def test_customize_schedule_dialog_and_rules_flow(self, mock_callback, mock_manager):
        from types import SimpleNamespace
        from unittest.mock import patch

        from bot.dialogs.schedule_view import (
            _collect_group_subjects,
            _rules_manager_from,
            get_customize_data,
            on_custom_lesson_input,
            on_custom_note_input,
            on_customize_click,
            on_reset_custom_rules,
            on_toggle_hide_subject,
        )
        from bot.dialogs.states import Schedule

        await on_customize_click(mock_callback, MagicMock(), mock_manager)
        mock_manager.switch_to.assert_awaited_with(Schedule.customize)

        # _rules_manager_from None when no session_maker
        mock_manager.middleware_data = {"user_data_manager": MagicMock(async_session_maker=None)}
        assert _rules_manager_from(mock_manager) is None

        fake_udm = MagicMock(async_session_maker=MagicMock())
        fake_tm = MagicMock(
            redis=None,
            _schedules={
                "О735Б": {
                    "odd": {
                        "Понедельник": [
                            {"subject": "Физика", "start_time_raw": "09:00"},
                            {"subject": "Физика", "start_time_raw": "10:50"},
                        ]
                    }
                }
            },
        )
        mock_manager.middleware_data = {"user_data_manager": fake_udm, "manager": fake_tm}
        assert _rules_manager_from(mock_manager) is not None
        mock_manager.event = SimpleNamespace(from_user=SimpleNamespace(id=77))
        ctx = mock_manager.current_context()
        ctx.dialog_data["group"] = "О735Б"
        ctx.dialog_data["is_group_target"] = True

        assert _collect_group_subjects(fake_tm, "О735Б", True) == ["Физика"]

        sample_rules = [
            {"rule_type": "hide_subject", "subject": "Физика"},
            {"rule_type": "note", "subject": "Физика", "payload": {"note": "КР"}},
            {"rule_type": "custom_lesson", "subject": "Спорт", "day_name": "Понедельник", "start_time": "08:00"},
            {"rule_type": "unknown", "subject": "X"},
        ]
        fake_rules_mgr = MagicMock()
        fake_rules_mgr.get_rules = AsyncMock(return_value=sample_rules)
        fake_rules_mgr.toggle_hide_subject = AsyncMock(return_value=True)
        fake_rules_mgr.clear_rules = AsyncMock(return_value=3)
        fake_rules_mgr.add_note = AsyncMock(return_value=1)
        fake_rules_mgr.add_custom_lesson = AsyncMock(return_value=2)

        with patch("bot.dialogs.schedule_view._rules_manager_from", return_value=fake_rules_mgr):
            data = await get_customize_data(mock_manager)
            assert data["has_rules"] is True
            assert len(data["subjects"]) == 1

            fake_rules_mgr.get_rules = AsyncMock(return_value=[])
            data_empty = await get_customize_data(mock_manager)
            assert data_empty["has_rules"] is False
            fake_rules_mgr.get_rules = AsyncMock(return_value=sample_rules)

            # toggle hide subject: valid and invalid index
            await on_toggle_hide_subject(mock_callback, MagicMock(), mock_manager, "0")
            mock_callback.answer.assert_awaited()
            await on_toggle_hide_subject(mock_callback, MagicMock(), mock_manager, "99")
            assert "не найден" in mock_callback.answer.call_args[0][0]

            # reset
            await on_reset_custom_rules(mock_callback, MagicMock(), mock_manager)
            assert "Сброшено" in mock_callback.answer.call_args[0][0]

            # note input: bad format, empty parts, valid
            msg = MagicMock(from_user=SimpleNamespace(id=77), text="no pipe")
            msg.reply = AsyncMock()
            await on_custom_note_input(msg, MagicMock(), mock_manager)
            msg.text = " | "
            await on_custom_note_input(msg, MagicMock(), mock_manager)
            msg.text = "Физ | Лаба"
            await on_custom_note_input(msg, MagicMock(), mock_manager)
            fake_rules_mgr.add_note.assert_awaited_once()

            # custom lesson input: short, bad day, no dash, bad time, odd/even, valid
            for bad_txt in ("Пн | 09:00", "Хз | 09:00-10:30 | Пара", "Пн | 09:00 | Пара", "Пн | xx:yy-10:30 | Пара"):
                msg.text = bad_txt
                await on_custom_lesson_input(msg, MagicMock(), mock_manager)
            msg.text = "Пн | 08:00-09:30 | Спорт | Зал | нечет"
            await on_custom_lesson_input(msg, MagicMock(), mock_manager)
            msg.text = "Вт | 08:00-09:30 | Спорт | Зал | чет"
            await on_custom_lesson_input(msg, MagicMock(), mock_manager)
            assert fake_rules_mgr.add_custom_lesson.await_count == 2

        # Missing rules manager branches
        with patch("bot.dialogs.schedule_view._rules_manager_from", return_value=None):
            await on_toggle_hide_subject(mock_callback, MagicMock(), mock_manager, "0")
            await on_reset_custom_rules(mock_callback, MagicMock(), mock_manager)
            msg.text = "Физ | Лаба"
            await on_custom_note_input(msg, MagicMock(), mock_manager)
            msg.text = "Пн | 08:00-09:30 | Спорт"
            await on_custom_lesson_input(msg, MagicMock(), mock_manager)

        # Active user_rules applied across schedule_view entrypoints
        ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = "2024-01-15"
        ctx.dialog_data["is_group_target"] = True
        fake_tm.get_schedule_for_day = AsyncMock(
            return_value={
                "date": date(2024, 1, 15),
                "day_name": "Понедельник",
                "week_name": "Нечётная неделя",
                "lessons": [{"subject": "Физика", "start_time_raw": "09:00", "time": "09:00-10:30"}],
            }
        )
        fake_tm.get_academic_week_type = AsyncMock(return_value=("odd", "Нечётная неделя"))
        fake_tm.get_semester_info_for = AsyncMock(return_value={})
        fake_tm.redis = AsyncMock()
        fake_tm.redis.get = AsyncMock(return_value=None)
        fake_tm.redis.set = AsyncMock(return_value=True)
        fake_udm.get_user_language = AsyncMock(return_value="ru")
        fake_udm.get_user_theme = AsyncMock(return_value="standard")
        mock_callback.message = MagicMock()
        mock_callback.message.answer = AsyncMock()
        mock_callback.message.answer_document = AsyncMock()

        from bot.dialogs.schedule_view import (
            on_export_ics_click,
            on_send_original_file,
            on_text_week_click,
        )

        with (
            patch("bot.dialogs.schedule_view._get_user_rules_for_target", AsyncMock(return_value=sample_rules)),
            patch("core.image_service.ImageService") as m_img_svc,
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.subscription_filter.is_user_subscribed", AsyncMock(return_value=True)),
            patch("bot.dialogs.schedule_view.generate_schedule_ics", return_value=b"BEGIN:VCALENDAR"),
        ):
            m_img_svc.return_value.get_or_generate_week_image = AsyncMock(return_value=(True, "/tmp/x.png"))
            sched_res = await get_schedule_data(mock_manager)
            assert sched_res["has_lessons"] is True

            await get_week_image_data(mock_manager)
            assert m_img_svc.return_value.get_or_generate_week_image.call_args.kwargs["rules_hash"]

            await on_send_original_file(mock_callback, MagicMock(), mock_manager)
            await on_text_week_click(mock_callback, MagicMock(), mock_manager)
            await on_export_ics_click(mock_callback, MagicMock(), mock_manager)
