"""Second pass over the bot layer branches.

The scenarios here are independent from ``test_branch_botcore_1.py``: different
data, different doubles and a different technique for the defensive branches
that cannot be reached with a consistent input.  As before, nothing in this
module touches the network, Docker or a real Telegram account.
"""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import bot.scheduler as sched
import bot.tasks as tasks
import bot.text_formatters as tf
import bot.utils.worker_loop as wl
from bot.middlewares.i18n_middleware import I18nMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.subscription_filter_middleware import SUB_CHECK_CALLBACK, SubscriptionFilterMiddleware
from core.config import MOSCOW_TZ


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #
def make_redis():
    redis = AsyncMock()
    redis.get.return_value = b""
    redis.set.return_value = True
    lock = AsyncMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    redis.lock = MagicMock(return_value=lock)
    return redis


def make_udm():
    return AsyncMock()


def _freeze_now(monkeypatch, hour, minute):
    class FrozenDatetime:
        moment = datetime.now(MOSCOW_TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)

        @classmethod
        def now(cls, tz=None):
            return cls.moment

        @classmethod
        def strptime(cls, value, fmt):
            return datetime.strptime(value, fmt)

        @classmethod
        def combine(cls, d, t):
            return datetime.combine(d, t)

    monkeypatch.setattr(sched, "datetime", FrozenDatetime)


def _i18n_with(overrides):
    original = tf.i18n.get

    def patched(key, lang="ru", **kwargs):
        if key in overrides:
            return overrides[key]
        return original(key, lang, **kwargs)

    return patched


class _RedisDouble:
    """Async Redis double with the handful of methods the tasks use."""

    def __init__(self, get_value=None):
        self.get_value = get_value
        self.closed = False

    async def get(self, key):
        return self.get_value

    async def set(self, key, value, ex=None):
        return True

    async def aclose(self):
        self.closed = True


# --------------------------------------------------------------------------- #
# bot/scheduler.py
# --------------------------------------------------------------------------- #
async def test_scheduler_break_without_known_next_start(monkeypatch):
    """scheduler.py 334 -> 340: the following lesson has no start time."""
    udm = make_udm()
    udm.get_users_for_lesson_reminders.return_value = [(7, "G7", 15, "ru")]
    lessons = [
        {"time": "09:00-11:15", "start_time_raw": "09:00", "end_time_raw": "11:15", "subject": "Лекция"},
        {"time": "11:25-12:40", "start_time_raw": "N/A", "end_time_raw": "12:40", "subject": "Семинар"},
    ]
    tm = MagicMock()
    tm.get_schedule_for_day = AsyncMock(return_value={"lessons": lessons})
    task = MagicMock()
    monkeypatch.setattr(sched, "send_lesson_reminder_task", task)
    _freeze_now(monkeypatch, 11, 15)

    await sched.minutely_reminders_checker(udm, tm)

    breaks = [call for call in task.send.call_args_list if call.args[2] == "break"]
    assert len(breaks) == 1
    assert breaks[0].args[3] is None


async def test_scheduler_retry_loop_stops_on_exhausted_budget(monkeypatch):
    """scheduler.py: исчерпание попыток оставляет прежнее расписание."""
    redis = make_redis()
    attempts_made = {"count": 0}

    async def broken_parser():
        attempts_made["count"] += 1
        raise RuntimeError("parser is down")

    monkeypatch.setattr(sched, "fetch_and_parse_all_schedules", broken_parser)
    monkeypatch.setattr("core.alert_sender.AlertSender.send", AsyncMock(return_value=True))

    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

    assert attempts_made["count"] == 3
    redis.lock.assert_not_called()


async def test_scheduler_change_with_unchanged_manager_instance(monkeypatch):
    """scheduler.py 421 -> 426: the active manager did not change."""
    redis = make_redis()
    redis.get.return_value = b"old-hash"
    monkeypatch.setattr(
        sched,
        "fetch_and_parse_all_schedules",
        AsyncMock(return_value={"__current_xml_hash__": "new-hash"}),
    )
    manager = MagicMock()
    manager.save_to_cache = AsyncMock()
    monkeypatch.setattr(sched, "TimetableManager", lambda *a, **k: manager)
    monkeypatch.setattr(sched, "SemesterSettingsManager", MagicMock())
    monkeypatch.setattr(sched, "global_timetable_manager_instance", manager)
    monkeypatch.setattr(sched, "manager_ref", None)
    monkeypatch.setattr("core.image_cache_manager.invalidate_all_generated_images", AsyncMock())
    notifier = AsyncMock(return_value=set())
    monkeypatch.setattr(sched, "send_schedule_diff_notifications", notifier)

    await sched.monitor_schedule_changes(make_udm(), redis, MagicMock())

    notifier.assert_not_awaited()


async def test_scheduler_diff_with_typed_users(monkeypatch):
    """scheduler.py 607 -> 609: the typed getter returned the users."""
    udm = make_udm()
    udm.get_students_with_groups_and_language = AsyncMock(return_value=[(3, "G3", "en")])
    udm.get_all_users_with_groups = AsyncMock(return_value=[(9, "G9", "ru")])
    udm.get_admin_users = AsyncMock(return_value=[])

    old_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})
    new_manager = MagicMock()
    new_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})

    changed = await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)

    udm.get_all_users_with_groups.assert_not_awaited()
    assert changed == set()


async def test_scheduler_diff_ignores_none_group(monkeypatch):
    """scheduler.py 618 -> 615: a row with ``group_name=None`` is skipped."""
    udm = make_udm()
    udm.get_students_with_groups_and_language = AsyncMock(return_value=[(4, None, "ru")])
    udm.get_admin_users = AsyncMock(return_value=[])

    old_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})
    new_manager = MagicMock()
    new_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})

    changed = await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)

    assert changed == set()
    old_manager.get_schedule_for_day.assert_not_awaited()


async def test_scheduler_diff_with_unformatted_message(monkeypatch):
    """scheduler.py 667 -> 638: the formatter returned ``None``."""
    udm = make_udm()
    udm.get_students_with_groups_and_language = AsyncMock(return_value=[(5, "G5", "ru")])
    udm.get_admin_users = AsyncMock(return_value=[])

    audit = MagicMock()
    audit.record_changes = AsyncMock()
    monkeypatch.setattr("core.audit_manager.AuditManager", MagicMock(return_value=audit))

    diff = MagicMock()
    diff.has_changes.return_value = True
    diff.changes = []
    monkeypatch.setattr(sched.ScheduleDiffDetector, "compare_group_schedules", MagicMock(return_value=diff))
    monkeypatch.setattr(sched.ScheduleDiffFormatter, "format_group_diff", MagicMock(return_value=None))

    old_manager = MagicMock()
    old_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})
    new_manager = MagicMock()
    new_manager.get_schedule_for_day = AsyncMock(return_value={"lessons": []})

    send = MagicMock()
    monkeypatch.setattr(sched, "send_message_task", send)
    monkeypatch.setattr(sched, "TASKS_SENT_TO_QUEUE", MagicMock())

    changed = await sched.send_schedule_diff_notifications(udm, old_manager, new_manager)

    assert changed == {"G5"}
    send.send.assert_not_called()


# --------------------------------------------------------------------------- #
# bot/tasks.py
# --------------------------------------------------------------------------- #
class _BrokerDouble:
    """Broker double that reports a fixed URL."""

    def __init__(self, url):
        self.url = url
        self.encoder = None
        self.middleware = []
        self.close_calls = 0

    def add_middleware(self, middleware):
        self.middleware.append(middleware)

    def close(self):
        self.close_calls += 1


def test_tasks_setup_broker_reuses_hardened_broker(monkeypatch):
    """tasks.py 226 -> 236: the broker already serves the hardened URL."""
    url = "amqp://guest:guest@rabbit:5672/"
    hardened, changed = tasks._harden_broker_url(url)
    assert changed is True

    broker = _BrokerDouble(hardened)
    created = []

    def fake_create(broker_url):
        created.append(broker_url)
        return broker

    monkeypatch.setattr(tasks, "_create_broker", fake_create)
    monkeypatch.setattr(tasks.dramatiq, "set_broker", lambda b: None)

    result_broker, result_url = tasks._setup_broker(url)

    assert result_broker is broker
    assert result_url == hardened
    assert created == [url]
    assert broker.close_calls == 0


def test_tasks_image_task_without_database_url(monkeypatch):
    """tasks.py: без DATABASE_URL тема пользователя не подтягивается."""
    service = MagicMock()
    service.get_or_generate_week_image = AsyncMock(return_value=(True, "p"))
    redis_client = _RedisDouble()

    class FakeBot:
        def __init__(self, *args, **kwargs):
            self.session = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=redis_client))
    monkeypatch.setattr(tasks, "ImageCacheManager", MagicMock())
    monkeypatch.setattr(tasks, "ImageService", MagicMock(return_value=service))
    monkeypatch.setattr(tasks, "Bot", FakeBot)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    tasks.generate_week_image_task("ck", {}, "Week", "G", "wk", user_id=7)

    kwargs = service.get_or_generate_week_image.await_args.kwargs
    assert kwargs["user_theme"] is None
    assert kwargs["user_id"] == 7
    assert redis_client.closed is True


def test_tasks_auto_generation_error_stays_silent(monkeypatch):
    """tasks.py 497 -> 500: an auto generation error notifies nobody."""
    service = MagicMock()
    service._generate_and_cache_image = AsyncMock(side_effect=RuntimeError("renderer is down"))
    monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=_RedisDouble()))
    monkeypatch.setattr(tasks, "ImageCacheManager", MagicMock())
    monkeypatch.setattr(tasks, "ImageService", MagicMock(return_value=service))
    error = AsyncMock()
    monkeypatch.setattr(tasks, "_send_error_message", error)

    tasks.generate_week_image_task("ck", {}, "Week", "G", "wk")

    error.assert_not_awaited()


def test_tasks_week_original_with_plain_http_link(monkeypatch, tmp_path):
    """tasks.py 659 -> 661: an ``http`` link is not rewritten."""
    redis_client = _RedisDouble(get_value=None)

    class FakeBot:
        def __init__(self, *args, **kwargs):
            self.get_chat_member = AsyncMock(side_effect=Exception("forbidden"))
            self.send_message = AsyncMock()
            self.send_document = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    bot = FakeBot()

    monkeypatch.setattr(tasks, "run_async", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=redis_client))
    monkeypatch.setattr(tasks, "_create_bot_with_timeout", lambda: bot)
    monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "http://t.me/plain")
    monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)

    tasks.send_week_original_if_subscribed_task(5, "ck")

    bot.send_message.assert_awaited_once()
    markup = bot.send_message.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "http://t.me/plain"


# --------------------------------------------------------------------------- #
# bot/text_formatters.py
# --------------------------------------------------------------------------- #
def test_text_formatters_evening_intro_with_list_context(monkeypatch):
    """text_formatters.py 490 -> 492: ``day_context`` is a plain list."""
    monkeypatch.setattr(tf.i18n, "get", _i18n_with({"day_context": ["monday"]}))

    text = tf.generate_evening_intro(None, datetime.now(MOSCOW_TZ))

    assert isinstance(text, str)


def test_text_formatters_evening_intro_with_text_advice(monkeypatch):
    """text_formatters.py 512 -> 522: ``clothing_advice`` is a string."""
    monkeypatch.setattr(tf.i18n, "get", _i18n_with({"clothing_advice": "wear a coat"}))

    text = tf.generate_evening_intro(
        {"temperature": -3, "description": "мороз", "emoji": "❄️"},
        datetime.now(MOSCOW_TZ),
        user_type="student",
    )

    assert "-3" in text


def test_text_formatters_reminder_break_without_advice(monkeypatch):
    """text_formatters.py 605 -> 608: the short break advice list is empty."""
    monkeypatch.setattr(tf.i18n, "get", _i18n_with({"reminder_break_short": []}))
    lesson = {"subject": "Физика", "type": "Практика", "time": "12:00-13:30", "room": "7"}

    text = tf.generate_reminder_text(lesson, "break", 10)

    assert isinstance(text, str)


def test_text_formatters_reminder_includes_lesson_details():
    """text_formatters.py: детали пары попадают в напоминание."""
    lesson = {"subject": "Геология", "type": "лек", "time": "14:00-15:30", "room": "101"}

    text = tf.generate_reminder_text(lesson, "first", None, reminder_time_minutes=30)

    assert isinstance(text, str)
    assert "Геология" in text
    assert "101" in text


# --------------------------------------------------------------------------- #
# bot/middlewares/i18n_middleware.py
# --------------------------------------------------------------------------- #
async def test_i18n_middleware_with_french_client():
    """i18n_middleware.py 34 -> 39: a French client keeps the default."""
    mw = I18nMiddleware()
    handler = AsyncMock(return_value="ok")
    data = {}
    user = SimpleNamespace(id=2, username="fr", language_code="fr-FR")
    event = SimpleNamespace(message=SimpleNamespace(from_user=user))

    await mw(handler, event, data)

    assert data["lang"] == "ru"


# --------------------------------------------------------------------------- #
# bot/middlewares/rate_limit_middleware.py
# --------------------------------------------------------------------------- #
class _LimiterRedis:
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


async def test_rate_limit_notification_for_inline_query_only():
    """rate_limit_middleware.py 126 -> exit: an inline query cannot be answered."""
    redis = _LimiterRedis()
    redis.store["rate:42:inline_query"] = [str(time.time()) for _ in range(2)]
    mw = RateLimitMiddleware(redis)
    handler = AsyncMock(return_value="ok")
    inline = SimpleNamespace(from_user=SimpleNamespace(id=42), query="q")
    event = SimpleNamespace(message=None, callback_query=None, inline_query=inline)

    assert await mw(handler, event, {}) is None

    handler.assert_not_awaited()


# --------------------------------------------------------------------------- #
# bot/middlewares/subscription_filter_middleware.py
# --------------------------------------------------------------------------- #
async def test_subscription_check_success_with_empty_message(monkeypatch):
    """subscription_filter_middleware.py 102 -> 108: there is nothing to delete."""
    monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
    mw = SubscriptionFilterMiddleware(MagicMock(), MagicMock())
    mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c"}])
    mw.manager.check_user_subscription = AsyncMock(return_value=(True, []))
    handler = AsyncMock()
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=11),
        data=SUB_CHECK_CALLBACK,
        message=0,  # falsy -> nothing is deleted
        answer=AsyncMock(),
    )
    event = SimpleNamespace(message=None, callback_query=callback)
    bot = AsyncMock()

    assert await mw(handler, event, {"bot": bot}) is None

    bot.send_message.assert_awaited_once()


class _UpdateMock(MagicMock):
    """MagicMock subclass used to attach a per-instance property."""


def _update_with_vanishing_message(message):
    update = _UpdateMock()
    update.callback_query = None
    type(update).message = PropertyMock(side_effect=[message, None])
    return update


async def test_subscription_gatekeeper_without_event_message(monkeypatch):
    """subscription_filter_middleware.py 179 -> 190: the message vanished."""
    monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
    mw = SubscriptionFilterMiddleware(MagicMock(), MagicMock())
    mw.manager.is_filter_enabled = AsyncMock(return_value=True)
    mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c", "title": "T"}])
    mw.manager.check_user_subscription = AsyncMock(return_value=(False, ["@c"]))
    handler = AsyncMock()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12),
        chat=SimpleNamespace(id=12),
        answer=AsyncMock(),
    )
    event = _update_with_vanishing_message(message)
    bot = AsyncMock()

    assert await mw(handler, event, {"bot": bot}) is None

    handler.assert_not_awaited()
    bot.send_message.assert_not_awaited()


# --------------------------------------------------------------------------- #
# bot/utils/worker_loop.py
# --------------------------------------------------------------------------- #
def test_worker_loop_survives_a_crashed_closed_loop(monkeypatch):
    """worker_loop.py 33 -> 35: a crashed loop that is already closed."""
    loop = MagicMock()
    loop.is_closed.return_value = True
    loop.run_forever.side_effect = RuntimeError("loop crashed")
    monkeypatch.setattr(wl, "_worker_loop", None)
    monkeypatch.setattr(wl, "_worker_loop_thread", None)
    monkeypatch.setattr(wl.asyncio, "new_event_loop", lambda: loop)
    monkeypatch.setattr(wl.asyncio, "set_event_loop", lambda loop: None)

    captured = {}

    class _RunningThread:
        def __init__(self, target, args, daemon, name):
            captured["target"] = target
            captured["args"] = args

        def start(self):
            captured["target"](*captured["args"])

        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(wl.threading, "Thread", _RunningThread)

    result = wl.get_worker_loop()

    assert result is loop
    loop.close.assert_not_called()
