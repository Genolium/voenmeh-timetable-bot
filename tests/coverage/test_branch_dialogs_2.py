"""Дополнительные (сверх ``test_branch_dialogs_1.py``) тесты ветвей для
``bot/dialogs/*``.

Здесь собраны дополнительные сценарии, которые не дублируют первый файл, но
закрепляют смежные ветки ``coverage.py`` (arc'и вида ``line->line``) и
проверяют, что обработчики корректно ведут себя на «неудобных» входах.

Покрываемые модули: ``admin_menu``, ``feedback_menu``, ``find_menu``,
``main_menu``, ``schedule_view``.

Сеть, Docker, реальный Telegram, Redis и БД не используются.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from bot.dialogs import admin_menu, feedback_menu, find_menu, main_menu, schedule_view
from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.states import FindMenu, MainMenu, Schedule
from tests.coverage.helpers import make_callback, make_dm, make_message, make_timetable_manager


# ---------------------------------------------------------------------------
# локальные хелперы
# ---------------------------------------------------------------------------
def _sched_dm(tm=None, udm=None, dialog_data=None, bot=None):
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
    )


# ===========================================================================
# bot/dialogs/admin_menu.py
# ===========================================================================
class TestAdminMenuExtraBranches:
    async def test_branch_dialogs_2_admin_menu_add_channel_chat_with_invite_link_only(self):
        """У чата есть invite_link, но нет username — используется invite-ссылка."""
        msg = make_message(text="@my_channel")
        bot = AsyncMock()
        chat = MagicMock()
        chat.id = -100999
        chat.title = "Channel"
        chat.username = None
        chat.invite_link = "https://t.me/+joinlink"
        bot.get_chat = AsyncMock(return_value=chat)
        dm = make_dm(middleware={"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": bot})

        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as mock_mgr:
            instance = mock_mgr.return_value
            instance.add_required_channel = AsyncMock()
            await admin_menu.on_add_channel_input(msg, MagicMock(), dm, data="@my_channel")

        kwargs = instance.add_required_channel.await_args.kwargs
        assert kwargs["invite_link"] == "https://t.me/+joinlink"
        assert kwargs["title"] == "Channel"
        assert kwargs["channel_id"] == "-100999"

    async def test_branch_dialogs_2_admin_menu_check_graduated_groups_handler_failure(self):
        """Ошибка в обработчике выпустившихся групп не должна ронять хендлер."""
        bot = AsyncMock()
        dm = make_dm(middleware={"bot": bot, "user_data_manager": AsyncMock(), "manager": AsyncMock()})
        callback = make_callback()
        client = MagicMock()
        client.aclose = AsyncMock()

        with (
            patch("redis.asyncio.Redis") as mock_redis,
            patch("bot.scheduler.handle_graduated_groups", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            mock_redis.from_url.return_value = client
            await admin_menu.on_check_graduated_groups(callback, MagicMock(), dm)

        client.aclose.assert_awaited_once()
        assert bot.send_message.await_count == 3

    async def test_branch_dialogs_2_admin_menu_clear_cache_error_after_cleanup(self):
        """Ошибка в кэше, обнаруженная уже после очистки."""
        bot = AsyncMock()
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        before = {"total_files": 1, "total_size_mb": 1.0, "files": ["a.png"], "redis_keys": 1}
        after = {"error": "cache broken"}

        with (
            patch("bot.dialogs.admin_menu.get_cache_info", new=AsyncMock(side_effect=[before, after])),
            patch("bot.dialogs.admin_menu.cleanup_old_cache", new=AsyncMock()),
        ):
            await admin_menu.on_clear_cache(callback, MagicMock(), dm)

        assert "Ошибка при работе с кэшем" in bot.send_message.await_args.args[1]


# ===========================================================================
# bot/dialogs/feedback_menu.py
# ===========================================================================
class TestFeedbackMenuExtraBranches:
    async def test_branch_dialogs_2_feedback_menu_document_without_caption(self):
        """Документ без подписи сохраняется с именем файла."""
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
        message.document = MagicMock(file_id="doc-id", file_name="report.pdf")

        with (
            patch("bot.dialogs.feedback_menu.FeedbackManager") as mock_fm,
            patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100123"),
        ):
            instance = mock_fm.return_value
            instance.create_feedback = AsyncMock()
            await feedback_menu.on_feedback_received(message, MagicMock(), dm)

        kwargs = instance.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "document"
        assert kwargs["message_text"] == "Документ: report.pdf"
        assert kwargs["file_id"] == "doc-id"

    async def test_branch_dialogs_2_feedback_menu_with_context(self):
        """Контекст жалобы добавляется к служебному сообщению."""
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        bot = AsyncMock()
        dm = make_dm(
            middleware={
                "bot": bot,
                "session_factory": MagicMock(),
                "user_data_manager": udm,
            },
            start_data={"feedback_context": "Жалоба на расписание: О735Б, 01.09.2025"},
        )
        message = make_message(text="что-то не так")

        with (
            patch("bot.dialogs.feedback_menu.FeedbackManager") as mock_fm,
            patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100123"),
        ):
            instance = mock_fm.return_value
            instance.create_feedback = AsyncMock()
            await feedback_menu.on_feedback_received(message, MagicMock(), dm)

        assert "Жалоба на расписание" in bot.send_message.await_args.args[1]


# ===========================================================================
# bot/dialogs/find_menu.py
# ===========================================================================
class TestFindMenuExtraBranches:
    async def test_branch_dialogs_2_find_menu_group_input_with_multiple_matches(self):
        """Несколько совпадений — переход к окну выбора найденного."""
        tm = make_timetable_manager()
        tm.find_groups.return_value = ["О735Б", "О735А"]
        dm = make_dm(middleware={"manager": tm, "user_data_manager": AsyncMock(), "_": lambda k, **kw: k})
        message = make_message(text="О735")

        await find_menu.on_group_input(message, MagicMock(), dm)

        assert dm.dialog_data[DialogDataKeys.FOUND_ITEMS] == ["О735Б", "О735А"]
        dm.switch_to.assert_awaited_with(FindMenu.select_item)

    async def test_branch_dialogs_2_find_menu_group_input_with_empty_text(self):
        """Пустой запрос — сообщение о ненайденной группе."""
        tm = make_timetable_manager()
        dm = make_dm(middleware={"manager": tm, "user_data_manager": AsyncMock(), "_": lambda k, **kw: k})
        message = make_message(text="   ")

        await find_menu.on_group_input(message, MagicMock(), dm)

        message.answer.assert_awaited_once()
        dm.switch_to.assert_not_awaited()

    async def test_branch_dialogs_2_find_menu_history_selected_group(self):
        """Выбор группы из истории открывает расписание."""
        dm = make_dm(middleware={})

        await find_menu.on_history_selected(make_callback(), MagicMock(), dm, "group:О735Б")

        dm.start.assert_awaited_once()


# ===========================================================================
# bot/dialogs/main_menu.py
# ===========================================================================
class TestMainMenuExtraBranches:
    async def test_branch_dialogs_2_main_menu_user_type_teacher(self):
        """Выбор типа «преподаватель» ведёт в окно ввода ФИО."""
        dm = make_dm(middleware={})
        callback = make_callback(data="user_type_teacher")

        await main_menu.on_user_type_selected(callback, MagicMock(), dm)

        assert dm.dialog_data["user_type"] == "teacher"
        dm.switch_to.assert_awaited_with(MainMenu.enter_teacher)

    async def test_branch_dialogs_2_main_menu_group_entered_with_suggestion(self):
        """Похожие группы предлагаются пользователю."""
        tm = make_timetable_manager()
        tm._schedules = {"О735Б": {}}
        dm = make_dm(middleware={"manager": tm, "user_data_manager": AsyncMock(), "_": lambda k, **kw: k})
        message = make_message(text="О735Х")

        await main_menu.on_group_entered(message, MagicMock(), dm)

        assert dm.dialog_data["group_suggestions"]
        dm.switch_to.assert_awaited_with(MainMenu.enter_group)
        message.answer.assert_not_awaited()

    async def test_branch_dialogs_2_main_menu_group_entered_without_suggestion(self):
        """Совпадений нет — сообщение о ненайденной группе."""
        tm = make_timetable_manager()
        tm._schedules = {"О735Б": {}}
        dm = make_dm(middleware={"manager": tm, "user_data_manager": AsyncMock(), "_": lambda k, **kw: k})
        message = make_message(text="О999А")

        await main_menu.on_group_entered(message, MagicMock(), dm)

        message.answer.assert_awaited_once()
        dm.switch_to.assert_not_awaited()


# ===========================================================================
# bot/dialogs/schedule_view.py
# ===========================================================================
class TestScheduleViewExtraBranches:
    async def test_branch_dialogs_2_schedule_view_send_original_file_with_custom_theme(self):
        """Нестандартная тема попадает в ключ кэша оригинала."""
        bot = AsyncMock()
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_theme.return_value = "dark"
        dm = _sched_dm(
            bot=bot,
            udm=udm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        callback = make_callback()

        with (
            patch("core.subscription_filter.is_user_subscribed", new=AsyncMock(return_value=True)),
            patch("pathlib.Path.exists", return_value=True),
        ):
            await schedule_view.on_send_original_file(callback, None, dm)

        bot.send_document.assert_awaited_once()
        document = bot.send_document.await_args.kwargs["document"]
        assert document.filename == "Schedule_О735Б_odd.png"

    async def test_branch_dialogs_2_schedule_view_send_original_file_numeric_channel(self):
        """Канал-идентификатор превращается в tg:// ссылку."""
        bot = AsyncMock()
        dm = _sched_dm(
            bot=bot,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        callback = make_callback()

        with (
            patch.object(schedule_view, "SUBSCRIPTION_CHANNEL", "-1001234567890"),
            patch("core.subscription_filter.is_user_subscribed", new=AsyncMock(return_value=False)),
        ):
            await schedule_view.on_send_original_file(callback, None, dm)

        dm.switch_to.assert_awaited_with(Schedule.full_quality_gate)
        callback.message.answer.assert_awaited_once()

    async def test_branch_dialogs_2_schedule_view_text_week_click_with_error_day(self):
        """День с ошибкой расписания пропускается при построении недели."""
        tm = make_timetable_manager()
        tm.get_schedule_for_day.return_value = {"error": "no group"}
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

    async def test_branch_dialogs_2_schedule_view_get_week_image_data_teacher_branch(self):
        """Недельное расписание преподавателя собирается из индекса преподавателей."""
        tm = make_timetable_manager()
        tm._teachers_index = {
            "Иванов И.И.": [
                {"day": "Понедельник", "week_code": "0"},
                {"day": "Вторник", "week_code": "1"},
            ]
        }
        dm = _sched_dm(
            tm=tm,
            dialog_data={
                DialogDataKeys.GROUP: "Иванов И.И.",
                DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                "user_type": "teacher",
            },
        )
        service = AsyncMock()
        service.get_or_generate_week_image.return_value = (True, "/tmp/week.png")

        with (
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=service),
        ):
            data = await schedule_view.get_week_image_data(dm)

        assert data["group"] == "Иванов И.И."
        kwargs = service.get_or_generate_week_image.await_args.kwargs
        assert kwargs["group"] == "Иванов И.И."
        assert kwargs["week_key"] == "odd"
