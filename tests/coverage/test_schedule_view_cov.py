from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs import schedule_view as sv
from bot.dialogs.constants import DialogDataKeys
from bot.dialogs.states import FindMenu, MainMenu, Schedule, SettingsMenu
from tests.coverage.helpers import make_callback, make_dm, make_timetable_manager

FIXED_NOW = datetime(2026, 10, 22, 12, 0, tzinfo=sv.MOSCOW_TZ)
TODAY_ISO = FIXED_NOW.date().isoformat()


def sched_dm(tm=None, udm=None, dialog_data=None, start_data=None, lang="ru", from_user_id=123, bot=None):
    if udm is None:
        udm = AsyncMock()
        udm.get_user_language.return_value = lang
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
        from_user_id=from_user_id,
    )


# ---------------------------------------------------------------------------
# cleanup_old_cache
# ---------------------------------------------------------------------------


class TestCleanupOldCache:
    def _patch(self, tmp_path, redis, cache_mgr):
        return (
            patch.object(sv, "MEDIA_PATH", tmp_path),
            patch("core.config.get_redis_client", return_value=redis),
            patch("core.image_cache_manager.ImageCacheManager", return_value=cache_mgr),
        )

    async def test_full_cleanup(self, tmp_path):
        gen = tmp_path / "generated"
        gen.mkdir()
        (gen / "a.png").write_bytes(b"x" * 128)
        (gen / "b.png").write_bytes(b"y" * 64)
        redis = AsyncMock()
        redis.keys.return_value = ["k1", "k2"]
        redis.aclose = AsyncMock()
        cache_mgr = MagicMock()
        cache_mgr.cache_data_prefix = "data:"
        cache_mgr.cache_metadata_prefix = "meta:"
        cache_mgr.get_cache_stats = AsyncMock(return_value={"file_count": 2})

        p1, p2, p3 = self._patch(tmp_path, redis, cache_mgr)
        with p1, p2, p3:
            await sv.cleanup_old_cache()

        assert list(gen.glob("*.png")) == []
        assert redis.delete.await_count == 2

    async def test_file_delete_error_is_logged(self, tmp_path):
        gen = tmp_path / "generated"
        gen.mkdir()
        (gen / "directory.png").mkdir()
        redis = AsyncMock()
        redis.keys.return_value = []
        redis.aclose = AsyncMock()
        cache_mgr = MagicMock()
        cache_mgr.cache_data_prefix = "data:"
        cache_mgr.cache_metadata_prefix = "meta:"
        cache_mgr.get_cache_stats = AsyncMock(return_value={})

        p1, p2, p3 = self._patch(tmp_path, redis, cache_mgr)
        with p1, p2, p3:
            await sv.cleanup_old_cache()

        assert (gen / "directory.png").exists()

    async def test_redis_error_is_swallowed(self, tmp_path):
        redis = AsyncMock()
        redis.keys.side_effect = RuntimeError("redis down")
        redis.aclose = AsyncMock()
        cache_mgr = MagicMock()
        cache_mgr.cache_data_prefix = "data:"
        cache_mgr.cache_metadata_prefix = "meta:"
        cache_mgr.get_cache_stats = AsyncMock(return_value={})

        p1, p2, p3 = self._patch(tmp_path, redis, cache_mgr)
        with p1, p2, p3:
            await sv.cleanup_old_cache()

    async def test_outer_error_and_close_error(self, tmp_path):
        redis = AsyncMock()
        redis.aclose.side_effect = RuntimeError("close failed")
        cache_mgr = MagicMock()
        cache_mgr.get_cache_stats = AsyncMock(side_effect=RuntimeError("stats failed"))

        p1, p2, p3 = self._patch(tmp_path, redis, cache_mgr)
        with p1, p2, p3:
            await sv.cleanup_old_cache()


# ---------------------------------------------------------------------------
# get_cache_info
# ---------------------------------------------------------------------------


class TestGetCacheInfo:
    async def test_success_with_broken_file(self, tmp_path):
        gen = tmp_path / "generated"
        gen.mkdir()
        (gen / "ok.png").write_bytes(b"x" * 32)
        (gen / "broken.png").symlink_to(tmp_path / "missing.png")
        redis = AsyncMock()
        redis.aclose = AsyncMock()
        cache_mgr = MagicMock()
        cache_mgr.get_cache_stats = AsyncMock(
            return_value={"file_count": 1, "file_size_mb": 0.1, "redis_count": 2, "redis_size_mb": 0.2}
        )

        with (
            patch.object(sv, "MEDIA_PATH", tmp_path),
            patch("core.config.get_redis_client", return_value=redis),
            patch("core.image_cache_manager.ImageCacheManager", return_value=cache_mgr),
        ):
            result = await sv.get_cache_info()

        assert result["total_files"] == 1
        assert result["redis_keys"] == 2

    async def test_outer_error_returns_error_dict(self, tmp_path):
        redis = AsyncMock()
        redis.aclose = AsyncMock()
        cache_mgr = MagicMock()
        cache_mgr.get_cache_stats = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch.object(sv, "MEDIA_PATH", tmp_path),
            patch("core.config.get_redis_client", return_value=redis),
            patch("core.image_cache_manager.ImageCacheManager", return_value=cache_mgr),
        ):
            result = await sv.get_cache_info()

        assert "error" in result

    async def test_close_error_is_swallowed(self, tmp_path):
        redis = AsyncMock()
        redis.aclose.side_effect = RuntimeError("close failed")
        cache_mgr = MagicMock()
        cache_mgr.get_cache_stats = AsyncMock(return_value={})

        with (
            patch.object(sv, "MEDIA_PATH", tmp_path),
            patch("core.config.get_redis_client", return_value=redis),
            patch("core.image_cache_manager.ImageCacheManager", return_value=cache_mgr),
        ):
            result = await sv.get_cache_info()

        assert "cache_dir" in result


# ---------------------------------------------------------------------------
# get_schedule_data
# ---------------------------------------------------------------------------


class TestGetScheduleData:
    async def test_defaults_from_start_data(self):
        tm = make_timetable_manager()
        dm = sched_dm(tm=tm, dialog_data={}, start_data={"group": "О735Б"})
        data = await sv.get_schedule_data(dm)
        assert data["user_type"] == "student"
        assert data["user_type_emoji"] == ""
        tm.get_schedule_for_day.assert_awaited_once()

    async def test_student_with_lessons(self):
        tm = make_timetable_manager()
        tm.get_schedule_for_day.return_value = {
            "lessons": [],
            "date": date(2025, 9, 1),
            "week_name": "Нечетная",
        }
        dm = sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        data = await sv.get_schedule_data(dm)
        assert data["has_lessons"] is False

    async def test_teacher_canonical_success(self):
        tm = make_timetable_manager()
        tm._teachers_index = {}
        tm.resolve_canonical_teacher.return_value = "Иванов И.И."
        tm.get_teacher_schedule.return_value = {"lessons": [], "date": date(2025, 9, 1)}
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_type.return_value = "teacher"
        dm = sched_dm(
            tm=tm,
            udm=udm,
            dialog_data={DialogDataKeys.GROUP: "Иванов", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        data = await sv.get_schedule_data(dm)
        assert data["user_type"] == "teacher"
        assert data["user_type_emoji"] == "🧑‍🏫"
        udm.set_user_group.assert_awaited_once_with(user_id=123, group="Иванов И.И.")
        assert dm.current_context().dialog_data[DialogDataKeys.GROUP] == "Иванов И.И."

    async def test_teacher_canonical_set_group_error(self):
        tm = make_timetable_manager()
        tm._teachers_index = {}
        tm.resolve_canonical_teacher.return_value = "Иванов И.И."
        tm.get_teacher_schedule.return_value = {"lessons": [], "date": date(2025, 9, 1)}
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_type.return_value = "teacher"
        udm.set_user_group.side_effect = RuntimeError("db down")
        dm = sched_dm(
            tm=tm,
            udm=udm,
            dialog_data={DialogDataKeys.GROUP: "Иванов", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        data = await sv.get_schedule_data(dm)
        assert data["user_type"] == "teacher"

    async def test_teacher_canonical_not_found(self):
        tm = make_timetable_manager()
        tm._teachers_index = {}
        tm.resolve_canonical_teacher.return_value = None
        tm.get_teacher_schedule.return_value = {"lessons": [], "date": date(2025, 9, 1)}
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_type.return_value = "teacher"
        dm = sched_dm(
            tm=tm,
            udm=udm,
            dialog_data={DialogDataKeys.GROUP: "Иванов", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        await sv.get_schedule_data(dm)
        udm.set_user_group.assert_not_awaited()

    async def test_get_user_type_error_defaults_to_student(self):
        tm = make_timetable_manager()
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_type.side_effect = RuntimeError("boom")
        dm = sched_dm(
            tm=tm,
            udm=udm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        data = await sv.get_schedule_data(dm)
        assert data["user_type"] == "student"

    async def test_metrics_error_is_swallowed(self):
        tm = make_timetable_manager()
        dm = sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        with patch.object(sv, "GROUP_POPULARITY", MagicMock(labels=MagicMock(side_effect=RuntimeError("boom")))):
            data = await sv.get_schedule_data(dm)
        assert data["schedule_text"]

    async def test_error_day_info(self):
        tm = make_timetable_manager()
        tm.get_schedule_for_day.return_value = {"error": "no group"}
        dm = sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        data = await sv.get_schedule_data(dm)
        assert data["has_lessons"] is False


# ---------------------------------------------------------------------------
# get_week_image_data
# ---------------------------------------------------------------------------


class TestGetWeekImageData:
    def _image_service(self, success=True, path="/tmp/img.png"):
        svc = AsyncMock()
        svc.get_or_generate_week_image.return_value = (success, path)
        return svc

    async def test_defaults_and_success_student(self):
        tm = make_timetable_manager()
        tm._schedules = {"О735Б": {"odd": {"Понедельник": []}}}
        dm = sched_dm(tm=tm, dialog_data={}, start_data={"group": "О735Б"})
        svc = self._image_service()
        with patch("core.image_cache_manager.ImageCacheManager"), patch("core.image_service.ImageService", return_value=svc):
            data = await sv.get_week_image_data(dm)
        assert data["group"] == "О735Б"
        assert "start_date" in data
        svc.get_or_generate_week_image.assert_awaited_once()

    async def test_teacher_branch_with_placeholder(self):
        tm = make_timetable_manager()
        tm._teachers_index = {
            "Иванов И.И.": [
                {"day": "Понедельник", "week_code": "0"},
                {"day": "Вторник", "week_code": "1"},
                {"day": "Среда", "week_code": "2"},
                {"day": "Выходной", "week_code": "0"},
            ]
        }
        dm = sched_dm(
            tm=tm,
            dialog_data={
                DialogDataKeys.GROUP: "Иванов И.И.",
                DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                "user_type": "teacher",
                "placeholder_msg_id:Иванов И.И._odd": 77,
            },
        )
        svc = self._image_service()
        with patch("core.image_cache_manager.ImageCacheManager"), patch("core.image_service.ImageService", return_value=svc):
            data = await sv.get_week_image_data(dm)
        assert data["week_name"]
        svc.get_or_generate_week_image.assert_awaited_once()

    async def test_missing_week_info(self):
        tm = make_timetable_manager()
        tm.get_academic_week_type.return_value = None
        dm = sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        data = await sv.get_week_image_data(dm)
        assert data["week_name"] == "Неизвестно"

    async def test_metrics_error_is_swallowed(self):
        tm = make_timetable_manager()
        dm = sched_dm(
            tm=tm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        svc = self._image_service()
        with (
            patch.object(sv, "GROUP_POPULARITY", MagicMock(labels=MagicMock(side_effect=RuntimeError("boom")))),
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=svc),
        ):
            data = await sv.get_week_image_data(dm)
        assert data["group"] == "О735Б"

    async def test_user_theme_error_and_generation_failure(self):
        tm = make_timetable_manager()
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_theme.side_effect = RuntimeError("boom")
        dm = sched_dm(
            tm=tm,
            udm=udm,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"},
        )
        svc = self._image_service(success=False, path=None)
        with patch("core.image_cache_manager.ImageCacheManager"), patch("core.image_service.ImageService", return_value=svc):
            data = await sv.get_week_image_data(dm)
        assert data["group"] == "О735Б"


# ---------------------------------------------------------------------------
# on_full_week_image_click
# ---------------------------------------------------------------------------


class TestOnFullWeekImageClick:
    def _dm(self):
        tm = make_timetable_manager()
        tm.redis = AsyncMock()
        tm.redis.get = AsyncMock(return_value=None)
        return sched_dm(tm=tm, dialog_data={"user_id": None}), tm

    async def test_normal_flow(self):
        dm, tm = self._dm()
        callback = make_callback()
        with patch.object(sv, "get_week_image_data", AsyncMock()), patch("core.analytics.track_feature_click", AsyncMock()):
            await sv.on_full_week_image_click(callback, MagicMock(), dm)
        tm.redis.set.assert_awaited_once()
        dm.switch_to.assert_awaited_with(Schedule.view)
        assert dm.current_context().dialog_data["user_id"] == 123

    async def test_rate_limited(self):
        dm, tm = self._dm()
        tm.redis.get.return_value = "1"
        callback = make_callback()
        with patch.object(sv, "get_week_image_data", AsyncMock()):
            await sv.on_full_week_image_click(callback, MagicMock(), dm)
        callback.answer.assert_awaited()
        dm.switch_to.assert_not_awaited()

    async def test_redis_error_swallowed(self):
        dm, tm = self._dm()
        tm.redis.get.side_effect = RuntimeError("redis down")
        callback = make_callback()
        with patch.object(sv, "get_week_image_data", AsyncMock()), patch("core.analytics.track_feature_click", AsyncMock()):
            await sv.on_full_week_image_click(callback, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(Schedule.view)

    async def test_toast_error_fallback(self):
        dm, tm = self._dm()
        dm.middleware_data["user_data_manager"].get_user_language.side_effect = RuntimeError("boom")
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("answer failed")
        with patch.object(sv, "get_week_image_data", AsyncMock()), patch("core.analytics.track_feature_click", AsyncMock()):
            await sv.on_full_week_image_click(callback, MagicMock(), dm)

    async def test_switch_to_error_swallowed(self):
        dm, tm = self._dm()
        dm.switch_to.side_effect = RuntimeError("no state")
        callback = make_callback()
        with patch.object(sv, "get_week_image_data", AsyncMock()), patch("core.analytics.track_feature_click", AsyncMock()):
            await sv.on_full_week_image_click(callback, MagicMock(), dm)

    async def test_final_answer_error_swallowed(self):
        dm, tm = self._dm()
        callback = make_callback()
        callback.answer = AsyncMock(side_effect=[None, RuntimeError("answer failed")])
        with patch.object(sv, "get_week_image_data", AsyncMock()), patch("core.analytics.track_feature_click", AsyncMock()):
            await sv.on_full_week_image_click(callback, MagicMock(), dm)


# ---------------------------------------------------------------------------
# on_send_original_file
# ---------------------------------------------------------------------------


class TestOnSendOriginalFile:
    def _dm(self, dialog_data=None, udm=None, bot=None, tm=None):
        return sched_dm(
            tm=tm,
            udm=udm,
            bot=bot,
            dialog_data=(
                dialog_data
                if dialog_data is not None
                else {
                    DialogDataKeys.GROUP: "О735Б",
                    DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                }
            ),
        )

    async def test_not_subscribed_gate(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = self._dm(bot=bot)
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"):
            await sv.on_send_original_file(callback, None, dm)
        dm.switch_to.assert_awaited_with(Schedule.full_quality_gate)
        callback.message.answer.assert_awaited()

    async def test_not_subscribed_channel_id_link(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = self._dm(bot=bot)
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "-1001234567890"):
            await sv.on_send_original_file(callback, None, dm)
        callback.message.answer.assert_awaited()

    async def test_not_subscribed_plain_link(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = self._dm(bot=bot)
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "plainchan"):
            await sv.on_send_original_file(callback, None, dm)
        callback.message.answer.assert_awaited()

    async def test_not_subscribed_switch_error(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = self._dm(bot=bot)
        dm.switch_to.side_effect = RuntimeError("no state")
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"):
            await sv.on_send_original_file(callback, None, dm)

    async def test_not_subscribed_answer_error(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = self._dm(bot=bot)
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("answer failed")
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"):
            await sv.on_send_original_file(callback, None, dm)

    async def test_subscription_check_error_is_fail_closed(self):
        bot = AsyncMock()
        bot.get_chat_member.side_effect = RuntimeError("telegram down")
        dm = self._dm(bot=bot)
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)
        bot.send_document.assert_not_awaited()
        callback.answer.assert_awaited()

    async def test_subscription_helper_exception_is_swallowed(self):
        bot = AsyncMock()
        dm = self._dm(bot=bot)
        callback = make_callback()
        with (
            patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"),
            patch("core.subscription_filter.is_user_subscribed", side_effect=RuntimeError("boom")),
            patch("pathlib.Path.exists", return_value=True),
        ):
            await sv.on_send_original_file(callback, None, dm)
        bot.send_document.assert_awaited()

    async def test_subscribed_file_exists_sends_document(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(bot=bot)
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)
        bot.send_document.assert_awaited_once()

    async def test_send_document_answer_error(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(bot=bot)
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("answer failed")
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)
        bot.send_document.assert_awaited_once()

    async def test_generate_student_then_send(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        tm = make_timetable_manager()
        tm._schedules = {"О735Б": {"odd": {"Понедельник": []}}}
        dm = self._dm(bot=bot, tm=tm)
        callback = make_callback()
        svc = AsyncMock()
        svc.get_or_generate_week_image.return_value = (True, "/tmp/x.png")
        with (
            patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"),
            patch("pathlib.Path.exists", side_effect=[False, False, True]),
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=svc),
        ):
            await sv.on_send_original_file(callback, None, dm)
        bot.send_document.assert_awaited_once()

    async def test_generate_teacher_then_fallback(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        tm = make_timetable_manager()
        tm._teachers_index = {"Иванов И.И.": [{"day": "Понедельник", "week_code": "0"}]}
        dm = self._dm(
            bot=bot,
            tm=tm,
            dialog_data={
                DialogDataKeys.GROUP: "Иванов И.И.",
                DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01",
                "user_type": "teacher",
            },
        )
        callback = make_callback()
        svc = AsyncMock()
        svc.get_or_generate_week_image.return_value = (True, "/tmp/x.png")
        with (
            patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"),
            patch("pathlib.Path.exists", side_effect=[False, False, False]),
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=svc),
            patch.object(sv, "send_week_original_if_subscribed_task") as send_task,
        ):
            await sv.on_send_original_file(callback, None, dm)
        send_task.send.assert_called_once()
        callback.answer.assert_awaited()

    async def test_generation_error_is_swallowed(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(bot=bot)
        callback = make_callback()
        svc = AsyncMock()
        svc.get_or_generate_week_image.side_effect = RuntimeError("gen failed")
        with (
            patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"),
            patch("pathlib.Path.exists", side_effect=[False, False, False]),
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=svc),
            patch.object(sv, "send_week_original_if_subscribed_task") as send_task,
        ):
            await sv.on_send_original_file(callback, None, dm)
        send_task.send.assert_called_once()

    async def test_send_document_error_falls_back_to_queue(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        bot.send_document.side_effect = RuntimeError("cannot send")
        dm = self._dm(bot=bot)
        callback = make_callback()
        with (
            patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"),
            patch("pathlib.Path.exists", return_value=True),
            patch.object(sv, "send_week_original_if_subscribed_task") as send_task,
        ):
            await sv.on_send_original_file(callback, None, dm)
        send_task.send.assert_called_once()

    async def test_queue_send_error_and_answer_error(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(bot=bot)
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("answer failed")
        svc = AsyncMock()
        svc.get_or_generate_week_image.side_effect = RuntimeError("gen failed")
        with (
            patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"),
            patch("pathlib.Path.exists", return_value=False),
            patch("core.image_cache_manager.ImageCacheManager"),
            patch("core.image_service.ImageService", return_value=svc),
            patch.object(sv, "send_week_original_if_subscribed_task") as send_task,
        ):
            send_task.send.side_effect = RuntimeError("queue down")
            await sv.on_send_original_file(callback, None, dm)
        send_task.send.assert_called_once()

    async def test_week_info_missing(self):
        bot = AsyncMock()
        dm = self._dm(bot=bot)
        dm.middleware_data["manager"].get_academic_week_type.return_value = None
        callback = make_callback()
        await sv.on_send_original_file(callback, None, dm)
        callback.answer.assert_awaited()

    async def test_week_info_missing_answer_error(self):
        bot = AsyncMock()
        dm = self._dm(bot=bot)
        dm.middleware_data["manager"].get_academic_week_type.return_value = None
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("answer failed")
        await sv.on_send_original_file(callback, None, dm)

    async def test_group_restored_from_db(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_theme.return_value = "dark"
        udm.get_user_group.return_value = "О735Б"
        dm = self._dm(bot=bot, udm=udm, dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"})
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)
        assert dm.current_context().dialog_data[DialogDataKeys.GROUP] == "О735Б"

    async def test_group_restore_error_is_swallowed(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_theme.return_value = "standard"
        udm.get_user_group.side_effect = RuntimeError("db down")
        dm = self._dm(bot=bot, udm=udm, dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"})
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)

    async def test_invalid_date_is_replaced(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(
            bot=bot,
            dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "not-a-date"},
        )
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)

    async def test_missing_date_is_defaulted(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(bot=bot, dialog_data={DialogDataKeys.GROUP: "О735Б"})
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)

    async def test_theme_and_lang_errors_are_swallowed(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        udm = AsyncMock()
        udm.get_user_theme.side_effect = RuntimeError("boom")
        udm.get_user_language.side_effect = RuntimeError("boom")
        dm = self._dm(bot=bot, udm=udm)
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)
        bot.send_document.assert_awaited()

    async def test_toast_error_is_swallowed(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(bot=bot)
        callback = make_callback()
        callback.answer.side_effect = [RuntimeError("toast failed"), None]
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file(callback, None, dm)

    async def test_user_id_extraction_error(self):
        class _NoUserIdDict(dict):
            def __setitem__(self, key, value):
                if key == "user_id":
                    raise RuntimeError("cannot store user id")
                super().__setitem__(key, value)

        bot = AsyncMock()
        dm = self._dm(
            bot=bot,
            dialog_data=_NoUserIdDict({DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"}),
        )
        callback = make_callback()
        with patch("pathlib.Path.exists", return_value=True), patch.object(sv, "SUBSCRIPTION_CHANNEL", None):
            await sv.on_send_original_file(callback, None, dm)
        bot.send_document.assert_awaited_once()

    async def test_callback_wrappers(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = self._dm(bot=bot)
        callback = make_callback()
        with patch.object(sv, "SUBSCRIPTION_CHANNEL", "@chan"), patch("pathlib.Path.exists", return_value=True):
            await sv.on_send_original_file_callback(callback, dm)
            await sv.on_check_subscription_callback(callback, dm)
        assert bot.send_document.await_count == 2


# ---------------------------------------------------------------------------
# navigation helpers
# ---------------------------------------------------------------------------


class TestNavigation:
    async def test_on_date_shift(self):
        dm = sched_dm(dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"})
        await sv.on_date_shift(make_callback(), MagicMock(), dm, -7)
        assert dm.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == "2025-08-25"

    async def test_on_date_shift_analytics_error(self):
        dm = sched_dm(dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"})
        with patch("core.analytics.track_feature_click", side_effect=RuntimeError("boom")):
            await sv.on_date_shift(make_callback(), MagicMock(), dm, 1)
        assert dm.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == "2025-09-02"

    async def test_on_today_click(self):
        dm = sched_dm(dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2000-01-01"})
        with patch.object(sv, "datetime") as mock_datetime:
            mock_datetime.now.return_value = FIXED_NOW
            await sv.on_today_click(make_callback(), MagicMock(), dm)
        assert dm.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == TODAY_ISO

    async def test_on_today_click_analytics_error(self):
        dm = sched_dm(dialog_data={DialogDataKeys.CURRENT_DATE_ISO: "2000-01-01"})
        with (
            patch.object(sv, "datetime") as mock_datetime,
            patch("core.analytics.track_feature_click", side_effect=RuntimeError("boom")),
        ):
            mock_datetime.now.return_value = FIXED_NOW
            await sv.on_today_click(make_callback(), MagicMock(), dm)
        assert dm.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] == TODAY_ISO

    async def test_on_change_group_click(self):
        dm = sched_dm()
        await sv.on_change_group_click(make_callback(), MagicMock(), dm)
        dm.start.assert_awaited_once()

    async def test_on_settings_click(self):
        dm = sched_dm()
        await sv.on_settings_click(make_callback(), MagicMock(), dm)
        dm.start.assert_awaited_once()

    async def test_on_find_click(self):
        dm = sched_dm()
        await sv.on_find_click(make_callback(), MagicMock(), dm)
        dm.start.assert_awaited_once()


class TestNewsAndBack:
    async def test_on_news_clicked(self):
        dm = sched_dm()
        callback = make_callback()
        await sv.on_news_clicked(callback, MagicMock(), dm)
        callback.message.answer.assert_awaited()

    async def test_on_news_clicked_language_error(self):
        udm = AsyncMock()
        udm.get_user_language.side_effect = RuntimeError("boom")
        dm = sched_dm(udm=udm)
        callback = make_callback()
        await sv.on_news_clicked(callback, MagicMock(), dm)
        callback.message.answer.assert_awaited()

    async def test_on_inline_back(self):
        dm = sched_dm()
        callback = make_callback()
        await sv.on_inline_back(callback, dm)
        callback.message.delete.assert_awaited()
        callback.answer.assert_awaited()

    async def test_on_inline_back_errors(self):
        dm = sched_dm()
        callback = make_callback()
        callback.message.delete.side_effect = RuntimeError("cannot delete")
        callback.answer.side_effect = RuntimeError("cannot answer")
        await sv.on_inline_back(callback, dm)


class TestDeadShims:
    async def test_auto_delete_old_messages(self):
        assert await sv.auto_delete_old_messages(MagicMock(), 1) is None

    async def test_track_message(self):
        assert await sv.track_message(MagicMock(), 1, 2) is None
