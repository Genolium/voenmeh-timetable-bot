"""Regression tests for security, bug, performance, and UI/UX audit fixes."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs.find_menu import on_classroom_input, on_teacher_input
from bot.scheduler import evening_broadcast, morning_summary_broadcast
from bot.utils import worker_loop
from core.alert_webhook import TELEGRAM_MAX_MESSAGE_LENGTH, create_alert_app
from core.schedule_diff import GroupScheduleDiff, LessonChange, ScheduleDiffFormatter
from core.semester_calendar import _safe_date
from core.subscription_filter import SubscriptionFilterManager
from core.user_data import UserDataManager
from core.weather_api import WeatherAPI


@pytest.mark.asyncio
async def test_find_menu_empty_or_none_text():
    msg = MagicMock()
    msg.text = None
    msg.answer = AsyncMock()
    mgr = MagicMock()
    mgr.middleware_data = {"manager": MagicMock(), "_": lambda k, **kw: k}

    await on_teacher_input(msg, MagicMock(), mgr)
    msg.answer.assert_awaited_with("find_teacher_not_found")

    msg.answer.reset_mock()
    await on_classroom_input(msg, MagicMock(), mgr)
    msg.answer.assert_awaited_with("find_classroom_not_found")


@pytest.mark.asyncio
async def test_scheduler_broadcasts_survive_weather_error(monkeypatch):
    udm = MagicMock()
    udm.get_users_for_evening_notify = AsyncMock(return_value=[(1, "G1", "ru", "student")])
    udm.get_users_for_morning_summary = AsyncMock(return_value=[(1, "G1", "ru", "student")])
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(return_value={"lessons": [], "day_name": "Пн", "week_name": "Нечетная"})

    fake_weather = MagicMock()
    fake_weather.get_forecast_for_time = AsyncMock(side_effect=RuntimeError("weather down"))
    monkeypatch.setattr("bot.scheduler.WeatherAPI", lambda *a, **k: fake_weather)
    monkeypatch.setattr("bot.scheduler.send_message_task", MagicMock())

    await evening_broadcast(udm, tm)
    await morning_summary_broadcast(udm, tm)


def test_stop_worker_loop_joins_thread(monkeypatch):
    fake_loop = MagicMock()
    fake_loop.is_running.return_value = True
    fake_thread = MagicMock()
    fake_thread.is_alive.return_value = True
    monkeypatch.setattr(worker_loop, "_worker_loop", fake_loop)
    monkeypatch.setattr(worker_loop, "_worker_loop_thread", fake_thread)
    worker_loop.stop_worker_loop()
    fake_thread.join.assert_called_once_with(timeout=5.0)
    fake_thread.is_alive.return_value = False
    worker_loop.stop_worker_loop()


@pytest.mark.asyncio
async def test_alert_webhook_non_dict_and_truncation():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    app = create_alert_app(bot, [1])
    handler = next(r.handler for r in app.router.routes() if r.resource.canonical == "/alerts")

    req_list = MagicMock()
    req_list.headers = {}
    req_list.json = AsyncMock(return_value=["not", "a", "dict"])
    resp = await handler(req_list)
    assert resp.status == 400

    req_long = MagicMock()
    req_long.headers = {}
    req_long.json = AsyncMock(
        return_value={
            "status": "firing",
            "alerts": [{"labels": {"alertname": "Big"}, "annotations": {"description": "x" * 5000}}],
        }
    )
    resp2 = await handler(req_long)
    assert resp2.status == 200
    sent_text = bot.send_message.call_args.args[1]
    assert len(sent_text) <= TELEGRAM_MAX_MESSAGE_LENGTH
    assert sent_text.endswith("...")


def test_semester_calendar_safe_date_leap_rollover():
    assert _safe_date(2025, 2, 29) == date(2025, 2, 28)
    assert _safe_date(2024, 2, 29) == date(2024, 2, 29)


def test_schedule_diff_formatter_unknown_type_returns_none():
    diff = GroupScheduleDiff("G1", date(2026, 9, 1), [LessonChange("unknown_type", "id", "Math")])  # type: ignore[arg-type]
    assert ScheduleDiffFormatter.format_group_diff(diff) is None


@pytest.mark.asyncio
async def test_subscription_filter_invalidate_all_caches_clears_users():
    redis = MagicMock()
    redis.delete = AsyncMock()

    async def _scan_iter(match=None):
        yield "timetable:sub_filter:user:1"

    redis.scan_iter = _scan_iter
    mgr = SubscriptionFilterManager(MagicMock(), redis)
    await mgr.invalidate_all_caches()
    assert redis.delete.await_count == 2

    # Empty scan_iter
    async def _empty_scan(match=None):
        if False:
            yield ""

    redis.scan_iter = _empty_scan
    redis.delete.reset_mock()
    await mgr.invalidate_all_caches()
    redis.delete.assert_awaited_once()

    # Without scan_iter attribute
    redis_no_scan = SimpleNamespace(delete=AsyncMock())
    mgr2 = SubscriptionFilterManager(MagicMock(), redis_no_scan)  # type: ignore[arg-type]
    await mgr2.invalidate_all_caches()
    redis_no_scan.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_user_and_get_updates_username():
    udm = UserDataManager("sqlite+aiosqlite:///:memory:")
    await udm.init_db()
    await udm.register_user(777, "old_name")
    updated = await udm.register_user_and_get(777, "new_name")
    assert updated is not None
    assert updated.username == "new_name"
    await udm.engine.dispose()


@pytest.mark.asyncio
async def test_weather_api_naive_datetime_uses_moscow_tz():
    api = WeatherAPI(api_key="k", city_id="1")
    WeatherAPI._cache.clear()
    naive_dt = datetime(2026, 11, 20, 12, 0, 0)
    # 12:00 MSK == 09:00 UTC
    key = "1_metric_2026-11-20_09h_ru"
    WeatherAPI._cache[key] = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        "data": {"temperature": 5},
    }
    res = await api.get_forecast_for_time(naive_dt)
    assert res == {"temperature": 5}


@pytest.mark.asyncio
async def test_legacy_xml_non_numeric_start_year():
    from core import parser

    xml_body = (
        '<Timetable><Period StartYear="invalid" /><Weeks />'
        '<Group Number="G1"><Days><Day Title="Понедельник">'
        "<GroupLessons><Lesson><Time>09:00</Time><Discipline>Math</Discipline>"
        "<WeekCode>1</WeekCode></Lesson></GroupLessons></Day></Days></Group></Timetable>"
    )
    resp = MagicMock()
    resp.status = 200
    resp.raise_for_status = MagicMock()
    resp.read = AsyncMock(return_value=xml_body.encode("utf-8"))
    resp.headers = {}
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    res = await parser._fetch_and_parse_legacy_xml(session=session)
    assert isinstance(res, dict) and "G1" in res


@pytest.mark.asyncio
async def test_about_noop_pager_answers_callback():
    from bot.dialogs.about_menu import _noop_pager

    cb = MagicMock()
    cb.answer = AsyncMock()
    await _noop_pager(cb, MagicMock(), MagicMock())
    cb.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_view_on_text_week_splits_long_message(monkeypatch):
    from bot.dialogs import schedule_view

    cb = MagicMock()
    cb.from_user.id = 1
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    ctx = SimpleNamespace(dialog_data={"group": "G1", "current_date_iso": "2026-11-20"})
    dm = MagicMock()
    dm.current_context.return_value = ctx
    tm = MagicMock()
    tm._schedules = {"G1": {}}
    tm.get_schedule_for_day = AsyncMock(return_value={"week_name": "Нечетная", "day_name": "Пн", "lessons": []})
    udm = MagicMock()
    udm.get_user_language = AsyncMock(return_value="ru")
    dm.middleware_data = {"manager": tm, "user_data_manager": udm}

    long_week = "\n".join([f"Line {i}: " + ("x" * 100) for i in range(60)])
    monkeypatch.setattr(schedule_view, "format_full_week_text", lambda *a, **k: long_week)
    await schedule_view.on_text_week_click(cb, MagicMock(), dm)
    assert cb.message.answer.await_count >= 2
    assert all(len(c.args[0]) <= 4096 for c in cb.message.answer.call_args_list)


@pytest.mark.asyncio
async def test_inline_safe_answer_swallows_error():
    from bot.handlers.inline_handlers import _safe_answer

    q = MagicMock()
    q.answer = AsyncMock(side_effect=RuntimeError("query expired"))
    await _safe_answer(q, [])


@pytest.mark.asyncio
async def test_rate_limit_notify_throttling_and_clear():
    from bot.middlewares.rate_limit_middleware import RateLimitMiddleware

    mw = RateLimitMiddleware(MagicMock())
    msg = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    upd = SimpleNamespace(message=msg, callback_query=None, inline_query=None)
    await mw._notify_user(upd, {})
    assert msg.answer.await_count == 1
    # Immediate second call is throttled
    await mw._notify_user(upd, {})
    assert msg.answer.await_count == 1
    # Eviction branch when > 5000 entries
    mw._last_notified = {i: 0.0 for i in range(5001)}
    await mw._notify_user(upd, {})
    assert len(mw._last_notified) == 1


def test_image_compression_oversized_dimensions_and_cache_reuse(tmp_path):
    from PIL import Image

    from bot.utils.image_compression import get_telegram_safe_image_path

    # 1. Corrupt small file -> exception branch -> returns original
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not-an-image")
    assert get_telegram_safe_image_path(str(corrupt)) == str(corrupt)

    # 2. Small file size (<5MB) but height > 4096 -> compresses, then reuses cached compressed file
    tall = tmp_path / "tall.png"
    Image.new("RGB", (100, 4200), (255, 255, 255)).save(tall, "PNG")
    out1 = get_telegram_safe_image_path(str(tall))
    assert out1.endswith("_compressed.png")
    out2 = get_telegram_safe_image_path(str(tall))
    assert out2 == out1


def test_image_generator_progress_bar_zero_total():
    from core.image_generator import print_progress_bar

    print_progress_bar(0, 0)


def test_schedule_diff_preserves_duplicate_subgroups():
    from core.schedule_diff import ScheduleDiffDetector

    old = {
        "lessons": [
            {"time": "09:00-10:30", "subject": "Информатика", "room": "101", "teachers": "А"},
            {"time": "09:00-10:30", "subject": "Информатика", "room": "102", "teachers": "Б"},
        ]
    }
    new = {
        "lessons": [
            {"time": "09:00-10:30", "subject": "Информатика", "room": "101", "teachers": "А"},
            {"time": "09:00-10:30", "subject": "Информатика", "room": "205", "teachers": "Б"},
        ]
    }
    changes = ScheduleDiffDetector.compare_day_schedules(old, new)
    assert any(c.new_value == "205" for c in changes)


@pytest.mark.asyncio
async def test_scheduler_monitor_no_hash_skips_redis_set(monkeypatch):
    from bot import scheduler

    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"oldhash")
    redis.set = AsyncMock()
    lock = MagicMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    redis.lock = MagicMock(return_value=lock)
    udm = MagicMock()
    udm.async_session_maker = MagicMock()
    monkeypatch.setattr(scheduler, "fetch_and_parse_all_schedules", AsyncMock(return_value={"G1": {}}))
    monkeypatch.setattr("core.image_cache_manager.invalidate_all_generated_images", AsyncMock(return_value=0))
    monkeypatch.setattr(scheduler, "handle_graduated_groups", AsyncMock())
    monkeypatch.setattr(scheduler, "TimetableManager", MagicMock(return_value=MagicMock(save_to_cache=AsyncMock())))
    await scheduler.monitor_schedule_changes(udm, redis, MagicMock())
    # REDIS_SCHEDULE_HASH_KEY was not set with None
    for call in redis.set.call_args_list:
        assert call.args[1] is not None


def test_generate_week_image_task_with_explicit_user_theme(monkeypatch):
    import asyncio

    import bot.tasks as tasks

    monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
    fake_redis = MagicMock()
    fake_redis.aclose = AsyncMock()
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=fake_redis))
    fake_bot = MagicMock()
    fake_bot.__aenter__ = AsyncMock(return_value=fake_bot)
    fake_bot.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(tasks, "Bot", MagicMock(return_value=fake_bot))
    fake_svc = MagicMock()
    fake_svc.get_or_generate_week_image = AsyncMock(return_value=(True, "/tmp/x.png"))
    monkeypatch.setattr(tasks, "ImageService", MagicMock(return_value=fake_svc))
    tasks.generate_week_image_task("k", {}, "Нечетная", "G1", "odd", user_id=1, user_theme="dark")
    assert fake_svc.get_or_generate_week_image.call_args.kwargs["user_theme"] == "dark"
