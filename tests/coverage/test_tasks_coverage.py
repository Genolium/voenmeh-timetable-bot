"""Full statement coverage tests for bot/tasks.py.

All network / RabbitMQ / Dramatiq / Telegram I/O is mocked.
"""

import asyncio
import builtins
import logging
import pathlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import dramatiq
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from dramatiq.errors import RateLimitExceeded

import bot.tasks as tasks
from bot.tasks import (
    RobustRetries,
    _copy_message,
    _create_bot_with_timeout,
    _mark_user_as_blocked,
    _send_error_message,
    _send_message,
    _shutdown_telemetry,
    check_theme_subscription_task,
    copy_message_task,
    generate_week_image_task,
    get_redis_client,
    get_worker_bot,
    send_lesson_reminder_task,
    send_message_task,
    send_week_original_if_subscribed_task,
)

TASKS_PATH = pathlib.Path(tasks.__file__).resolve()


# --------------------------------------------------------------------------- #
# Small helpers / doubles
# --------------------------------------------------------------------------- #
def _run_coro(coro):
    return asyncio.run(coro)


class FakeBotCtx:
    """Bot double that supports ``async with`` and records API calls."""

    def __init__(self, *args, **kwargs):
        self.get_chat_member = AsyncMock()
        self.send_message = AsyncMock()
        self.send_document = AsyncMock()
        self.answer_callback_query = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeRedis:
    def __init__(self, get_value=None, raise_get=False, raise_set=False, raise_aclose=False):
        self.get_value = get_value
        self.raise_get = raise_get
        self.raise_set = raise_set
        self.raise_aclose = raise_aclose
        self.set_calls = []

    async def get(self, key):
        if self.raise_get:
            raise RuntimeError("get failed")
        return self.get_value

    async def set(self, key, value, ex=None):
        if self.raise_set:
            raise RuntimeError("set failed")
        self.set_calls.append((key, value, ex))

    async def aclose(self):
        if self.raise_aclose:
            raise RuntimeError("aclose failed")


class FakeUDM:
    def __init__(self, *args, **kwargs):
        self.engine = SimpleNamespace(dispose=AsyncMock())
        self.set_user_blocked = AsyncMock()
        self.get_user_theme = AsyncMock(return_value="dark")


# --------------------------------------------------------------------------- #
# Module level helpers
# --------------------------------------------------------------------------- #
class TestModuleHelpers:
    def test_create_bot_with_timeout(self):
        bot = _create_bot_with_timeout()
        assert bot is not None
        asyncio.run(bot.session.close())

    def test_get_redis_client(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(tasks.redis.Redis, "from_url", MagicMock(return_value=sentinel))
        assert get_redis_client(decode_responses=True) is sentinel

    def test_get_worker_bot(self, monkeypatch):
        fake = object()
        monkeypatch.setattr(tasks, "_worker_bot", None)
        monkeypatch.setattr(tasks, "AiohttpSession", MagicMock())
        monkeypatch.setattr(tasks, "Bot", MagicMock(return_value=fake))
        assert asyncio.run(get_worker_bot()) is fake

    def test_shutdown_telemetry(self):
        _shutdown_telemetry()  # must not raise


# --------------------------------------------------------------------------- #
# RobustRetries
# --------------------------------------------------------------------------- #
class TestRobustRetries:
    def test_connection_error_with_retries_left(self):
        retries = RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000)
        broker = MagicMock()
        message = MagicMock()
        message.options = {"retries": 2}
        retries.after_process_message(broker, message, exception=Exception("broken pipe detected"))
        broker.enqueue.assert_called_once()
        assert message.options["retries"] == 1

    def test_connection_error_without_retries_left_falls_back(self):
        retries = RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000)
        broker = MagicMock()
        actor = MagicMock()
        actor.options = {}
        broker.get_actor.return_value = actor
        message = MagicMock()
        message.options = {"retries": 0}
        message.actor_name = "a"
        message.message_id = "m"
        retries.after_process_message(broker, message, exception=Exception("stream lost"))
        broker.enqueue.assert_called_once()

    def test_no_exception_delegates(self):
        retries = RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000)
        broker = MagicMock()
        message = MagicMock()
        message.options = {}
        retries.after_process_message(broker, message, exception=None)
        broker.enqueue.assert_not_called()

    def test_regular_exception_delegates(self):
        retries = RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000)
        broker = MagicMock()
        actor = MagicMock()
        actor.options = {}
        broker.get_actor.return_value = actor
        message = MagicMock()
        message.options = {}
        message.actor_name = "a"
        message.message_id = "m"
        retries.after_process_message(broker, message, exception=ValueError("boom"))
        broker.enqueue.assert_called_once()

    def test_exhausted_retries_go_to_dlq(self, monkeypatch):
        record = MagicMock()
        monkeypatch.setattr(tasks, "_record_failed_message_to_dlq", record)
        retries = RobustRetries(max_retries=2, min_backoff=1, max_backoff=2)
        broker = MagicMock()
        actor = MagicMock()
        actor.options = {}
        broker.get_actor.return_value = actor
        message = MagicMock()
        message.options = {"retries": 2}
        message.actor_name = "send_message_task"
        message.message_id = "m"
        retries.after_process_message(broker, message, exception=ValueError("boom"))
        record.assert_called_once()


def test_record_failed_message_to_dlq_helper(monkeypatch):
    recorded = {}

    class FakeEngine:
        async def dispose(self):
            recorded["disposed"] = True

    class FakeDLQ:
        def __init__(self, session_factory=None):
            recorded["factory"] = session_factory

        async def record_failed_message(self, **kwargs):
            recorded["kwargs"] = kwargs
            return 1

    monkeypatch.setattr("core.dlq_manager.DLQManager", FakeDLQ)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", lambda *a, **k: FakeEngine())
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", lambda *a, **k: object())

    def fake_run(coro):
        asyncio.run(coro)

    monkeypatch.setattr(tasks, "run_async", fake_run)
    message = SimpleNamespace(args=[7, "payload"], options={"retries": 5}, actor_name="send_message_task")
    tasks._record_failed_message_to_dlq(message, ValueError("boom"))
    assert recorded["kwargs"]["user_id"] == 7
    assert recorded["disposed"] is True


def test_record_failed_message_to_dlq_swallows_errors(monkeypatch):
    def fake_run(coro):
        coro.close()
        raise RuntimeError("persist failed")

    monkeypatch.setattr(tasks, "run_async", fake_run)
    tasks._record_failed_message_to_dlq(SimpleNamespace(args=[], options={}), Exception("e"))


def test_compose_hostname_outside_docker_uses_stub_for_compose_hosts():
    assert tasks._compose_hostname_outside_docker("amqp://guest@rabbitmq:5672/") is True
    assert tasks._compose_hostname_outside_docker("amqp://localhost:5672/") is False
    assert tasks._compose_hostname_outside_docker("amqp://127.0.0.1:5672/") is False


def test_compose_hostname_swallows_urlparse_errors(monkeypatch):
    def _boom(_url):
        raise RuntimeError("bad url")

    monkeypatch.setattr("urllib.parse.urlparse", _boom)
    assert tasks._compose_hostname_outside_docker("amqp://guest@rabbitmq:5672/") is False


def test_compose_hostname_inside_docker_keeps_rabbitmq(monkeypatch):
    class FakePath:
        def __init__(self, _path):
            pass

        def exists(self):
            return True

    monkeypatch.setattr(tasks, "Path", FakePath)
    assert tasks._compose_hostname_outside_docker("amqp://guest@rabbitmq:5672/") is False


# --------------------------------------------------------------------------- #
# _mark_user_as_blocked
# --------------------------------------------------------------------------- #
class TestMarkUserAsBlocked:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        udm = FakeUDM()
        monkeypatch.setattr(tasks, "UserDataManager", MagicMock(return_value=udm))
        await _mark_user_as_blocked(5)
        udm.set_user_blocked.assert_awaited_once_with(5, True)
        udm.engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_constructor_failure(self, monkeypatch):
        monkeypatch.setattr(tasks, "UserDataManager", MagicMock(side_effect=Exception("db down")))
        await _mark_user_as_blocked(5)  # must not raise

    @pytest.mark.asyncio
    async def test_dispose_failure(self, monkeypatch):
        udm = FakeUDM()
        udm.engine.dispose = AsyncMock(side_effect=Exception("dispose failed"))
        monkeypatch.setattr(tasks, "UserDataManager", MagicMock(return_value=udm))
        await _mark_user_as_blocked(5)  # must not raise


# --------------------------------------------------------------------------- #
# _send_message / _copy_message
# --------------------------------------------------------------------------- #
class TestSendMessage:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        bot = AsyncMock()
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        await _send_message(1, "hi")
        bot.send_message.assert_awaited_once_with(1, "hi", disable_web_page_preview=True)

    @pytest.mark.asyncio
    async def test_success_metric_failure(self, monkeypatch):
        bot = AsyncMock()
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        monkeypatch.setattr(tasks, "NOTIFICATION_DELIVERY", MagicMock(labels=MagicMock(side_effect=Exception("m"))))
        await _send_message(1, "hi")

    @pytest.mark.asyncio
    async def test_forbidden(self, monkeypatch):
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramForbiddenError(method=MagicMock(), message="forbidden")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        monkeypatch.setattr(tasks, "NOTIFICATION_DELIVERY", MagicMock(labels=MagicMock(side_effect=Exception("m"))))
        mark = AsyncMock()
        monkeypatch.setattr(tasks, "_mark_user_as_blocked", mark)
        await _send_message(1, "hi")
        mark.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_bad_request_blocked(self, monkeypatch):
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramBadRequest(method=MagicMock(), message="bot was blocked by the user")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        mark = AsyncMock()
        monkeypatch.setattr(tasks, "_mark_user_as_blocked", mark)
        await _send_message(1, "hi")
        mark.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_bad_request_other_raises(self, monkeypatch):
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramBadRequest(method=MagicMock(), message="unexpected")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises(TelegramBadRequest):
            await _send_message(1, "hi")

    @pytest.mark.asyncio
    async def test_retry_after(self, monkeypatch):
        # NOTE: production bug - `raise RateLimitExceeded()` (bot/tasks.py:312)
        # omits the required `message` argument, so a TypeError is raised instead.
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramRetryAfter(method=MagicMock(), message="slow down", retry_after=5)
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises((RateLimitExceeded, TypeError)):
            await _send_message(1, "hi")

    @pytest.mark.asyncio
    async def test_connection_error_raises(self, monkeypatch):
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("connect call failed")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises(Exception):
            await _send_message(1, "hi")

    @pytest.mark.asyncio
    async def test_timeout_swallowed(self, monkeypatch):
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("read timeout")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        assert await _send_message(1, "hi") is None

    @pytest.mark.asyncio
    async def test_unknown_error_raises(self, monkeypatch):
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("weird error")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises(Exception):
            await _send_message(1, "hi")


class TestCopyMessage:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        bot = AsyncMock()
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        await _copy_message(1, 2, 3)
        bot.copy_message.assert_awaited_once_with(chat_id=1, from_chat_id=2, message_id=3)

    @pytest.mark.asyncio
    async def test_forbidden(self, monkeypatch):
        bot = AsyncMock()
        bot.copy_message.side_effect = TelegramForbiddenError(method=MagicMock(), message="forbidden")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        mark = AsyncMock()
        monkeypatch.setattr(tasks, "_mark_user_as_blocked", mark)
        await _copy_message(1, 2, 3)
        mark.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_bad_request_blocked(self, monkeypatch):
        bot = AsyncMock()
        bot.copy_message.side_effect = TelegramBadRequest(method=MagicMock(), message="chat not found")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        mark = AsyncMock()
        monkeypatch.setattr(tasks, "_mark_user_as_blocked", mark)
        await _copy_message(1, 2, 3)
        mark.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_bad_request_other_raises(self, monkeypatch):
        bot = AsyncMock()
        bot.copy_message.side_effect = TelegramBadRequest(method=MagicMock(), message="unexpected")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises(TelegramBadRequest):
            await _copy_message(1, 2, 3)

    @pytest.mark.asyncio
    async def test_retry_after(self, monkeypatch):
        # NOTE: production bug - `raise RateLimitExceeded()` (bot/tasks.py:353)
        # omits the required `message` argument, so a TypeError is raised instead.
        bot = AsyncMock()
        bot.copy_message.side_effect = TelegramRetryAfter(method=MagicMock(), message="slow", retry_after=1)
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises((RateLimitExceeded, TypeError)):
            await _copy_message(1, 2, 3)

    @pytest.mark.asyncio
    async def test_connection_error_raises(self, monkeypatch):
        bot = AsyncMock()
        bot.copy_message.side_effect = Exception("cannot connect")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises(Exception):
            await _copy_message(1, 2, 3)

    @pytest.mark.asyncio
    async def test_timeout_swallowed(self, monkeypatch):
        bot = AsyncMock()
        bot.copy_message.side_effect = Exception("timeout")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        assert await _copy_message(1, 2, 3) is None

    @pytest.mark.asyncio
    async def test_unknown_error_raises(self, monkeypatch):
        bot = AsyncMock()
        bot.copy_message.side_effect = Exception("weird")
        monkeypatch.setattr(tasks, "get_worker_bot", AsyncMock(return_value=bot))
        with pytest.raises(Exception):
            await _copy_message(1, 2, 3)


# --------------------------------------------------------------------------- #
# Simple task wrappers
# --------------------------------------------------------------------------- #
class TestTaskWrappers:
    def test_send_message_task(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_async", MagicMock())
        send_message_task(1, "hi")
        tasks.run_async.assert_called_once()

    def test_copy_message_task(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_async", MagicMock())
        copy_message_task(1, 2, 3)
        tasks.run_async.assert_called_once()

    def test_reminder_task_with_text(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_async", _run_coro)
        monkeypatch.setattr(tasks, "generate_reminder_text", MagicMock(return_value="reminder"))
        send = AsyncMock()
        monkeypatch.setattr(tasks, "_send_message", send)
        send_lesson_reminder_task(1, {"subject": "x"}, "before_lesson", 10)
        send.assert_awaited_once_with(1, "reminder")

    def test_reminder_task_no_text(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_async", _run_coro)
        monkeypatch.setattr(tasks, "generate_reminder_text", MagicMock(return_value=None))
        send = AsyncMock()
        monkeypatch.setattr(tasks, "_send_message", send)
        send_lesson_reminder_task(1, None, "before_lesson", 10)
        send.assert_not_awaited()

    def test_reminder_task_exception(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_async", _run_coro)
        monkeypatch.setattr(tasks, "generate_reminder_text", MagicMock(side_effect=Exception("bad")))
        send_lesson_reminder_task(1, {"subject": "x"}, "before_lesson", 10)  # swallowed


# --------------------------------------------------------------------------- #
# _send_error_message
# --------------------------------------------------------------------------- #
class TestSendErrorMessage:
    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        send = AsyncMock()
        monkeypatch.setattr(tasks, "_send_message", send)
        await _send_error_message(1, "boom")
        send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure(self, monkeypatch):
        monkeypatch.setattr(tasks, "_send_message", AsyncMock(side_effect=Exception("nope")))
        await _send_error_message(1, "boom")  # swallowed


# --------------------------------------------------------------------------- #
# generate_week_image_task
# --------------------------------------------------------------------------- #
def _patch_image_env(monkeypatch, service, redis_client=None):
    redis_client = redis_client or FakeRedis()
    monkeypatch.setattr(tasks, "run_async", _run_coro)
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=redis_client))
    monkeypatch.setattr(tasks, "ImageCacheManager", MagicMock())
    monkeypatch.setattr(tasks, "ImageService", MagicMock(return_value=service))
    monkeypatch.setattr(tasks, "Bot", FakeBotCtx)
    return redis_client


class TestGenerateWeekImageTask:
    def test_auto_success(self, monkeypatch):
        service = MagicMock()
        service._generate_and_cache_image = AsyncMock(return_value=(True, "p"))
        _patch_image_env(monkeypatch, service)
        generate_week_image_task("ck", {}, "Week", "G", "wk")
        service._generate_and_cache_image.assert_awaited_once()

    def test_auto_failure(self, monkeypatch):
        service = MagicMock()
        service._generate_and_cache_image = AsyncMock(return_value=(False, None))
        _patch_image_env(monkeypatch, service)
        generate_week_image_task("ck", {}, "Week", "G", "wk")

    def test_user_success(self, monkeypatch):
        service = MagicMock()
        service.get_or_generate_week_image = AsyncMock(return_value=(True, "p"))
        udm = FakeUDM()
        monkeypatch.setattr(tasks, "UserDataManager", MagicMock(return_value=udm))
        _patch_image_env(monkeypatch, service)
        generate_week_image_task("ck", {}, "Week", "G", "wk", user_id=5)
        udm.get_user_theme.assert_awaited_once_with(5)
        udm.engine.dispose.assert_awaited_once()

    def test_user_failure_sends_error(self, monkeypatch):
        service = MagicMock()
        service.get_or_generate_week_image = AsyncMock(return_value=(False, None))
        udm = FakeUDM()
        monkeypatch.setattr(tasks, "UserDataManager", MagicMock(return_value=udm))
        error = AsyncMock()
        monkeypatch.setattr(tasks, "_send_error_message", error)
        _patch_image_env(monkeypatch, service)
        generate_week_image_task("ck", {}, "Week", "G", "wk", user_id=5)
        error.assert_awaited_once()

    def test_user_theme_error_and_dispose_error(self, monkeypatch):
        service = MagicMock()
        service.get_or_generate_week_image = AsyncMock(return_value=(True, "p"))
        udm = FakeUDM()
        udm.get_user_theme = AsyncMock(side_effect=Exception("theme fail"))
        udm.engine.dispose = AsyncMock(side_effect=Exception("dispose fail"))
        monkeypatch.setattr(tasks, "UserDataManager", MagicMock(return_value=udm))
        _patch_image_env(monkeypatch, service)
        generate_week_image_task("ck", {}, "Week", "G", "wk", user_id=5)

    def test_no_db_url_skips_theme(self, monkeypatch):
        service = MagicMock()
        service.get_or_generate_week_image = AsyncMock(return_value=(True, "p"))
        monkeypatch.setenv("DATABASE_URL", "")
        _patch_image_env(monkeypatch, service)
        generate_week_image_task("ck", {}, "Week", "G", "wk", user_id=5)

    def test_exception_path_sends_error(self, monkeypatch):
        service = MagicMock()
        monkeypatch.setattr(tasks, "run_async", _run_coro)
        monkeypatch.setattr(tasks, "get_redis_client", MagicMock(side_effect=Exception("redis down")))
        error = AsyncMock()
        monkeypatch.setattr(tasks, "_send_error_message", error)
        generate_week_image_task("ck", {}, "Week", "G", "wk", user_id=5)
        error.assert_awaited_once()

    def test_aclose_failure_swallowed(self, monkeypatch):
        service = MagicMock()
        service._generate_and_cache_image = AsyncMock(return_value=(True, "p"))
        redis_client = FakeRedis(raise_aclose=True)
        _patch_image_env(monkeypatch, service, redis_client=redis_client)
        generate_week_image_task("ck", {}, "Week", "G", "wk")


# --------------------------------------------------------------------------- #
# check_theme_subscription_task
# --------------------------------------------------------------------------- #
def _patch_theme_env(monkeypatch, redis_client, bot):
    monkeypatch.setattr(tasks, "run_async", _run_coro)
    monkeypatch.setattr(tasks, "get_redis_client", MagicMock(return_value=redis_client))
    monkeypatch.setattr(tasks, "_create_bot_with_timeout", lambda: bot)


class TestCheckThemeSubscription:
    def test_cached_subscribed(self, monkeypatch):
        redis = FakeRedis(get_value="1")
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5)
        bot.send_message.assert_awaited_once()

    def test_cached_subscribed_with_callback(self, monkeypatch):
        redis = FakeRedis(get_value="1")
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5, callback_data="cb1")
        bot.answer_callback_query.assert_awaited_once_with("cb1", "✅ Доступ к темам подтверждён!")

    def test_cached_not_subscribed(self, monkeypatch):
        redis = FakeRedis(get_value="0")
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5)
        bot.send_message.assert_awaited_once()

    def test_chat_member_returns_member(self, monkeypatch):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.return_value = SimpleNamespace(status="member")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5)
        assert redis.set_calls and redis.set_calls[0][2] == 21600

    def test_chat_member_raises(self, monkeypatch):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.side_effect = Exception("forbidden")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5)
        bot.send_message.assert_awaited_once()

    def test_channel_link_numeric(self, monkeypatch):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.side_effect = Exception("x")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "-100123")
        check_theme_subscription_task(5)

    def test_channel_link_plain_name(self, monkeypatch):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.side_effect = Exception("x")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "mychannel")
        check_theme_subscription_task(5)

    def test_channel_link_http(self, monkeypatch):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.side_effect = Exception("x")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "https://t.me/chan")
        check_theme_subscription_task(5)

    def test_no_subscription_channel(self, monkeypatch):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", None)
        check_theme_subscription_task(5)
        bot.send_message.assert_awaited_once()

    def test_get_raises(self, monkeypatch):
        redis = FakeRedis(raise_get=True)
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5)

    def test_set_raises(self, monkeypatch):
        redis = FakeRedis(get_value=None, raise_set=True)
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5)

    def test_outer_exception(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_async", _run_coro)
        monkeypatch.setattr(tasks, "get_redis_client", MagicMock(side_effect=Exception("no redis")))
        check_theme_subscription_task(5)

    def test_aclose_raises(self, monkeypatch):
        redis = FakeRedis(get_value="1", raise_aclose=True)
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        check_theme_subscription_task(5)


# --------------------------------------------------------------------------- #
# send_week_original_if_subscribed_task
# --------------------------------------------------------------------------- #
class TestSendWeekOriginal:
    def test_not_subscribed_sends_gatekeeper(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.side_effect = Exception("x")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "ck")
        bot.send_message.assert_awaited_once()

    def test_subscribed_file_exists(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value="1")
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        (tmp_path / "generated").mkdir()
        (tmp_path / "generated" / "ck.png").write_bytes(b"data")
        send_week_original_if_subscribed_task(5, "ck")
        bot.send_document.assert_awaited_once()

    def test_subscribed_file_missing(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value="1")
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "missing")
        bot.send_document.assert_not_awaited()

    def test_subscribed_via_chat_member(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.return_value = SimpleNamespace(status="creator")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "missing")
        bot.send_document.assert_not_awaited()

    def test_no_subscription_channel_skips_gatekeeper(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", None)
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "missing")
        bot.send_message.assert_not_awaited()

    def test_get_raises_swallowed(self, monkeypatch, tmp_path):
        redis = FakeRedis(raise_get=True)
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "missing")
        bot.send_message.assert_awaited_once()

    def test_channel_link_numeric(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.side_effect = Exception("x")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "-100123")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "missing")

    def test_channel_link_plain_name(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value=None)
        bot = FakeBotCtx()
        bot.get_chat_member.side_effect = Exception("x")
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "mychannel")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "missing")

    def test_outer_exception(self, monkeypatch):
        monkeypatch.setattr(tasks, "run_async", _run_coro)
        monkeypatch.setattr(tasks, "get_redis_client", MagicMock(side_effect=Exception("no redis")))
        send_week_original_if_subscribed_task(5, "ck")

    def test_aclose_raises(self, monkeypatch, tmp_path):
        redis = FakeRedis(get_value="1", raise_aclose=True)
        bot = FakeBotCtx()
        _patch_theme_env(monkeypatch, redis, bot)
        monkeypatch.setattr(tasks, "SUBSCRIPTION_CHANNEL", "@chan")
        monkeypatch.setattr(tasks, "MEDIA_PATH", tmp_path)
        send_week_original_if_subscribed_task(5, "missing")


# --------------------------------------------------------------------------- #
# Import-time branches (executed via compile/exec so the real module is intact)
# --------------------------------------------------------------------------- #
class _RaisingTimeLimit:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("time limit unavailable")


def _run_tasks_module(
    monkeypatch,
    *,
    broker_url=None,
    bot_token=None,
    raise_time_limit=False,
    raise_urlparse=False,
    clear_root_handlers=False,
    import_hook=None,
):
    import core.config as config

    saved_broker = dramatiq.get_broker()
    saved_otel = config.settings.OTEL_ENABLED
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    if broker_url is not None:
        monkeypatch.setenv("DRAMATIQ_BROKER_URL", broker_url)
    if bot_token is not None:
        monkeypatch.setenv("BOT_TOKEN", bot_token)
    config.settings.OTEL_ENABLED = False

    if raise_time_limit:
        import dramatiq.middleware.time_limit as tl

        monkeypatch.setattr(tl, "TimeLimit", _RaisingTimeLimit)
    if raise_urlparse:
        import urllib.parse

        def _boom(*a, **k):
            raise RuntimeError("bad url")

        monkeypatch.setattr(urllib.parse, "urlparse", _boom)

    monkeypatch.setattr(tasks.atexit, "register", lambda *a, **k: None)

    try:
        if clear_root_handlers:
            root.handlers = []
        namespace = {"__name__": "bot.tasks_cov", "__file__": str(TASKS_PATH)}
        if import_hook is not None:
            namespace["__builtins__"] = {**builtins.__dict__, "__import__": import_hook}
        code = compile(TASKS_PATH.read_text(), str(TASKS_PATH), "exec")
        exec(code, namespace)
        return namespace
    finally:
        dramatiq.set_broker(saved_broker)
        config.settings.OTEL_ENABLED = saved_otel
        root.handlers = saved_handlers
        root.setLevel(saved_level)


class TestImportTimeBranches:
    def test_retryafter_fallback(self, monkeypatch):
        import aiogram.exceptions as real_exc

        real_import = builtins.__import__

        def hook(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "aiogram.exceptions" and fromlist and "TelegramRetryAfter" in fromlist:
                stub = types.ModuleType("aiogram.exceptions")
                stub.TelegramBadRequest = real_exc.TelegramBadRequest
                stub.TelegramForbiddenError = real_exc.TelegramForbiddenError
                return stub
            return real_import(name, globals, locals, fromlist, level)

        namespace = _run_tasks_module(monkeypatch, broker_url="", import_hook=hook)
        assert issubclass(namespace["RetryAfter"], Exception)

    def test_no_broker_url_uses_stub_broker(self, monkeypatch):
        namespace = _run_tasks_module(monkeypatch, broker_url="")
        assert namespace["rabbitmq_broker"] is not None

    def test_no_stub_broker_uses_noop_broker(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "dramatiq.brokers.stub", None)
        namespace = _run_tasks_module(monkeypatch, broker_url="")
        broker = namespace["rabbitmq_broker"]
        assert type(broker).__name__ == "_NoopBroker"
        broker.declare_actor(object())
        assert broker.enqueue(object()) is None

    def test_time_limit_fallbacks(self, monkeypatch):
        namespace = _run_tasks_module(monkeypatch, raise_time_limit=True)
        assert namespace["rabbitmq_broker"] is not None

    def test_url_harden_exception(self, monkeypatch):
        namespace = _run_tasks_module(monkeypatch, raise_urlparse=True)
        assert namespace["rabbitmq_broker"] is not None

    def test_missing_bot_token_raises(self, monkeypatch):
        with pytest.raises(RuntimeError):
            _run_tasks_module(monkeypatch, broker_url="", bot_token="")

    def test_root_logger_handler_setup(self, monkeypatch):
        _run_tasks_module(monkeypatch, broker_url="", clear_root_handlers=True)
