"""Branch coverage for the bot layer (scheduler / tasks / formatters / middlewares).

Every test below targets exactly one ``line -> line`` arc that
``coverage.py --cov-branch`` reported as missing.  All external systems
(Redis, Dramatiq, the university parser, the weather API and Telegram) are
replaced by in-test doubles, so the module stays fast and completely offline.
"""

import asyncio
import sys
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.scheduler as sched
import bot.tasks as tasks
import bot.text_formatters as tf
import bot.utils.worker_loop as wl
from bot.middlewares.i18n_middleware import I18nMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.subscription_filter_middleware import SUB_CHECK_CALLBACK, SubscriptionFilterMiddleware
from core.config import MOSCOW_TZ


# --------------------------------------------------------------------------- #
# Shared doubles
# --------------------------------------------------------------------------- #
def make_redis():
    """Async Redis double with a working ``lock()`` context manager."""
    redis = AsyncMock()
    redis.get.return_value = b""
    redis.set.return_value = True
    redis.incr.return_value = 2
    lock = AsyncMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    redis.lock = MagicMock(return_value=lock)
    return redis


def make_udm():
    return AsyncMock()


def _patch_now(monkeypatch, hour, minute):
    """Freeze ``datetime.now()`` inside bot.scheduler."""

    class FakeDatetime:
        fixed = datetime.now(MOSCOW_TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)

        @classmethod
        def now(cls, tz=None):
            return cls.fixed

        @classmethod
        def strptime(cls, value, fmt):
            return datetime.strptime(value, fmt)

        @classmethod
        def combine(cls, d, t):
            return datetime.combine(d, t)

    monkeypatch.setattr(sched, "datetime", FakeDatetime)


def _fake_i18n_get(overrides):
    """``i18n.get`` replacement that returns ``overrides`` for chosen keys."""
    original = tf.i18n.get

    def fake_get(key, lang="ru", **kwargs):
        if key in overrides:
            return overrides[key]
        return original(key, lang, **kwargs)

    return fake_get


class FakeRedis:
    """Minimal async Redis double used by the worker/task tests."""

    def __init__(self, get_value=None):
        self.get_value = get_value
        self.set_calls = []
        self.closed = False

    async def get(self, key):
        return self.get_value

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))

    async def aclose(self):
        self.closed = True


# --------------------------------------------------------------------------- #
# bot/scheduler.py
# --------------------------------------------------------------------------- #
async def test_scheduler_break_event_without_next_lesson_start(monkeypatch):
    """scheduler.py 334 -> 340: the next lesson has no parsable start time.

    The break duration stays ``None`` when ``start_time_raw`` of the following
    lesson is missing or ``"N/A"``.
    """
    udm = make_udm()
    udm.get_users_for_lesson_reminders.return_value = [(1, "G1", 20, "ru")]
    lessons = [
        {"time": "09:00-10:30", "start_time_raw": "09:00", "end_time_raw": "10:30", "subject": "A"},
        {"time": "10:40-12:10", "start_time_raw": "N/A", "end_time_raw": "12:10", "subject": "B"},
    ]
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(return_value={"lessons": lessons})
    task = MagicMock()
    monkeypatch.setattr(sched, "send_lesson_reminder_task", task)
    _patch_now(monkeypatch, 10, 30)

    await sched.minutely_reminders_checker(udm, tm)

    breaks = [call for call in task.send.call_args_list if call.args[2] == "break"]
    assert breaks, "the break reminder must be scheduled"
    assert breaks[0].args[3] is None, "break duration is unknown without a next start time"


async def test_scheduler_retry_budget_exhausted(monkeypatch):
    """scheduler.py: after three failed attempts the old schedule is kept."""
    redis = make_redis()
    calls = []

    async def failing_parser():
        calls.append(1)
        raise RuntimeError("parser down")

    monkeypatch.setattr(sched, "fetch_and_parse_all_schedules", failing_parser)
    monkeypatch.setattr("core.alert_sender.AlertSender.send", AsyncMock(return_value=True))

    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

    assert len(calls) == 3, "парсер должен быть опробован три раза"
    # Прежнее расписание сохраняем: кэш не трогаем, блокировку не берём.
    redis.lock.assert_not_called()


async def test_scheduler_change_without_previous_manager(monkeypatch):
    """scheduler.py 421 -> 426: there is no previous manager to diff against."""
    redis = make_redis()
    redis.get.return_value = b"old-hash"
    monkeypatch.setattr(
        sched,
        "fetch_and_parse_all_schedules",
        AsyncMock(return_value={"__current_xml_hash__": "new-hash"}),
    )
    new_manager = MagicMock()
    new_manager.save_to_cache = AsyncMock()
    monkeypatch.setattr(sched, "TimetableManager", lambda *a, **k: new_manager)
    monkeypatch.setattr(sched, "SemesterSettingsManager", MagicMock())
    monkeypatch.setattr(sched, "global_timetable_manager_instance", None)
    monkeypatch.setattr(sched, "manager_ref", None)
    monkeypatch.setattr("core.image_cache_manager.invalidate_all_generated_images", AsyncMock())
    diff_notifier = AsyncMock(return_value={"G1"})
    monkeypatch.setattr(sched, "send_schedule_diff_notifications", diff_notifier)

    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

    diff_notifier.assert_not_awaited()
    assert redis.set.await_args_list[0].args == ("timetable:schedule_hash", "new-hash")


async def test_scheduler_diff_uses_typed_user_getter(monkeypatch):
    """scheduler.py 607 -> 609: the typed getter already returned the users."""
    udm = make_udm()
    udm.get_students_with_groups_and_language = AsyncMock(return_value=[(1, "G1", "ru")])
    udm.get_all_users_with_groups = AsyncMock(return_value=[])
    udm.get_admin_users = AsyncMock(return_value=[])

    audit = MagicMock()
    audit.record_changes = AsyncMock()
    monkeypatch.setattr("core.audit_manager.AuditManager", MagicMock(return_value=audit))

    old_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})
    new_manager = MagicMock()
    new_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})

    changed = await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)

    udm.get_all_users_with_groups.assert_not_awaited()
    assert changed == set()


async def test_scheduler_diff_skips_rows_without_group(monkeypatch):
    """scheduler.py 618 -> 615: a row without a group name is skipped."""
    udm = make_udm()
    udm.get_students_with_groups_and_language = AsyncMock(return_value=[(1, "", "ru")])
    udm.get_admin_users = AsyncMock(return_value=[])

    old_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})
    new_manager = MagicMock()
    new_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})

    changed = await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)

    assert changed == set()
    old_manager.get_schedule_for_day.assert_not_awaited()


async def test_scheduler_diff_skips_empty_message(monkeypatch):
    """scheduler.py 667 -> 638: an empty diff message sends nothing."""
    udm = make_udm()
    udm.get_students_with_groups_and_language = AsyncMock(return_value=[(1, "G1", "ru")])
    udm.get_admin_users = AsyncMock(return_value=[])

    audit = MagicMock()
    audit.record_changes = AsyncMock()
    monkeypatch.setattr("core.audit_manager.AuditManager", MagicMock(return_value=audit))

    diff = MagicMock()
    diff.has_changes.return_value = True
    diff.changes = []
    monkeypatch.setattr(sched.ScheduleDiffDetector, "compare_group_schedules", MagicMock(return_value=diff))
    monkeypatch.setattr(sched.ScheduleDiffFormatter, "format_group_diff", MagicMock(return_value=""))

    old_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})
    new_manager = MagicMock()
    new_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})

    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)
    monkeypatch.setattr(sched, "TASKS_SENT_TO_QUEUE", MagicMock())

    changed = await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)

    assert changed == {"G1"}
    send.send.assert_not_called()


# --------------------------------------------------------------------------- #
# bot/tasks.py
# --------------------------------------------------------------------------- #
def test_tasks_setup_broker_keeps_matching_url(monkeypatch):
    """tasks.py 226 -> 236: the broker is not recreated for an equal URL."""
    url = "amqp://guest:guest@localhost:5672/"
    hardened, changed = tasks._harden_broker_url(url)
    assert changed is True

    broker = MagicMock()
    broker.url = hardened
    created = []

    def fake_create(broker_url):
        created.append(broker_url)
        return broker

    monkeypatch.setattr(tasks, "_create_broker", fake_create)
    monkeypatch.setattr(tasks.dramatiq, "set_broker", lambda b: None)

    result_broker, result_url = tasks._setup_broker(url)

    assert result_broker is broker
    assert result_url == hardened
    assert created == [url], "the broker must not be re-created"
    broker.close.assert_not_called()


def test_tasks_image_generation_uses_user_theme(monkeypatch):
    """tasks.py: тема пользователя подтягивается из БД перед генерацией."""
    service = MagicMock()
    service.get_or_generate_week_image = AsyncMock(return_value=(True, "p"))
    udm = MagicMock()
    udm.get_user_theme = AsyncMock(return_value="nord")
    udm.engine = MagicMock()
    udm.engine.dispose = AsyncMock()

    class FakeBot:
        def __init__(self, *args, **kwargs):
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            self.closed = True
            return False

    redis_client = FakeRedis()
    monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=redis_client))
    monkeypatch.setattr(tasks, "ImageCacheManager", MagicMock())
    monkeypatch.setattr(tasks, "ImageService", MagicMock(return_value=service))
    monkeypatch.setattr(tasks, "Bot", FakeBot)
    monkeypatch.setattr(tasks, "UserDataManager", MagicMock(return_value=udm))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/test")

    tasks.generate_week_image_task("ck", {}, "Week", "G", "wk", user_id=5)

    udm.get_user_theme.assert_awaited_once_with(5)
    kwargs = service.get_or_generate_week_image.await_args.kwargs
    assert kwargs["user_theme"] == "nord"
    assert kwargs["user_id"] == 5
    assert redis_client.closed is True


def test_tasks_auto_generation_failure_sends_no_error_message(monkeypatch):
    """tasks.py 497 -> 500: auto generation failures stay silent."""
    service = MagicMock()
    service._generate_and_cache_image = AsyncMock(return_value=(False, None))
    monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(side_effect=RuntimeError("redis down")))
    monkeypatch.setattr(tasks, "ImageCacheManager", MagicMock())
    monkeypatch.setattr(tasks, "ImageService", MagicMock(return_value=service))
    error = AsyncMock()
    monkeypatch.setattr(tasks, "_send_error_message", error)

    # No user id -> the task runs in auto mode and nobody has to be notified.
    tasks.generate_week_image_task("ck", {}, "Week", "G", "wk")

    error.assert_not_awaited()


def test_tasks_week_original_keeps_http_channel_link(monkeypatch, tmp_path):
    """tasks.py 659 -> 661: an ``http`` channel link is used as is."""
    redis_client = FakeRedis(get_value=None)

    class FakeBotCtx:
        """Bot double that supports ``async with``."""

        def __init__(self, *args, **kwargs):
            self.get_chat_member = AsyncMock(side_effect=Exception("forbidden"))
            self.send_message = AsyncMock()
            self.send_document = AsyncMock()
            self.answer_callback_query = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    bot = FakeBotCtx()

    monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=redis_client))
    monkeypatch.setattr(tasks, "_create_bot_with_timeout", lambda: bot)
    monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "https://t.me/chan")
    monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)

    tasks.send_week_original_if_subscribed_task(5, "ck")

    bot.send_message.assert_awaited_once()
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/chan"


# --------------------------------------------------------------------------- #
# bot/text_formatters.py
# --------------------------------------------------------------------------- #
def test_text_formatters_evening_intro_context_is_not_a_dict(monkeypatch):
    """text_formatters.py 490 -> 492: ``day_context`` is not a mapping."""
    monkeypatch.setattr(tf.i18n, "get", _fake_i18n_get({"day_context": "not-a-dict"}))

    text = tf.generate_evening_intro(None, datetime.now(MOSCOW_TZ))

    assert isinstance(text, str)
    assert text


def test_text_formatters_evening_intro_clothing_advice_is_not_a_dict(monkeypatch):
    """text_formatters.py 512 -> 522: ``clothing_advice`` is not a mapping."""
    monkeypatch.setattr(tf.i18n, "get", _fake_i18n_get({"clothing_advice": ["cold"]}))

    text = tf.generate_evening_intro(
        {"temperature": 5, "description": "ясно", "emoji": "☀️"},
        datetime.now(MOSCOW_TZ),
        user_type="student",
    )

    assert isinstance(text, str)
    assert "5" in text


def test_text_formatters_reminder_short_break_without_advice_list(monkeypatch):
    """text_formatters.py 605 -> 608: the short-break advice is not a list."""
    monkeypatch.setattr(tf.i18n, "get", _fake_i18n_get({"reminder_break_short": "short break"}))
    lesson = {"subject": "Матан", "type": "Лекция", "time": "10:40-12:10", "room": "101"}

    text = tf.generate_reminder_text(lesson, "break", 5)

    assert isinstance(text, str)
    assert text


def test_text_formatters_reminder_with_lesson_details():
    """text_formatters.py: детали пары добавляются к напоминанию."""
    lesson = {"subject": "Матан", "type": "лек", "time": "09:00-10:30", "room": "101"}

    text = tf.generate_reminder_text(lesson, "first", None, reminder_time_minutes=15)

    assert isinstance(text, str)
    assert "Матан" in text
    assert "101" in text


# --------------------------------------------------------------------------- #
# bot/middlewares/i18n_middleware.py
# --------------------------------------------------------------------------- #
async def test_i18n_middleware_language_without_zh_or_en():
    """i18n_middleware.py 34 -> 39: an unknown language code stays ``ru``."""
    mw = I18nMiddleware()
    handler = AsyncMock(return_value="ok")
    data = {}
    user = SimpleNamespace(id=1, username="user", language_code="de-DE")
    event = SimpleNamespace(message=SimpleNamespace(from_user=user))

    assert await mw(handler, event, data) == "ok"

    assert data["lang"] == "ru"
    assert data["i18n_lang"] == "ru"


# --------------------------------------------------------------------------- #
# bot/middlewares/rate_limit_middleware.py
# --------------------------------------------------------------------------- #
class _RateLimitRedis:
    """Async Redis double for the rate limiter."""

    def __init__(self):
        self.store = {}

    async def lrange(self, key, start, end):
        return list(self.store.get(key, []))

    async def rpush(self, key, value):
        self.store.setdefault(key, []).append(value)

    async def expire(self, key, ttl):
        return True

    async def ltrim(self, key, start, end):
        return True


async def test_rate_limit_notification_without_message_or_callback():
    """rate_limit_middleware.py 126 -> exit: nothing can be answered.

    Inline queries carry a user id (so the limiter applies) but the
    notification helper knows neither messages nor callback queries.
    """
    redis = _RateLimitRedis()
    redis.store["rate:7:inline_query"] = [str(time.time())]
    mw = RateLimitMiddleware(redis)
    handler = AsyncMock(return_value="ok")
    inline = SimpleNamespace(from_user=SimpleNamespace(id=7), query="q")
    event = SimpleNamespace(message=None, callback_query=None, inline_query=inline)

    assert await mw(handler, event, {}) is None

    handler.assert_not_awaited()


# --------------------------------------------------------------------------- #
# bot/middlewares/subscription_filter_middleware.py
# --------------------------------------------------------------------------- #
async def test_subscription_check_success_without_message(monkeypatch):
    """subscription_filter_middleware.py 102 -> 108: nothing to delete."""
    monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
    mw = SubscriptionFilterMiddleware(MagicMock(), MagicMock())
    mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c"}])
    mw.manager.check_user_subscription = AsyncMock(return_value=(True, []))
    handler = AsyncMock()
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=5),
        data=SUB_CHECK_CALLBACK,
        message=None,
        answer=AsyncMock(),
    )
    event = SimpleNamespace(message=None, callback_query=callback)
    bot = AsyncMock()

    assert await mw(handler, event, {"bot": bot}) is None

    callback.answer.assert_awaited_once()
    bot.send_message.assert_awaited_once()


class _VanishingUpdate:
    """Update whose ``message`` is only visible for the first lookup."""

    def __init__(self, message):
        self._message = message
        self._lookups = 0
        self.callback_query = None

    @property
    def message(self):
        self._lookups += 1
        return self._message if self._lookups == 1 else None


async def test_subscription_gatekeeper_without_message_event(monkeypatch):
    """subscription_filter_middleware.py 179 -> 190: no message to answer."""
    monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
    mw = SubscriptionFilterMiddleware(MagicMock(), MagicMock())
    mw.manager.is_filter_enabled = AsyncMock(return_value=True)
    mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c", "title": "T"}])
    mw.manager.check_user_subscription = AsyncMock(return_value=(False, ["@c"]))
    handler = AsyncMock()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=5),
        chat=SimpleNamespace(id=5),
        answer=AsyncMock(),
    )
    event = _VanishingUpdate(message)
    bot = AsyncMock()

    assert await mw(handler, event, {"bot": bot}) is None

    handler.assert_not_awaited()
    bot.send_message.assert_not_awaited()


# --------------------------------------------------------------------------- #
# bot/utils/worker_loop.py
# --------------------------------------------------------------------------- #
def test_worker_loop_does_not_close_an_already_closed_loop(monkeypatch):
    """worker_loop.py 33 -> 35: the loop was closed by somebody else."""
    loop = MagicMock()
    loop.is_closed.return_value = True
    monkeypatch.setattr(wl, "_worker_loop", None)
    monkeypatch.setattr(wl, "_worker_loop_thread", None)
    monkeypatch.setattr(wl.asyncio, "new_event_loop", lambda: loop)
    monkeypatch.setattr(wl.asyncio, "set_event_loop", lambda loop: None)

    started = {}

    class _ImmediateThread:
        def __init__(self, target, args, daemon, name):
            started["target"] = target
            started["args"] = args

        def start(self):
            started["target"](*started["args"])

        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(wl.threading, "Thread", _ImmediateThread)

    result = wl.get_worker_loop()

    assert result is loop
    loop.close.assert_not_called()
    loop.run_forever.assert_called_once()
