"""Полное покрытие bot/dialogs/admin_menu.py.

Все внешние зависимости (aiogram Bot, dramatiq-задачи, БД, Redis, менеджеры,
файловая система) замоканы. Реальная сеть и RabbitMQ не используются.
"""

import asyncio
import io
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import ContentType

import bot.dialogs.admin_menu as admin_menu
from bot.dialogs.admin_menu import (
    active_generations,
    admin_dialog,
    build_segment_users,
    get_backup_menu_data,
    get_backup_upload_data,
    get_preview_data,
    get_semester_settings_data,
    get_stats_data,
    get_sub_filter_data,
    get_user_manage_data,
    on_add_channel_input,
    on_backup_create,
    on_backup_upload_received,
    on_broadcast_received,
    on_cancel_generation,
    on_check_graduated_groups,
    on_clear_cache,
    on_confirm_segment_send,
    on_delete_sub_filter_channel,
    on_edit_fall_semester,
    on_edit_spring_semester,
    on_fall_semester_input,
    on_force_update_schedule,
    on_generate_full_schedule,
    on_message_to_user_input,
    on_new_group_input,
    on_period_selected,
    on_segment_criteria_input,
    on_semester_settings,
    on_send_message_to_user,
    on_send_stats_chart,
    on_spring_semester_input,
    on_template_input_message,
    on_test_alert,
    on_test_evening,
    on_test_morning,
    on_test_reminders_for_week,
    on_toggle_sub_filter,
    on_user_id_input,
    render_template,
)
from bot.dialogs.states import Admin


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def make_manager(middleware=None, dialog_data=None):
    mgr = MagicMock()
    mgr.middleware_data = middleware if middleware is not None else {}
    mgr.dialog_data = dialog_data if dialog_data is not None else {}
    mgr.switch_to = AsyncMock()
    return mgr


def make_callback(user_id=1):
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer_document = AsyncMock()
    cb.message.answer_photo = AsyncMock()
    return cb


def make_message(text="hello", content_type=ContentType.TEXT, user_id=1):
    m = MagicMock()
    m.text = text
    m.content_type = content_type
    m.answer = AsyncMock()
    m.reply = AsyncMock()
    m.from_user = MagicMock()
    m.from_user.id = user_id
    m.chat = MagicMock()
    m.chat.id = 555
    m.message_id = 777
    m.bot = MagicMock()
    m.bot.download = AsyncMock()
    m.document = None
    return m


def make_user_info(uid=1, username="user", group="О7", last_active=datetime(2024, 1, 2, 10, 0)):
    info = MagicMock()
    info.user_id = uid
    info.username = username
    info.group = group
    info.last_active_date = last_active
    info.registration_date = datetime(2023, 9, 1, 9, 0)
    return info


def make_udm(**overrides):
    udm = MagicMock()
    udm.get_all_user_ids = AsyncMock(return_value=[])
    udm.get_full_user_info = AsyncMock(return_value=None)
    udm.get_users_for_lesson_reminders = AsyncMock(return_value=[])
    udm.set_user_group = AsyncMock()
    udm._get_redis_client = AsyncMock(return_value=MagicMock())
    for key, value in overrides.items():
        setattr(udm, key, value)
    return udm


async def drain_background_tasks(rounds=8):
    """Дожидается выполнения задач, созданных через asyncio.create_task."""
    for _ in range(rounds):
        await asyncio.sleep(0)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# module import / dialog definitions
# ---------------------------------------------------------------------------
class TestDialogDefinition:
    def test_dialog_imported_with_windows(self):
        assert admin_dialog is not None
        assert len(admin_dialog.windows) > 0

    def test_active_generations_is_dict(self):
        assert isinstance(active_generations, dict)


# ---------------------------------------------------------------------------
# diagnostics / broadcasts
# ---------------------------------------------------------------------------
class TestDiagnostics:
    async def test_on_test_morning(self):
        cb = make_callback()
        mgr = make_manager({"user_data_manager": MagicMock(), "manager": MagicMock()})
        with patch("bot.dialogs.admin_menu.morning_summary_broadcast", new=AsyncMock()) as broadcast:
            await on_test_morning(cb, MagicMock(), mgr)
        broadcast.assert_awaited_once()
        cb.answer.assert_awaited_once()
        cb.message.answer.assert_awaited_once()

    async def test_on_test_evening(self):
        cb = make_callback()
        mgr = make_manager({"user_data_manager": MagicMock(), "manager": MagicMock()})
        with patch("bot.dialogs.admin_menu.evening_broadcast", new=AsyncMock()) as broadcast:
            await on_test_evening(cb, MagicMock(), mgr)
        broadcast.assert_awaited_once()
        cb.answer.assert_awaited_once()

    async def test_on_test_alert(self):
        cb = make_callback(user_id=42)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot})
        await on_test_alert(cb, MagicMock(), mgr)
        bot.send_message.assert_awaited_once()
        assert "ALERTMANAGER" in bot.send_message.await_args[0][1]

    async def test_on_generate_full_schedule(self):
        cb = make_callback()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot})
        await on_generate_full_schedule(cb, MagicMock(), mgr)
        cb.answer.assert_awaited_once()
        bot.send_message.assert_awaited_once()

    async def test_on_generate_full_schedule_send_error(self):
        cb = make_callback()
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("boom"))
        mgr = make_manager({"bot": bot})
        # исключение внутри проглатывается
        await on_generate_full_schedule(cb, MagicMock(), mgr)
        cb.answer.assert_awaited_once()

    async def test_on_test_reminders_no_users(self):
        cb = make_callback(user_id=7)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_users_for_lesson_reminders=AsyncMock(return_value=[]))
        mgr = make_manager({"bot": bot, "user_data_manager": udm, "manager": MagicMock()})
        await on_test_reminders_for_week(cb, MagicMock(), mgr)
        bot.send_message.assert_awaited_once()
        assert "Не найдено" in bot.send_message.await_args[0][1]

    async def test_on_test_reminders_full_flow(self):
        cb = make_callback(user_id=7)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_users_for_lesson_reminders=AsyncMock(return_value=[(123, "О7", 20, "ru")]))

        calls = {"n": 0}

        async def sched(group, target_date=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"lessons": [{"start_time_raw": "08:00"}]}
            if calls["n"] == 2:
                return {"error": "bad"}
            if calls["n"] == 3:
                return None
            if calls["n"] == 4:
                return {"lessons": []}
            return {"lessons": [{"start_time_raw": "09:00"}]}

        timetable = MagicMock()
        timetable.get_schedule_for_day = AsyncMock(side_effect=sched)

        reminder_calls = {"n": 0}

        def fake_reminder(*args, **kwargs):
            reminder_calls["n"] += 1
            if reminder_calls["n"] == 2:
                raise RuntimeError("reminder error")
            return "напоминание"

        mgr = make_manager({"bot": bot, "user_data_manager": udm, "manager": timetable})
        with patch("bot.dialogs.admin_menu.generate_reminder_text", side_effect=fake_reminder):
            await on_test_reminders_for_week(cb, MagicMock(), mgr)

        # 7 итераций + финальное сообщение
        assert bot.send_message.await_count >= 8
        assert reminder_calls["n"] >= 1

    async def test_on_check_graduated_groups_success(self):
        cb = make_callback()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot, "user_data_manager": MagicMock(), "manager": MagicMock()})
        client = MagicMock()
        client.aclose = AsyncMock()
        with (
            patch("redis.asyncio.Redis") as MockRedis,
            patch("bot.scheduler.handle_graduated_groups", new=AsyncMock()) as handler,
        ):
            MockRedis.from_url.return_value = client
            await on_check_graduated_groups(cb, MagicMock(), mgr)
        handler.assert_awaited_once()
        client.aclose.assert_awaited_once()
        assert "завершена" in bot.send_message.await_args[0][1]

    async def test_on_check_graduated_groups_error(self):
        cb = make_callback()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot, "user_data_manager": MagicMock(), "manager": MagicMock()})
        client = MagicMock()
        client.aclose = AsyncMock()
        with (
            patch("redis.asyncio.Redis") as MockRedis,
            patch(
                "bot.scheduler.handle_graduated_groups",
                new=AsyncMock(side_effect=RuntimeError("fail")),
            ),
        ):
            MockRedis.from_url.return_value = client
            await on_check_graduated_groups(cb, MagicMock(), mgr)
        # три сообщения: старт, ошибка, детали
        assert bot.send_message.await_count == 3

    async def test_on_check_graduated_groups_aclose_error(self):
        cb = make_callback()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot, "user_data_manager": MagicMock(), "manager": MagicMock()})
        client = MagicMock()
        client.aclose = AsyncMock(side_effect=Exception("close fail"))
        with patch("redis.asyncio.Redis") as MockRedis, patch("bot.scheduler.handle_graduated_groups", new=AsyncMock()):
            MockRedis.from_url.return_value = client
            await on_check_graduated_groups(cb, MagicMock(), mgr)
        client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# semester settings navigation / inputs
# ---------------------------------------------------------------------------
class TestSemesterSettings:
    async def test_on_semester_settings(self):
        mgr = make_manager()
        await on_semester_settings(make_callback(), MagicMock(), mgr)
        mgr.switch_to.assert_awaited_once_with(Admin.semester_settings)

    async def test_on_edit_fall_semester(self):
        mgr = make_manager()
        await on_edit_fall_semester(make_callback(), MagicMock(), mgr)
        mgr.switch_to.assert_awaited_once_with(Admin.edit_fall_semester)

    async def test_on_edit_spring_semester(self):
        mgr = make_manager()
        await on_edit_spring_semester(make_callback(), MagicMock(), mgr)
        mgr.switch_to.assert_awaited_once_with(Admin.edit_spring_semester)

    async def test_on_fall_semester_input_success(self):
        msg = make_message(text="01.09.2025")
        mgr = make_manager({"session_factory": MagicMock()})
        with patch("bot.dialogs.admin_menu.SemesterSettingsManager") as MockMgr:
            inst = MockMgr.return_value
            inst.get_semester_settings = AsyncMock(return_value=(date(2024, 9, 1), date(2025, 2, 9)))
            inst.update_semester_settings = AsyncMock(return_value=True)
            await on_fall_semester_input(msg, MagicMock(), mgr, data="01.09.2025")
        msg.answer.assert_awaited_once()
        assert "успешно обновлена" in msg.answer.await_args[0][0]
        mgr.switch_to.assert_awaited_once_with(Admin.semester_settings)

    async def test_on_fall_semester_input_update_false(self):
        msg = make_message(text="01.09.2025")
        mgr = make_manager({"session_factory": MagicMock()})
        with patch("bot.dialogs.admin_menu.SemesterSettingsManager") as MockMgr:
            inst = MockMgr.return_value
            inst.get_semester_settings = AsyncMock(return_value=None)
            inst.update_semester_settings = AsyncMock(return_value=False)
            await on_fall_semester_input(msg, MagicMock(), mgr, data="01.09.2025")
        assert "Ошибка при обновлении" in msg.answer.await_args[0][0]

    async def test_on_fall_semester_input_invalid_format(self):
        msg = make_message(text="not-a-date")
        mgr = make_manager({"session_factory": MagicMock()})
        await on_fall_semester_input(msg, MagicMock(), mgr, data="not-a-date")
        assert "Неверный формат" in msg.answer.await_args[0][0]
        mgr.switch_to.assert_awaited_once_with(Admin.semester_settings)

    async def test_on_fall_semester_input_generic_error(self):
        msg = make_message(text="01.09.2025")
        mgr = make_manager({"session_factory": MagicMock()})
        with patch("bot.dialogs.admin_menu.SemesterSettingsManager") as MockMgr:
            MockMgr.side_effect = RuntimeError("db down")
            await on_fall_semester_input(msg, MagicMock(), mgr, data="01.09.2025")
        assert "Ошибка" in msg.answer.await_args[0][0]

    async def test_on_spring_semester_input_success(self):
        msg = make_message(text="09.02.2025")
        mgr = make_manager({"session_factory": MagicMock()})
        with patch("bot.dialogs.admin_menu.SemesterSettingsManager") as MockMgr:
            inst = MockMgr.return_value
            inst.get_semester_settings = AsyncMock(return_value=(date(2024, 9, 1), date(2025, 2, 9)))
            inst.update_semester_settings = AsyncMock(return_value=True)
            await on_spring_semester_input(msg, MagicMock(), mgr, data="09.02.2025")
        assert "успешно обновлена" in msg.answer.await_args[0][0]

    async def test_on_spring_semester_input_update_false(self):
        msg = make_message(text="09.02.2025")
        mgr = make_manager({"session_factory": MagicMock()})
        with patch("bot.dialogs.admin_menu.SemesterSettingsManager") as MockMgr:
            inst = MockMgr.return_value
            inst.get_semester_settings = AsyncMock(return_value=None)
            inst.update_semester_settings = AsyncMock(return_value=False)
            await on_spring_semester_input(msg, MagicMock(), mgr, data="09.02.2025")
        assert "Ошибка при обновлении" in msg.answer.await_args[0][0]

    async def test_on_spring_semester_input_invalid_format(self):
        msg = make_message(text="bad")
        mgr = make_manager({"session_factory": MagicMock()})
        await on_spring_semester_input(msg, MagicMock(), mgr, data="bad")
        assert "Неверный формат" in msg.answer.await_args[0][0]

    async def test_on_spring_semester_input_generic_error(self):
        msg = make_message(text="09.02.2025")
        mgr = make_manager({"session_factory": MagicMock()})
        with patch("bot.dialogs.admin_menu.SemesterSettingsManager") as MockMgr:
            MockMgr.side_effect = RuntimeError("db down")
            await on_spring_semester_input(msg, MagicMock(), mgr, data="09.02.2025")
        assert "Ошибка" in msg.answer.await_args[0][0]

    async def test_get_semester_settings_data_no_session(self):
        mgr = make_manager({})
        data = await get_semester_settings_data(mgr)
        assert "Ошибка" in data["semester_settings_text"]

    async def test_get_semester_settings_data_ok(self):
        mgr = make_manager({"session_factory": MagicMock()})
        with patch("bot.dialogs.admin_menu.SemesterSettingsManager") as MockMgr:
            MockMgr.return_value.get_formatted_settings = AsyncMock(return_value="TEXT")
            data = await get_semester_settings_data(mgr)
        assert data["semester_settings_text"] == "TEXT"


# ---------------------------------------------------------------------------
# backups
# ---------------------------------------------------------------------------
class TestBackups:
    async def test_get_backup_menu_data_no_status(self):
        mgr = make_manager(dialog_data={})
        data = await get_backup_menu_data(mgr)
        assert "Резервные копии" in data["backup_status"]

    async def test_get_backup_menu_data_with_status(self):
        mgr = make_manager(dialog_data={"backup_status": "STATUS"})
        data = await get_backup_menu_data(mgr)
        assert data["backup_status"].endswith("STATUS")

    async def test_get_backup_upload_data_default(self):
        mgr = make_manager(dialog_data={})
        data = await get_backup_upload_data(mgr)
        assert "Ожидаем" in data["backup_upload_status"]

    async def test_get_backup_upload_data_value(self):
        mgr = make_manager(dialog_data={"backup_upload_status": "DONE"})
        data = await get_backup_upload_data(mgr)
        assert data["backup_upload_status"] == "DONE"

    async def test_on_backup_create_success(self, tmp_path):
        cb = make_callback()
        mgr = make_manager()
        backup_file = tmp_path / "backup.sql"
        backup_file.write_text("sql")
        with patch(
            "bot.dialogs.admin_menu.create_db_backup",
            new=AsyncMock(return_value=backup_file),
        ):
            await on_backup_create(cb, MagicMock(), mgr)
        cb.message.answer_document.assert_awaited_once()
        assert "успешно создан" in mgr.dialog_data["backup_status"]
        assert not backup_file.exists()
        # switch_to: сначала confirm, затем menu
        assert mgr.switch_to.await_count == 2

    async def test_on_backup_create_unlink_oserror(self, tmp_path):
        cb = make_callback()
        mgr = make_manager()
        backup_file = tmp_path / "backup.sql"
        backup_file.write_text("sql")
        from pathlib import Path

        with (
            patch(
                "bot.dialogs.admin_menu.create_db_backup",
                new=AsyncMock(return_value=backup_file),
            ),
            patch.object(Path, "unlink", side_effect=OSError("cannot unlink")),
        ):
            await on_backup_create(cb, MagicMock(), mgr)
        cb.message.answer_document.assert_awaited_once()
        assert "успешно создан" in mgr.dialog_data["backup_status"]

    async def test_on_backup_create_backup_error(self):
        cb = make_callback()
        mgr = make_manager()
        from core.db.backups import BackupError

        with patch(
            "bot.dialogs.admin_menu.create_db_backup",
            new=AsyncMock(side_effect=BackupError("no db")),
        ):
            await on_backup_create(cb, MagicMock(), mgr)
        cb.message.answer.assert_awaited_once()
        assert "Не удалось создать" in mgr.dialog_data["backup_status"]

    async def test_on_backup_create_unexpected_error(self):
        cb = make_callback()
        mgr = make_manager()
        with patch(
            "bot.dialogs.admin_menu.create_db_backup",
            new=AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            await on_backup_create(cb, MagicMock(), mgr)
        assert "Ошибка создания бэкапа" in mgr.dialog_data["backup_status"]

    async def test_on_backup_upload_no_document(self):
        msg = make_message()
        msg.document = None
        mgr = make_manager()
        await on_backup_upload_received(msg, MagicMock(), mgr)
        msg.answer.assert_awaited_once()
        assert "Отправьте документ" in msg.answer.await_args[0][0]

    async def test_on_backup_upload_success(self, tmp_path):
        msg = make_message()
        msg.document = MagicMock()
        msg.document.file_name = "backup.sql"
        msg.document.file_size = 100
        msg.bot = AsyncMock()
        mgr = make_manager()
        with patch("bot.dialogs.admin_menu.tempfile.mkdtemp", return_value=str(tmp_path)):
            await on_backup_upload_received(msg, MagicMock(), mgr)
        assert "RESTORE" in mgr.dialog_data["backup_upload_status"]
        assert mgr.switch_to.await_args[0][0] == Admin.backup_upload_confirm
        assert "backup_restore_path" in mgr.dialog_data

    async def test_on_backup_upload_rejects_wrong_type(self):
        msg = make_message()
        msg.document = MagicMock()
        msg.document.file_name = "notes.txt"
        msg.document.file_size = 10
        mgr = make_manager()
        await on_backup_upload_received(msg, MagicMock(), mgr)
        assert "sql" in msg.answer.await_args[0][0].lower()

    async def test_on_backup_restore_typed_confirm(self, tmp_path):
        from bot.dialogs.admin_menu import on_backup_restore_typed

        backup = tmp_path / "backup.sql"
        backup.write_text("-- sql", encoding="utf-8")
        msg = make_message()
        msg.text = "RESTORE"
        mgr = make_manager(dialog_data={"backup_restore_path": str(backup)})
        with (
            patch("bot.dialogs.admin_menu.maybe_decompress_gzip", return_value=backup),
            patch("bot.dialogs.admin_menu.restore_db_backup", new=AsyncMock()) as restore,
        ):
            await on_backup_restore_typed(msg, MagicMock(), mgr)
        restore.assert_awaited_once()
        assert "успешно восстановлена" in mgr.dialog_data["backup_upload_status"]

    async def test_on_backup_upload_restore_error(self, tmp_path):
        from bot.dialogs.admin_menu import on_backup_restore_typed
        from core.db.backups import BackupError

        backup = tmp_path / "backup.sql"
        backup.write_text("-- sql", encoding="utf-8")
        msg = make_message()
        msg.text = "RESTORE"
        mgr = make_manager(dialog_data={"backup_restore_path": str(backup)})
        with (
            patch("bot.dialogs.admin_menu.maybe_decompress_gzip", return_value=backup),
            patch(
                "bot.dialogs.admin_menu.restore_db_backup",
                new=AsyncMock(side_effect=BackupError("bad dump")),
            ),
        ):
            await on_backup_restore_typed(msg, MagicMock(), mgr)
        assert "Не удалось применить" in mgr.dialog_data["backup_upload_status"]

    async def test_on_backup_upload_rejects_oversized(self):
        from bot.dialogs.admin_menu import MAX_RESTORE_BYTES

        msg = make_message()
        msg.document = MagicMock()
        msg.document.file_name = "backup.sql"
        msg.document.file_size = MAX_RESTORE_BYTES + 1
        mgr = make_manager()
        await on_backup_upload_received(msg, MagicMock(), mgr)
        assert "слишком большой" in msg.answer.await_args[0][0]

    async def test_on_backup_restore_typed_rejects_wrong_word(self):
        from bot.dialogs.admin_menu import on_backup_restore_typed

        msg = make_message()
        msg.text = "yes"
        mgr = make_manager(dialog_data={"backup_restore_path": "/tmp/x.sql"})
        await on_backup_restore_typed(msg, MagicMock(), mgr)
        assert "RESTORE" in msg.answer.await_args[0][0]
        mgr.switch_to.assert_not_awaited()

    async def test_on_backup_restore_typed_missing_file(self):
        from bot.dialogs.admin_menu import on_backup_restore_typed

        msg = make_message()
        msg.text = "RESTORE"
        mgr = make_manager(dialog_data={})
        await on_backup_restore_typed(msg, MagicMock(), mgr)
        assert "не найден" in msg.answer.await_args[0][0]
        assert mgr.switch_to.await_args[0][0] == Admin.backup_menu

    async def test_on_admin_dialog_start_rejects_non_admin(self):
        from bot.dialogs.admin_menu import on_admin_dialog_start

        mgr = make_manager()
        mgr.event = MagicMock()
        mgr.event.from_user = MagicMock(id=999999999)
        mgr.done = AsyncMock()
        await on_admin_dialog_start({}, mgr)
        mgr.done.assert_awaited_once()

    async def test_is_sql_backup_name(self):
        from bot.dialogs.admin_menu import _is_sql_backup_name

        assert _is_sql_backup_name("db.sql")
        assert _is_sql_backup_name("db.sql.gz")
        assert not _is_sql_backup_name("photo.png")


# ---------------------------------------------------------------------------
# subscription filter
# ---------------------------------------------------------------------------
class TestSubFilter:
    async def test_get_sub_filter_data_enabled_with_channels(self):
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock()})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.is_filter_enabled = AsyncMock(return_value=True)
            inst.get_required_channels = AsyncMock(
                return_value=[
                    {"channel_id": "@c1", "title": "Канал 1", "invite_link": "https://t.me/c1"},
                    {"channel_id": "@c2", "title": "Канал 2", "invite_link": None},
                ]
            )
            data = await get_sub_filter_data(mgr)
        assert data["is_enabled"] is True
        assert data["channels_count"] == 2
        assert "Канал 1" in data["channels_text"]

    async def test_get_sub_filter_data_disabled_empty(self):
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock()})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.is_filter_enabled = AsyncMock(return_value=False)
            inst.get_required_channels = AsyncMock(return_value=[])
            data = await get_sub_filter_data(mgr)
        assert data["is_enabled"] is False
        assert "не добавлены" in data["channels_text"]
        assert "Включить фильтр" in data["toggle_btn_text"]

    async def test_on_toggle_sub_filter(self):
        cb = make_callback()
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock()})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.is_filter_enabled = AsyncMock(return_value=False)
            inst.set_filter_enabled = AsyncMock()
            await on_toggle_sub_filter(cb, MagicMock(), mgr)
        inst.set_filter_enabled.assert_awaited_once_with(True)
        assert "включен" in cb.answer.await_args[0][0]

    async def test_on_delete_sub_filter_channel(self):
        cb = make_callback()
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock()})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.remove_required_channel = AsyncMock()
            await on_delete_sub_filter_channel(cb, MagicMock(), mgr, item_id="@old")
        inst.remove_required_channel.assert_awaited_once_with("@old")
        assert "удалён" in cb.answer.await_args[0][0]

    async def test_on_add_channel_input_cancel(self):
        msg = make_message(text="/cancel")
        mgr = make_manager()
        await on_add_channel_input(msg, MagicMock(), mgr, data="/cancel")
        assert "Отменено" in msg.answer.await_args[0][0]
        mgr.switch_to.assert_awaited_once_with(Admin.sub_filter_menu)

    async def test_on_add_channel_input_username_with_bot(self):
        msg = make_message(text="@my_channel")
        bot = MagicMock()
        chat = MagicMock()
        chat.id = -1001234567890
        chat.title = "My Channel"
        chat.username = "my_channel"
        chat.invite_link = None
        bot.get_chat = AsyncMock(return_value=chat)
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": bot})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="@my_channel")
        inst.add_required_channel.assert_awaited_once_with(
            channel_id="-1001234567890",
            title="My Channel",
            invite_link="https://t.me/my_channel",
        )

    async def test_on_add_channel_input_chat_invite_link(self):
        msg = make_message(text="https://t.me/somechannel")
        bot = MagicMock()
        chat = MagicMock()
        chat.id = -100999
        chat.title = None
        chat.username = None
        chat.invite_link = "https://t.me/+joinlink"
        bot.get_chat = AsyncMock(return_value=chat)
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": bot})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="https://t.me/somechannel")
        assert inst.add_required_channel.await_args.kwargs["invite_link"] == "https://t.me/+joinlink"

    async def test_on_add_channel_input_invite_plus(self):
        msg = make_message(text="https://t.me/+AbCdEf123")
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": None})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="https://t.me/+AbCdEf123")
        inst.add_required_channel.assert_not_awaited()
        msg.answer.assert_awaited()
        assert "Пригласительные ссылки" in msg.answer.await_args.args[0]

    async def test_on_add_channel_input_joinchat(self):
        msg = make_message(text="t.me/joinchat/xyz")
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": None})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="t.me/joinchat/xyz")
        inst.add_required_channel.assert_not_awaited()
        msg.answer.assert_awaited()
        assert "Пригласительные ссылки" in msg.answer.await_args.args[0]

    async def test_on_add_channel_input_numeric_id(self):
        msg = make_message(text="-1001234567890")
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": None})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="-1001234567890")
        assert inst.add_required_channel.await_args.kwargs["channel_id"] == "-1001234567890"

    async def test_on_add_channel_input_plain_digits(self):
        msg = make_message(text="123456")
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": None})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="123456")
        assert inst.add_required_channel.await_args.kwargs["channel_id"] == "123456"

    async def test_on_add_channel_input_at_prefix_fallback(self):
        msg = make_message(text="@noBotChannel")
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": None})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="@noBotChannel")
        assert inst.add_required_channel.await_args.kwargs["invite_link"] == "https://t.me/noBotChannel"

    async def test_on_add_channel_input_plain_name_fallback(self):
        msg = make_message(text="plainname")
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": None})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="plainname")
        assert inst.add_required_channel.await_args.kwargs["invite_link"] == "https://t.me/plainname"

    async def test_on_add_channel_input_get_chat_failure(self):
        msg = make_message(text="@failchannel")
        bot = MagicMock()
        bot.get_chat = AsyncMock(side_effect=RuntimeError("telegram error"))
        mgr = make_manager({"session_factory": MagicMock(), "redis_client": AsyncMock(), "bot": bot})
        with patch("bot.dialogs.admin_menu.SubscriptionFilterManager") as MockMgr:
            inst = MockMgr.return_value
            inst.add_required_channel = AsyncMock()
            await on_add_channel_input(msg, MagicMock(), mgr, data="@failchannel")
        # предупреждение + успешное добавление
        assert msg.answer.await_count == 2


# ---------------------------------------------------------------------------
# segment broadcast
# ---------------------------------------------------------------------------
class TestSegments:
    async def test_build_segment_users_with_filters(self):
        udm = make_udm()

        async def all_ids():
            return list(range(1, 102))

        async def info(uid):
            if uid == 999:
                return None
            obj = MagicMock()
            obj.group = "О7" if uid % 2 == 0 else "ИУ5"
            obj.last_active_date = datetime.now() if uid % 3 else None
            return obj

        udm.get_all_user_ids = AsyncMock(side_effect=all_ids)
        udm.get_full_user_info = AsyncMock(side_effect=info)

        result = await build_segment_users(udm, "о7", 7)
        assert result  # часть пользователей прошла фильтр
        assert all(uid % 2 == 0 for uid in result)

    async def test_build_segment_users_no_filters(self):
        udm = make_udm()
        udm.get_all_user_ids = AsyncMock(return_value=[1, 2])
        udm.get_full_user_info = AsyncMock(return_value=make_user_info())
        result = await build_segment_users(udm, None, None)
        assert result == [1, 2]

    async def test_build_segment_users_skips_none_info(self):
        udm = make_udm()
        udm.get_all_user_ids = AsyncMock(return_value=[1])
        udm.get_full_user_info = AsyncMock(return_value=None)
        result = await build_segment_users(udm, None, None)
        assert result == []

    def test_render_template_full(self):
        info = make_user_info(uid=5, username="bob", group="О7", last_active=datetime(2024, 3, 4))
        out = render_template("{user_id}|{username}|{group}|{last_active}", info)
        assert out == "5|bob|О7|04.03.2024"

    def test_render_template_none_values(self):
        info = make_user_info(uid=5, username=None, group=None, last_active=None)
        out = render_template("{username}|{group}|{last_active}", info)
        assert out == "N/A|N/A|N/A"

    async def test_on_segment_criteria_input_with_days(self):
        msg = make_message(text="О7|7")
        mgr = make_manager(dialog_data={})
        await on_segment_criteria_input(msg, MagicMock(), mgr, data="О7|7")
        assert mgr.dialog_data["segment_group_prefix"] == "О7"
        assert mgr.dialog_data["segment_days_active"] == 7
        mgr.switch_to.assert_awaited_once_with(Admin.template_input)

    async def test_on_segment_criteria_input_invalid_days(self):
        msg = make_message(text="О7|abc")
        mgr = make_manager(dialog_data={})
        await on_segment_criteria_input(msg, MagicMock(), mgr, data="О7|abc")
        assert mgr.dialog_data["segment_days_active"] is None

    async def test_on_segment_criteria_input_no_pipe(self):
        msg = make_message(text="О7")
        mgr = make_manager(dialog_data={})
        await on_segment_criteria_input(msg, MagicMock(), mgr, data="О7")
        assert mgr.dialog_data["segment_group_prefix"] == "О7"
        assert mgr.dialog_data["segment_days_active"] is None

    async def test_on_template_input_message_text(self):
        msg = make_message(text="Hello {user_id}", content_type=ContentType.TEXT)
        mgr = make_manager(dialog_data={"segment_message_chat_id": 1, "segment_message_id": 2})
        await on_template_input_message(msg, MagicMock(), mgr)
        assert mgr.dialog_data["segment_template"] == "Hello {user_id}"
        assert mgr.dialog_data["segment_message_type"] == "text"
        assert "segment_message_chat_id" not in mgr.dialog_data
        assert "segment_message_id" not in mgr.dialog_data
        mgr.switch_to.assert_awaited_once_with(Admin.preview)

    async def test_on_template_input_message_media(self):
        msg = make_message(content_type=ContentType.PHOTO)
        mgr = make_manager(dialog_data={"segment_template": "old"})
        await on_template_input_message(msg, MagicMock(), mgr)
        assert mgr.dialog_data["segment_message_type"] == "media"
        assert mgr.dialog_data["segment_message_chat_id"] == msg.chat.id
        assert mgr.dialog_data["segment_message_id"] == msg.message_id
        assert "segment_template" not in mgr.dialog_data

    async def test_get_preview_data_text(self):
        udm = make_udm(get_full_user_info=AsyncMock(return_value=make_user_info(uid=1)))
        mgr = make_manager(
            {"user_data_manager": udm},
            dialog_data={
                "segment_group_prefix": None,
                "segment_days_active": None,
                "segment_template": "Hi {user_id}",
                "segment_message_type": "text",
            },
        )
        with patch("bot.dialogs.admin_menu.build_segment_users", new=AsyncMock(return_value=[1])):
            data = await get_preview_data(mgr)
        assert data["selected_count"] == 1
        assert "Hi 1" in data["preview_text"]

    async def test_get_preview_data_text_no_users(self):
        udm = make_udm()
        mgr = make_manager(
            {"user_data_manager": udm},
            dialog_data={"segment_message_type": "text", "segment_template": "x"},
        )
        with patch("bot.dialogs.admin_menu.build_segment_users", new=AsyncMock(return_value=[])):
            data = await get_preview_data(mgr)
        assert data["selected_count"] == 0
        assert "не удалось" in data["preview_text"]

    async def test_get_preview_data_media(self):
        udm = make_udm()
        mgr = make_manager(
            {"user_data_manager": udm},
            dialog_data={"segment_message_type": "media"},
        )
        with patch("bot.dialogs.admin_menu.build_segment_users", new=AsyncMock(return_value=[1, 2])):
            data = await get_preview_data(mgr)
        assert "Медиа" in data["preview_text"]
        assert data["selected_count"] == 2

    async def test_on_confirm_segment_send_text(self):
        cb = make_callback(user_id=99)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_full_user_info=AsyncMock(return_value=make_user_info()))
        mgr = make_manager(
            {"bot": bot, "user_data_manager": udm},
            dialog_data={
                "segment_message_type": "text",
                "segment_template": "Hi {user_id}",
                "segment_selected_ids": list(range(51)),
            },
        )
        with patch("bot.dialogs.admin_menu.send_message_task") as task:
            await on_confirm_segment_send(cb, MagicMock(), mgr)
            await drain_background_tasks()
        assert task.send.call_count == 51
        assert "текстовых" in bot.send_message.await_args[0][1]
        mgr.switch_to.assert_awaited_once_with(Admin.menu)

    async def test_on_confirm_segment_send_media(self):
        cb = make_callback(user_id=99)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm()
        mgr = make_manager(
            {"bot": bot, "user_data_manager": udm},
            dialog_data={
                "segment_message_type": "media",
                "segment_message_chat_id": 5,
                "segment_message_id": 6,
                "segment_selected_ids": list(range(51)),
            },
        )
        with patch("bot.dialogs.admin_menu.copy_message_task") as task:
            await on_confirm_segment_send(cb, MagicMock(), mgr)
            await drain_background_tasks()
        assert task.send.call_count == 51
        assert "медиа" in bot.send_message.await_args[0][1]

    async def test_on_confirm_segment_send_media_missing_ids(self):
        cb = make_callback(user_id=99)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager(
            {"bot": bot, "user_data_manager": make_udm()},
            dialog_data={"segment_message_type": "media", "segment_selected_ids": [1]},
        )
        with patch("bot.dialogs.admin_menu.copy_message_task") as task:
            await on_confirm_segment_send(cb, MagicMock(), mgr)
            await drain_background_tasks()
        task.send.assert_not_called()

    async def test_on_confirm_segment_send_text_skips_missing_info(self):
        cb = make_callback(user_id=99)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_full_user_info=AsyncMock(return_value=None))
        mgr = make_manager(
            {"bot": bot, "user_data_manager": udm},
            dialog_data={
                "segment_message_type": "text",
                "segment_template": "x",
                "segment_selected_ids": [1, 2],
            },
        )
        with patch("bot.dialogs.admin_menu.send_message_task") as task:
            await on_confirm_segment_send(cb, MagicMock(), mgr)
            await drain_background_tasks()
        task.send.assert_not_called()
        assert "Поставлено задач: 0" in bot.send_message.await_args[0][1]


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
class TestStats:
    async def test_on_period_selected(self):
        mgr = make_manager(dialog_data={})
        await on_period_selected(make_callback(), MagicMock(), mgr, item_id="30")
        assert mgr.dialog_data["stats_period"] == 30

    async def test_get_stats_data_full(self):
        udm = MagicMock()
        udm.get_total_users_count = AsyncMock(return_value=100)
        udm.get_active_users_by_period = AsyncMock(return_value=10)
        udm.get_subscribed_users_count = AsyncMock(return_value=50)
        udm.get_unsubscribed_count = AsyncMock(return_value=5)
        udm.get_subscription_breakdown = AsyncMock(return_value={"evening": 1, "morning": 2, "reminders": 3})
        udm.get_top_groups = AsyncMock(return_value=[("О7", 5), (None, 3)])
        udm.get_group_distribution = AsyncMock(return_value={"small": 4})
        udm.get_new_users_count = AsyncMock(return_value=7)
        udm.get_blocked_users_count = AsyncMock(return_value=2)
        udm.get_users_without_group_count = AsyncMock(return_value=8)
        udm._get_redis_client = AsyncMock(return_value=MagicMock())

        mgr = make_manager({"user_data_manager": udm}, dialog_data={"stats_period": 30})
        with patch(
            "core.analytics.get_top_features",
            new=AsyncMock(return_value=[("f1", "Feature", 3)]),
        ):
            data = await get_stats_data(mgr)
        assert data["period"] == 30
        assert "Feature" in data["stats_text"]
        assert "Месяц" in data["stats_text"]
        assert data["periods"] == [("День", 1), ("Неделя", 7), ("Месяц", 30)]

    async def test_get_stats_data_top_features_error(self):
        udm = MagicMock()
        udm.get_total_users_count = AsyncMock(return_value=0)
        udm.get_active_users_by_period = AsyncMock(return_value=0)
        udm.get_subscribed_users_count = AsyncMock(return_value=0)
        udm.get_unsubscribed_count = AsyncMock(return_value=0)
        udm.get_subscription_breakdown = AsyncMock(return_value={})
        udm.get_top_groups = AsyncMock(return_value=[])
        udm.get_group_distribution = AsyncMock(return_value={})
        udm.get_new_users_count = AsyncMock(return_value=0)
        udm.get_blocked_users_count = AsyncMock(return_value=0)
        udm.get_users_without_group_count = AsyncMock(return_value=0)
        udm._get_redis_client = AsyncMock(side_effect=RuntimeError("no redis"))

        mgr = make_manager({"user_data_manager": udm}, dialog_data={})
        data = await get_stats_data(mgr)
        assert "Нет данных" in data["stats_text"]
        assert "Чистый прирост" in data["stats_text"]

    async def test_get_stats_data_non_standard_types(self):
        udm = MagicMock()
        udm.get_total_users_count = AsyncMock(return_value="bad")
        udm.get_active_users_by_period = AsyncMock(return_value=None)
        udm.get_subscribed_users_count = AsyncMock(return_value=None)
        udm.get_unsubscribed_count = AsyncMock(return_value=None)
        udm.get_subscription_breakdown = AsyncMock(return_value="bad")
        udm.get_top_groups = AsyncMock(return_value="bad")
        udm.get_group_distribution = AsyncMock(return_value="bad")
        udm.get_new_users_count = AsyncMock(return_value="bad")
        udm.get_blocked_users_count = AsyncMock(return_value="bad")
        udm.get_users_without_group_count = AsyncMock(return_value="bad")
        udm._get_redis_client = AsyncMock(return_value=MagicMock())

        mgr = make_manager({"user_data_manager": udm}, dialog_data={"stats_period": 1})
        with patch("core.analytics.get_top_features", new=AsyncMock(return_value=[])):
            data = await get_stats_data(mgr)
        assert data["period"] == 1

    async def test_get_stats_data_without_optional_methods(self):
        class MinimalUDM:
            async def get_total_users_count(self):
                return 10

            async def get_active_users_by_period(self, days=1):
                return 3

            async def get_subscribed_users_count(self):
                return 4

            async def get_unsubscribed_count(self):
                return 1

            async def get_subscription_breakdown(self):
                return {"evening": 1}

            async def get_top_groups(self, limit=5):
                return []

            async def get_group_distribution(self):
                return {}

            async def get_new_users_count(self, days=7):
                return 2

        mgr = make_manager({"user_data_manager": MinimalUDM()}, dialog_data={})
        data = await get_stats_data(mgr)
        assert data["period"] == 7

    async def test_on_send_stats_chart_success(self):
        cb = make_callback()
        buf = io.BytesIO(b"png-bytes")
        udm = MagicMock()
        udm.get_activity_history = AsyncMock(return_value=([datetime(2024, 1, 1)], [1], [2]))
        mgr = make_manager({"user_data_manager": udm}, dialog_data={"stats_period": 7})
        with patch("core.analytics.generate_dynamics_chart", return_value=buf):
            await on_send_stats_chart(cb, MagicMock(), mgr)
        cb.message.answer_photo.assert_awaited_once()
        cb.answer.assert_awaited_once()

    async def test_on_send_stats_chart_history_error(self):
        cb = make_callback()
        buf = io.BytesIO(b"png")
        udm = MagicMock()
        udm.get_activity_history = AsyncMock(side_effect=RuntimeError("history fail"))
        mgr = make_manager({"user_data_manager": udm}, dialog_data={"stats_period": 1})
        with patch("core.analytics.generate_dynamics_chart", return_value=buf):
            await on_send_stats_chart(cb, MagicMock(), mgr)
        cb.message.answer_photo.assert_awaited_once()

    async def test_on_send_stats_chart_generation_error(self):
        cb = make_callback()
        udm = MagicMock()
        udm.get_activity_history = AsyncMock(return_value=([], [], []))
        mgr = make_manager({"user_data_manager": udm}, dialog_data={"stats_period": 30})
        with patch("core.analytics.generate_dynamics_chart", side_effect=RuntimeError("chart fail")):
            await on_send_stats_chart(cb, MagicMock(), mgr)
        cb.message.answer.assert_awaited_once()
        assert "Не удалось сгенерировать" in cb.message.answer.await_args[0][0]

    async def test_on_send_stats_chart_no_udm(self):
        cb = make_callback()
        buf = io.BytesIO(b"png")
        mgr = make_manager({"user_data_manager": None}, dialog_data={})
        with patch("core.analytics.generate_dynamics_chart", return_value=buf):
            await on_send_stats_chart(cb, MagicMock(), mgr)
        cb.message.answer_photo.assert_awaited_once()


# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------
class TestBroadcast:
    async def test_on_broadcast_received_text_two_args(self):
        msg = make_message(text="Hi {user_id}", content_type=ContentType.TEXT)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(
            get_all_user_ids=AsyncMock(return_value=list(range(51))),
            get_full_user_info=AsyncMock(return_value=make_user_info()),
        )
        mgr = make_manager({"bot": bot, "user_data_manager": udm})
        with patch("bot.dialogs.admin_menu.send_message_task") as task:
            await on_broadcast_received(msg, mgr)
            await drain_background_tasks()
        assert task.send.call_count == 51
        assert "завершена" in bot.send_message.await_args[0][1]
        mgr.switch_to.assert_awaited_once_with(Admin.menu)

    async def test_on_broadcast_received_text_three_args(self):
        msg = make_message(text="Hi", content_type=ContentType.TEXT)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_all_user_ids=AsyncMock(return_value=[1]))
        mgr = make_manager({"bot": bot, "user_data_manager": udm})
        with patch("bot.dialogs.admin_menu.send_message_task"):
            await on_broadcast_received(msg, MagicMock(), mgr)
            await drain_background_tasks()
        msg.reply.assert_awaited_once()

    async def test_on_broadcast_received_kwargs_fallback(self):
        msg = make_message(text="Hi", content_type=ContentType.TEXT)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_all_user_ids=AsyncMock(return_value=[1]))
        mgr = make_manager({"bot": bot, "user_data_manager": udm})
        with patch("bot.dialogs.admin_menu.send_message_task"):
            await on_broadcast_received(message=msg, manager=mgr)
            await drain_background_tasks()
        msg.reply.assert_awaited_once()

    async def test_on_broadcast_received_text_skips_missing_info(self):
        msg = make_message(text="Hi", content_type=ContentType.TEXT)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(
            get_all_user_ids=AsyncMock(return_value=[1, 2]),
            get_full_user_info=AsyncMock(return_value=None),
        )
        mgr = make_manager({"bot": bot, "user_data_manager": udm})
        with patch("bot.dialogs.admin_menu.send_message_task") as task:
            await on_broadcast_received(msg, mgr)
            await drain_background_tasks()
        task.send.assert_not_called()
        assert "Поставлено задач: 0" in bot.send_message.await_args[0][1]

    async def test_on_broadcast_received_media(self):
        msg = make_message(content_type=ContentType.PHOTO)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_all_user_ids=AsyncMock(return_value=list(range(51))))
        mgr = make_manager({"bot": bot, "user_data_manager": udm})
        with patch("bot.dialogs.admin_menu.copy_message_task") as task:
            await on_broadcast_received(msg, mgr)
            await drain_background_tasks()
        assert task.send.call_count == 51
        assert "Медиа-рассылка завершена" in bot.send_message.await_args[0][1]

    async def test_on_broadcast_received_media_error(self):
        msg = make_message(content_type=ContentType.PHOTO)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        udm = make_udm(get_all_user_ids=AsyncMock(return_value=[1, 2]))
        mgr = make_manager({"bot": bot, "user_data_manager": udm})
        with patch("bot.dialogs.admin_menu.copy_message_task") as task:
            task.send.side_effect = RuntimeError("queue down")
            await on_broadcast_received(msg, mgr)
            await drain_background_tasks()
        assert "Ошибка при медиа-рассылке" in bot.send_message.await_args[0][1]


# ---------------------------------------------------------------------------
# user management
# ---------------------------------------------------------------------------
class TestUserManagement:
    async def test_on_user_id_input_valid(self):
        msg = make_message(text="123")
        udm = make_udm(get_full_user_info=AsyncMock(return_value=make_user_info(uid=123)))
        mgr = make_manager({"user_data_manager": udm})
        await on_user_id_input(msg, MagicMock(), mgr, data="123")
        assert mgr.dialog_data["found_user_info"]["user_id"] == 123
        mgr.switch_to.assert_awaited_once_with(Admin.user_manage)

    async def test_on_user_id_input_invalid(self):
        msg = make_message(text="abc")
        mgr = make_manager({"user_data_manager": make_udm()})
        await on_user_id_input(msg, MagicMock(), mgr, data="abc")
        assert "должно быть числом" in msg.answer.await_args[0][0]

    async def test_on_user_id_input_not_found(self):
        msg = make_message(text="999")
        udm = make_udm(get_full_user_info=AsyncMock(return_value=None))
        mgr = make_manager({"user_data_manager": udm})
        await on_user_id_input(msg, MagicMock(), mgr, data="999")
        assert "не найден" in msg.answer.await_args[0][0]

    async def test_on_new_group_input_valid(self):
        msg = make_message(text="о7")
        timetable = MagicMock()
        timetable._schedules = {"О7": {}}
        udm = make_udm()
        mgr = make_manager(
            {"manager": timetable, "user_data_manager": udm},
            dialog_data={"found_user_info": {"user_id": 123, "group": "ИУ5"}},
        )
        await on_new_group_input(msg, MagicMock(), mgr, data="о7")
        udm.set_user_group.assert_awaited_once_with(123, "О7")
        assert mgr.dialog_data["found_user_info"]["group"] == "О7"
        mgr.switch_to.assert_awaited_once_with(Admin.user_manage)

    async def test_on_new_group_input_not_found(self):
        msg = make_message(text="XX9")
        timetable = MagicMock()
        timetable._schedules = {"О7": {}}
        mgr = make_manager({"manager": timetable})
        await on_new_group_input(msg, MagicMock(), mgr, data="XX9")
        assert "не найдена" in msg.answer.await_args[0][0]

    async def test_get_user_manage_data(self):
        mgr = make_manager(
            dialog_data={
                "found_user_info": {
                    "user_id": 1,
                    "username": "bob",
                    "group": "О7",
                    "reg_date": "2024-01-01",
                    "last_active": "2024-02-01",
                }
            }
        )
        data = await get_user_manage_data(mgr)
        assert "bob" in data["user_info_text"]

    async def test_get_user_manage_data_no_group(self):
        mgr = make_manager(dialog_data={"found_user_info": {"user_id": 1, "username": None, "group": None}})
        data = await get_user_manage_data(mgr)
        assert "Не установлена" in data["user_info_text"]

    async def test_get_user_manage_data_empty(self):
        mgr = make_manager(dialog_data={})
        data = await get_user_manage_data(mgr)
        assert data == {}

    async def test_on_send_message_to_user(self):
        mgr = make_manager()
        await on_send_message_to_user(make_callback(), MagicMock(), mgr)
        mgr.switch_to.assert_awaited_once_with(Admin.send_message_text)

    async def test_on_message_to_user_input_text(self):
        msg = make_message(text="hello user", content_type=ContentType.TEXT)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.copy_message = AsyncMock()
        mgr = make_manager({"bot": bot}, dialog_data={"found_user_info": {"user_id": 42}})
        await on_message_to_user_input(msg, MagicMock(), mgr)
        bot.send_message.assert_awaited_once_with(42, "hello user")
        mgr.switch_to.assert_awaited_once_with(Admin.user_manage)

    async def test_on_message_to_user_input_media(self):
        msg = make_message(content_type=ContentType.PHOTO)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        bot.copy_message = AsyncMock()
        mgr = make_manager({"bot": bot}, dialog_data={"found_user_info": {"user_id": 42}})
        await on_message_to_user_input(msg, MagicMock(), mgr)
        bot.copy_message.assert_awaited_once()

    async def test_on_message_to_user_input_no_user(self):
        msg = make_message(text="hi")
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot}, dialog_data={})
        await on_message_to_user_input(msg, MagicMock(), mgr)
        assert "пользователь не найден" in msg.answer.await_args[0][0]
        mgr.switch_to.assert_awaited_once_with(Admin.enter_user_id)

    async def test_on_message_to_user_input_error(self):
        msg = make_message(text="hi", content_type=ContentType.TEXT)
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=RuntimeError("blocked"))
        mgr = make_manager({"bot": bot}, dialog_data={"found_user_info": {"user_id": 42}})
        await on_message_to_user_input(msg, MagicMock(), mgr)
        assert "Ошибка при отправке" in msg.answer.await_args[0][0]


# ---------------------------------------------------------------------------
# cache / force update / cancel generation
# ---------------------------------------------------------------------------
class TestCacheAndGeneration:
    async def test_on_clear_cache_success(self):
        cb = make_callback(user_id=1)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot})
        before = {
            "total_files": 8,
            "total_size_mb": 10.0,
            "files": [f"f{i}.png" for i in range(7)],
            "redis_keys": 5,
        }
        after = {
            "total_files": 0,
            "total_size_mb": 0.0,
            "files": [],
            "redis_keys": 0,
        }
        with (
            patch(
                "bot.dialogs.admin_menu.get_cache_info",
                new=AsyncMock(side_effect=[before, after]),
            ),
            patch("bot.dialogs.admin_menu.cleanup_old_cache", new=AsyncMock()),
        ):
            await on_clear_cache(cb, MagicMock(), mgr)
        bot.send_message.assert_awaited_once()
        assert "и еще 2 файлов" in bot.send_message.await_args[0][1]

    async def test_on_clear_cache_error(self):
        cb = make_callback(user_id=1)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = make_manager({"bot": bot})
        with (
            patch(
                "bot.dialogs.admin_menu.get_cache_info",
                new=AsyncMock(side_effect=[{"error": "x"}, {"total_files": 0, "total_size_mb": 0}]),
            ),
            patch("bot.dialogs.admin_menu.cleanup_old_cache", new=AsyncMock()),
        ):
            await on_clear_cache(cb, MagicMock(), mgr)
        assert "Ошибка при работе с кэшем" in bot.send_message.await_args[0][1]

    async def test_on_force_update_schedule_success(self):
        cb = make_callback(user_id=1)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        redis = AsyncMock()
        mgr = make_manager({"bot": bot, "user_data_manager": MagicMock(), "redis_client": redis})
        with patch("bot.scheduler.monitor_schedule_changes", new=AsyncMock()) as monitor, patch("core.parser") as parser:
            await on_force_update_schedule(cb, MagicMock(), mgr)
            await drain_background_tasks()
        redis.delete.assert_awaited_once()
        monitor.assert_awaited_once()
        parser._LAST_ETAG = None
        cb.answer.assert_awaited_once()

    async def test_on_force_update_schedule_error(self):
        cb = make_callback(user_id=1)
        bot = MagicMock()
        bot.send_message = AsyncMock()
        redis = AsyncMock()
        redis.delete = AsyncMock(side_effect=RuntimeError("redis down"))
        mgr = make_manager({"bot": bot, "user_data_manager": MagicMock(), "redis_client": redis})
        with patch("bot.scheduler.monitor_schedule_changes", new=AsyncMock()), patch("core.parser"):
            await on_force_update_schedule(cb, MagicMock(), mgr)
            await drain_background_tasks()
        assert "Критическая ошибка" in bot.send_message.await_args[0][1]

    async def test_on_cancel_generation_active(self):
        cb = make_callback(user_id=321)
        active_generations[321] = {"cancelled": False, "status_msg_id": 11}
        try:
            await on_cancel_generation(cb)
        finally:
            active_generations.pop(321, None)
        cb.answer.assert_awaited_once()
        assert 321 not in active_generations
        cb.message.edit_text.assert_awaited_once()

    async def test_on_cancel_generation_edit_error(self):
        cb = make_callback(user_id=322)
        cb.message.edit_text = AsyncMock(side_effect=Exception("cannot edit"))
        active_generations[322] = {"cancelled": False, "status_msg_id": 11}
        try:
            await on_cancel_generation(cb)
        finally:
            active_generations.pop(322, None)
        assert 322 not in active_generations

    async def test_on_cancel_generation_none(self):
        cb = make_callback(user_id=323)
        active_generations.pop(323, None)
        await on_cancel_generation(cb)
        assert "Нет активной генерации" in cb.answer.await_args[0][0]
