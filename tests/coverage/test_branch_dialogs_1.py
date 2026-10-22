"""Дополнительные тесты для 100% branch coverage модулей ``bot/dialogs/*``.

Файл закрывает непокрытые пары ветвей (line -> line) из отчёта ``coverage.py``
с флагом ``--cov-branch``:

* ``bot/dialogs/admin_menu.py``   — 212->exit, 400->409, 1015->1018
* ``bot/dialogs/feedback_menu.py`` — 76->82
* ``bot/dialogs/find_menu.py``    — 72->76, 91->exit, 149->154, 185->190, 208->210
* ``bot/dialogs/main_menu.py``    — 77->exit
* ``bot/dialogs/schedule_view.py``— 97->exit, 176->182, 188->197, 224->231,
  409->416, 411->416, 467->472, 475->481, 477->481, 500->505, 507->513,
  552->555, 555->567, 593->588, 595->588, 658->exit, 670->exit, 693->687

Все внешние зависимости (Telegram Bot, Redis, БД, файловая система, dramatiq)
замоканы — сеть, Docker и реальный Telegram не используются.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from bot.dialogs import admin_menu, feedback_menu, find_menu, main_menu, schedule_view
from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.states import FindMenu, Schedule
from tests.coverage.helpers import make_callback, make_dm, make_message, make_timetable_manager


# ---------------------------------------------------------------------------
# локальные хелперы
# ---------------------------------------------------------------------------
def _admin_dm(middleware=None, dialog_data=None):
    return make_dm(middleware=middleware if middleware is not None else {}, dialog_data=dialog_data)


def _find_dm(tm=None, udm=None, dialog_data=None):
    if udm is None:
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_history.return_value = []
    return make_dm(
        middleware={"manager": tm, "user_data_manager": udm, "_": lambda k, **kw: k},
        dialog_data=dialog_data if dialog_data is not None else {},
    )


def _sched_dm(tm=None, udm=None, dialog_data=None, start_data=None, bot=None):
    if udm is None:
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_type.return_value = "student"
        udm.get_user_theme.return_value = "standard"
        udm.get_user_group.return_value = None
    tm = tm if tm is not None else make_timetable_manager()
    return make_dm(
        middleware={
            "manager": tm,
            "user_data_manager": udm,
            "bot": bot if bot is not None else AsyncMock(),
            "session_factory": MagicMock(),
        },
        dialog_data=dialog_data if dialog_data is not None else {},
        start_data=start_data if start_data is not None else {},
    )


# ===========================================================================
# bot/dialogs/admin_menu.py
# ===========================================================================
class TestAdminMenuBranches:
    async def test_admin_menu_check_graduated_groups_without_redis_client(self):
        """212->exit: Redis-клиент не создан, ``aclose()`` не вызывается."""
        bot = AsyncMock()
        dm = _admin_dm({"bot": bot, "user_data_manager": AsyncMock(), "manager": AsyncMock()})
        callback = make_callback()

        with patch("redis.asyncio.Redis") as mock_redis:
            mock_redis.from_url.side_effect = RuntimeError("redis unavailable")
            await admin_menu.on_check_graduated_groups(callback, MagicMock(), dm)

        # redis_client остался None -> finally не вызывает aclose
        assert bot.send_message.await_count == 2
        assert "Ошибка при проверке" in bot.send_message.await_args_list[0].args[1]

    async def test_admin_menu_add_channel_chat_without_username_and_invite_link(self):
        """400->409: у чата нет ни username, ни invite_link."""
        msg = make_message(text="@my_channel")
        bot = AsyncMock()
        chat = MagicMock()
        chat.id = -1001234567890
        chat.title = "My Channel"
        chat.username = None
        chat.invite_link = None
        bot.get_chat = AsyncMock(return_value=chat)
        dm = _admin_dm({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": bot})

        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as mock_mgr:
            instance = mock_mgr.return_value
            instance.add_required_channel = AsyncMock()
            await admin_menu.on_add_channel_input(msg, MagicMock(), dm, data="@my_channel")

        kwargs = instance.add_required_channel.await_args.kwargs
        assert kwargs["channel_id"] == "-1001234567890"
        assert kwargs["title"] == "My Channel"
        # ссылка собрана из числового id чата, т.к. Telegram не вернул username
        assert kwargs["invite_link"] == "https://t.me/-1001234567890"

    async def test_admin_menu_clear_cache_with_few_files(self):
        """1015->1018: файлов в кэше не больше пяти — блок «... и еще N» не добавляется."""
        bot = AsyncMock()
        dm = _admin_dm({"bot": bot})
        callback = make_callback()
        before = {
            "total_files": 3,
            "total_size_mb": 1.5,
            "files": ["a.png", "b.png", "c.png"],
            "redis_keys": 2,
        }
        after = {
            "total_files": 0,
            "total_size_mb": 0.0,
            "files": [],
            "redis_keys": 0,
        }

        with (
            patch("bot.dialogs.admin_menu.get_cache_info", new=AsyncMock(side_effect=[before, after])),
            patch("bot.dialogs.admin_menu.cleanup_old_cache", new=AsyncMock()),
        ):
            await admin_menu.on_clear_cache(callback, MagicMock(), dm)

        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.args[1]
        assert "и еще" not in text
        assert "a.png" in text


# ===========================================================================
# bot/dialogs/feedback_menu.py
# ===========================================================================
class TestFeedbackMenuBranches:
    async def test_feedback_menu_message_without_known_content_type(self):
        """76->82: сообщение не является текстом/фото/видео/документом/аудио/голосом/стикером."""
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        bot = AsyncMock()
        dm = make_dm(
            middleware={
                "bot": bot,
                "session_factory": MagicMock(),
                "user_data_manager": udm,
            }
        )
        message = make_message(text=None)

        with (
            patch("bot.dialogs.feedback_menu.FeedbackManager") as mock_fm,
            patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100123"),
        ):
            instance = mock_fm.return_value
            instance.create_feedback = AsyncMock()
            await feedback_menu.on_feedback_received(message, MagicMock(), dm)

        kwargs = instance.create_feedback.await_args.kwargs
        assert kwargs["message_text"] is None
        assert kwargs["message_type"] == "text"
        assert kwargs["file_id"] is None
        bot.forward_message.assert_awaited_once()
        dm.done.assert_awaited_once()


# ===========================================================================
# bot/dialogs/find_menu.py
# ===========================================================================
class TestFindMenuBranches:
    async def test_find_menu_history_data_without_user_data_manager(self):
        """72->76: менеджера пользователей нет — список истории пуст."""
        dm = make_dm(middleware={})
        data = await find_menu.get_history_data(dm)
        assert data["history_items"] == []

    async def test_find_menu_history_selected_unknown_item_type(self):
        """91->exit: тип элемента истории неизвестен — ни одна ветка не срабатывает."""
        dm = _find_dm()
        await find_menu.on_history_selected(make_callback(), MagicMock(), dm, "unknown:value")
        dm.switch_to.assert_not_awaited()
        dm.start.assert_not_awaited()

    async def test_find_menu_teacher_input_manager_without_redis(self):
        """149->154: у менеджера расписания нет атрибута ``redis``."""
        tm = make_timetable_manager()
        tm.find_teachers.return_value = ["Иванов И.И."]
        del tm.redis
        udm = AsyncMock()
        dm = _find_dm(tm=tm, udm=udm)
        message = make_message(text="Иванов")

        await find_menu.on_teacher_input(message, MagicMock(), dm)

        dm.switch_to.assert_awaited_with(FindMenu.view_result)
        assert dm.dialog_data[DialogDataKeys.TEACHER_NAME] == "Иванов И.И."

    async def test_find_menu_classroom_input_manager_without_redis(self):
        """185->190: у менеджера расписания нет атрибута ``redis``."""
        tm = make_timetable_manager()
        tm.find_classrooms.return_value = ["521"]
        del tm.redis
        udm = AsyncMock()
        dm = _find_dm(tm=tm, udm=udm)
        message = make_message(text="521")

        await find_menu.on_classroom_input(message, MagicMock(), dm)

        dm.switch_to.assert_awaited_with(FindMenu.view_result)
        assert dm.dialog_data[DialogDataKeys.CLASSROOM_NUMBER] == "521"

    async def test_find_menu_item_selected_group_without_user_data_manager(self):
        """208->210: нет менеджера пользователей — группа не пишется в историю."""
        dm = make_dm(middleware={}, dialog_data={DialogDataKeys.SEARCH_TYPE: "group"})

        await find_menu.on_item_selected(make_callback(), MagicMock(), dm, "О735Б")

        dm.start.assert_awaited_once()


# ===========================================================================
# bot/dialogs/main_menu.py
# ===========================================================================
class TestMainMenuBranches:
    async def test_main_menu_unknown_user_type(self):
        """77->exit: тип пользователя не ``student`` и не ``teacher``."""
        dm = make_dm(middleware={})
        callback = make_callback(data="user_type_alien")

        await main_menu.on_user_type_selected(callback, MagicMock(), dm)

        assert dm.dialog_data["user_type"] == "alien"
        dm.switch_to.assert_not_awaited()


# ===========================================================================
# bot/dialogs/schedule_view.py
# ===========================================================================
class TestScheduleViewBranches:
    async def test_schedule_view_cleanup_cache_without_redis_client(self):
        """97->exit: Redis-клиент не создан, ``aclose()`` не вызывается."""
        with patch("core.config.get_redis_client", side_effect=RuntimeError("redis unavailable")):
            await schedule_view.cleanup_old_cache()

    async def test_schedule_view_get_schedule_data_without_event_user(self):
        """176->182: у события нет пользователя — тип пользователя остаётся ``student``."""
        tm = make_timetable_manager()
        dm = _sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        dm.event.from_user = None

        data = await schedule_view.get_schedule_data(dm)

        assert data["user_type"] == "student"
        tm.get_schedule_for_day.assert_awaited_once()

    async def test_schedule_view_get_schedule_data_teacher_already_in_index(self):
        """188->197: группа преподавателя уже есть в индексе — канонизация не нужна."""
        tm = make_timetable_manager()
        tm._teachers_index = {"Иванов И.И.": [{"day": "Понедельник", "week_code": "0"}]}
        tm.get_teacher_schedule.return_value = {"lessons": [], "date": date(2025, 9, 1)}
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_type.return_value = "teacher"
        dm = _sched_dm(
            tm=tm,
            udm=udm,
            dialog_data={DialogDataKeys.GROUP: "Иванов И.И.", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )

        data = await schedule_view.get_schedule_data(dm)

        assert data["user_type"] == "teacher"
        udm.set_user_group.assert_not_awaited()
        tm.get_teacher_schedule.assert_awaited_once()

    async def test_schedule_view_get_schedule_data_without_redis_update(self):
        """224->231: в Redis нет метки последнего обновления расписания."""
        tm = make_timetable_manager()
        tm.redis.get.return_value = None
        dm = _sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )

        data = await schedule_view.get_schedule_data(dm)

        assert data["schedule_freshness"] == ""

    async def test_schedule_view_get_week_image_data_without_event_user(self):
        """409->416: нет user_id — тема пользователя не запрашивается."""
        tm = make_timetable_manager()
        dm = _sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        dm.event.from_user = None
        service = AsyncMock()
        service.get_or_generate_week_image.return_value = (True, "/tmp/week.png")

        with (
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=service),
        ):
            data = await schedule_view.get_week_image_data(dm)

        assert data["group"] == "О735Б"
        service.get_or_generate_week_image.assert_awaited_once()

    async def test_schedule_view_get_week_image_data_without_user_data_manager(self):
        """411->416: user_id есть, но менеджера пользователей нет."""
        tm = make_timetable_manager()
        dm = make_dm(
            middleware={"manager": tm, "bot": AsyncMock()},
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        service = AsyncMock()
        service.get_or_generate_week_image.return_value = (True, "/tmp/week.png")

        with (
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=service),
        ):
            data = await schedule_view.get_week_image_data(dm)

        assert data["group"] == "О735Б"
        service.get_or_generate_week_image.assert_awaited_once()

    async def test_schedule_view_send_original_file_without_from_user(self):
        """467->472: у колбэка нет пользователя — user_id не сохраняется."""
        bot = AsyncMock()
        dm = _sched_dm(
            bot=bot,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        callback = make_callback()
        callback.from_user = None

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch.object(schedule_view, "send_week_original_if_subscribed_task"),
        ):
            await schedule_view.on_send_original_file(callback, None, dm)

        assert "user_id" not in dm.current_context().dialog_data

    async def test_schedule_view_send_original_file_without_user_data_manager(self):
        """475->481, 500->505, 507->513: менеджер пользователей недоступен."""
        bot = AsyncMock()
        dm = make_dm(
            middleware={"manager": make_timetable_manager(), "bot": bot, "session_factory": MagicMock()},
            dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        callback = make_callback()

        with (
            patch("core.subscription_filter.is_user_subscribed", new=AsyncMock(return_value=True)),
            patch("pathlib.Path.exists", return_value=True),
        ):
            await schedule_view.on_send_original_file(callback, None, dm)

        bot.send_document.assert_awaited_once()

    async def test_schedule_view_send_original_file_without_saved_group(self):
        """477->481: у пользователя нет сохранённой группы."""
        bot = AsyncMock()
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_theme.return_value = "standard"
        udm.get_user_group.return_value = None
        dm = _sched_dm(
            bot=bot,
            udm=udm,
            dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        callback = make_callback()

        with (
            patch("core.subscription_filter.is_user_subscribed", new=AsyncMock(return_value=True)),
            patch("pathlib.Path.exists", return_value=True),
        ):
            await schedule_view.on_send_original_file(callback, None, dm)

        udm.get_user_group.assert_awaited_once()
        assert DialogDataKeys.GROUP not in dm.current_context().dialog_data

    async def test_schedule_view_send_original_file_without_subscription_channel(self):
        """552->555, 555->567: канал подписки не настроен — ссылка пустая."""
        bot = AsyncMock()
        dm = _sched_dm(
            bot=bot,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        callback = make_callback()

        with (
            patch.object(schedule_view, "SUBSCRIPTION_CHANNEL", None),
            patch("core.subscription_filter.is_user_subscribed", new=AsyncMock(return_value=False)),
        ):
            await schedule_view.on_send_original_file(callback, None, dm)

        dm.switch_to.assert_awaited_with(Schedule.full_quality_gate)
        callback.message.answer.assert_not_awaited()
        callback.answer.assert_awaited()

    async def test_schedule_view_send_original_file_teacher_lesson_week_filtering(self):
        """593->588, 595->588: фильтрация уроков преподавателя по неделе и дню."""
        bot = AsyncMock()
        tm = make_timetable_manager()
        tm._teachers_index = {
            "Иванов И.И.": [
                {"day": "Понедельник", "week_code": "0"},
                {"day": "Выходной", "week_code": "0"},
                {"day": "Вторник", "week_code": "2"},
                {"day": "Среда", "week_code": "0"},
            ]
        }
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_theme.return_value = "standard"
        dm = _sched_dm(
            tm=tm,
            udm=udm,
            bot=bot,
            dialog_data={
                DialogDataKeys.GROUP: "Иванов И.И.",
                DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                "user_type": "teacher",
            },
        )
        callback = make_callback()
        service = AsyncMock()
        service.get_or_generate_week_image.return_value = (True, "/tmp/week.png")

        with (
            patch("core.subscription_filter.is_user_subscribed", new=AsyncMock(return_value=True)),
            patch("pathlib.Path.exists", side_effect=[False, False, False]),
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=service),
            patch.object(schedule_view, "send_week_original_if_subscribed_task"),
        ):
            await schedule_view.on_send_original_file(callback, None, dm)

        service.get_or_generate_week_image.assert_awaited_once()

    async def test_schedule_view_date_shift_without_manager(self):
        """658->exit: менеджер расписания недоступен — аналитика не вызывается."""
        dm = make_dm(middleware={}, dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"})

        await schedule_view.on_date_shift(make_callback(), MagicMock(), dm, -1)

        assert dm.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == "2025-08-31"

    async def test_schedule_view_today_click_without_manager(self):
        """670->exit: менеджер расписания недоступен — аналитика не вызывается."""
        dm = make_dm(middleware={}, dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2000-01-01"})

        await schedule_view.on_today_click(make_callback(), MagicMock(), dm)

        assert dm.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] != "2000-01-01"

    async def test_schedule_view_text_week_click_skips_days_without_info(self):
        """693->687: день без расписания пропускается."""
        tm = make_timetable_manager()
        tm.get_schedule_for_day.return_value = None
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        dm = _sched_dm(
            tm=tm,
            udm=udm,
            dialog_data={
                DialogDataKeys.GROUP: "О735Б",
                DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                "user_type": "student",
            },
        )
        callback = make_callback()

        await schedule_view.on_text_week_click(callback, MagicMock(), dm)

        callback.message.answer.assert_awaited_once()
        assert tm.get_schedule_for_day.await_count == 6
