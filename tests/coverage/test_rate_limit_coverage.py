"""Full statement coverage tests for bot/middlewares/rate_limit_middleware.py."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.middlewares.rate_limit_middleware import RateLimitMiddleware


class FakeRedis:
    """Minimal async Redis double exposing only the methods used by the limiter."""

    def __init__(self):
        self.store = {}
        self.expires = {}
        self.trims = []
        self.raise_on_lrange = False

    async def lrange(self, key, start, end):
        if self.raise_on_lrange:
            raise RuntimeError("redis down")
        return list(self.store.get(key, []))

    async def rpush(self, key, value):
        self.store.setdefault(key, []).append(value)

    async def expire(self, key, ttl):
        self.expires[key] = ttl

    async def ltrim(self, key, start, end):
        self.trims.append((key, start, end))
        if end == -1:
            self.store[key] = self.store.get(key, [])[-50:]


def _user(uid=1):
    return SimpleNamespace(id=uid, username="user", language_code="ru")


def _message_update(uid=1):
    return SimpleNamespace(
        message=SimpleNamespace(from_user=_user(uid), text="hi", answer=AsyncMock()),
        callback_query=None,
        inline_query=None,
    )


class TestEventTypeAndUser:
    def test_get_event_type_all_branches(self):
        mw = RateLimitMiddleware(FakeRedis())
        assert mw._get_event_type(SimpleNamespace(message=object(), callback_query=None, inline_query=None)) == "message"
        assert (
            mw._get_event_type(SimpleNamespace(message=None, callback_query=object(), inline_query=None)) == "callback_query"
        )
        assert mw._get_event_type(SimpleNamespace(message=None, callback_query=None, inline_query=object())) == "inline_query"
        assert mw._get_event_type(SimpleNamespace(message=None, callback_query=None, inline_query=None)) == "default"

    def test_get_user_id_all_branches(self):
        mw = RateLimitMiddleware(FakeRedis())
        assert (
            mw._get_user_id(
                SimpleNamespace(message=SimpleNamespace(from_user=_user(11)), callback_query=None, inline_query=None)
            )
            == 11
        )
        assert (
            mw._get_user_id(
                SimpleNamespace(
                    message=None,
                    callback_query=SimpleNamespace(from_user=_user(22)),
                    inline_query=None,
                )
            )
            == 22
        )
        assert (
            mw._get_user_id(
                SimpleNamespace(
                    message=None,
                    callback_query=None,
                    inline_query=SimpleNamespace(from_user=_user(33)),
                )
            )
            == 33
        )
        # message without from_user -> falls through to callback/inline -> None
        assert (
            mw._get_user_id(SimpleNamespace(message=SimpleNamespace(from_user=None), callback_query=None, inline_query=None))
            is None
        )

    def test_custom_limits_and_window(self):
        mw = RateLimitMiddleware(FakeRedis(), limits={"message": 9.0}, window_seconds=2.0)
        assert mw.limits["message"] == 9.0
        assert mw.window == 2.0


class TestIsRateLimited:
    @pytest.mark.asyncio
    async def test_under_limit(self):
        redis = FakeRedis()
        mw = RateLimitMiddleware(redis)
        result = await mw._is_rate_limited("k", 5.0)
        assert result is False
        assert redis.store["k"]
        assert "k" in redis.expires

    @pytest.mark.asyncio
    async def test_over_limit(self):
        redis = FakeRedis()
        redis.store["k"] = [str(time.time())]
        mw = RateLimitMiddleware(redis)
        assert await mw._is_rate_limited("k", 1.0) is True

    @pytest.mark.asyncio
    async def test_bytes_and_invalid_timestamps(self):
        redis = FakeRedis()
        redis.store["k"] = [str(time.time()).encode(), "not-a-number", b"still-bad"]
        mw = RateLimitMiddleware(redis)
        assert await mw._is_rate_limited("k", 5.0) is False

    @pytest.mark.asyncio
    async def test_old_timestamps_are_ignored(self):
        redis = FakeRedis()
        redis.store["k"] = [str(time.time() - 1000)]
        mw = RateLimitMiddleware(redis)
        assert await mw._is_rate_limited("k", 1.0) is False

    @pytest.mark.asyncio
    async def test_ltrim_when_history_too_long(self):
        redis = FakeRedis()
        redis.store["k"] = [str(time.time()) for _ in range(101)]
        mw = RateLimitMiddleware(redis)
        assert await mw._is_rate_limited("k", 1000.0) is False
        assert redis.trims
        assert len(redis.store["k"]) <= 51

    @pytest.mark.asyncio
    async def test_exception_path(self):
        redis = FakeRedis()
        redis.raise_on_lrange = True
        mw = RateLimitMiddleware(redis)
        assert await mw._is_rate_limited("k", 1.0) is False


class TestNotifyAndCall:
    @pytest.mark.asyncio
    async def test_notify_callback(self):
        mw = RateLimitMiddleware(FakeRedis())
        cb = SimpleNamespace(answer=AsyncMock())
        update = SimpleNamespace(callback_query=cb, message=None)
        await mw._notify_user(update, {})
        cb.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_message_with_i18n(self):
        mw = RateLimitMiddleware(FakeRedis())
        msg = SimpleNamespace(answer=AsyncMock())
        update = SimpleNamespace(callback_query=None, message=msg)
        i18n = MagicMock()
        i18n.get.return_value = "custom text"
        await mw._notify_user(update, {"i18n": i18n, "lang": "en"})
        msg.answer.assert_awaited_once_with("custom text")
        i18n.get.assert_called_once_with("rate_limit_error", lang="en")

    @pytest.mark.asyncio
    async def test_notify_swallows_exception(self):
        mw = RateLimitMiddleware(FakeRedis())
        msg = SimpleNamespace(answer=AsyncMock(side_effect=Exception("boom")))
        update = SimpleNamespace(callback_query=None, message=msg)
        await mw._notify_user(update, {})  # must not raise

    @pytest.mark.asyncio
    async def test_call_without_user(self):
        mw = RateLimitMiddleware(FakeRedis())
        handler = AsyncMock(return_value="ok")
        event = SimpleNamespace(message=None, callback_query=None, inline_query=None)
        assert await mw(handler, event, {}) == "ok"
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_under_limit(self):
        mw = RateLimitMiddleware(FakeRedis())
        handler = AsyncMock(return_value="ok")
        event = _message_update()
        assert await mw(handler, event, {}) == "ok"
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_over_limit_notifies_message(self):
        redis = FakeRedis()
        redis.store["rate:1:message"] = [str(time.time())]
        mw = RateLimitMiddleware(redis)
        handler = AsyncMock(return_value="ok")
        event = _message_update()
        assert await mw(handler, event, {}) is None
        handler.assert_not_awaited()
        event.message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_over_limit_notifies_callback(self):
        redis = FakeRedis()
        redis.store["rate:5:callback_query"] = [str(time.time()) for _ in range(3)]
        mw = RateLimitMiddleware(redis)
        handler = AsyncMock(return_value="ok")
        cb = SimpleNamespace(from_user=_user(5), answer=AsyncMock(), data="x")
        event = SimpleNamespace(message=None, callback_query=cb, inline_query=None)
        assert await mw(handler, event, {}) is None
        cb.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_metric_exception_is_swallowed(self, monkeypatch):
        redis = FakeRedis()
        redis.store["rate:1:message"] = [str(time.time())]
        mw = RateLimitMiddleware(redis)
        handler = AsyncMock(return_value="ok")
        bad_metric = MagicMock()
        bad_metric.labels.side_effect = Exception("metrics down")
        monkeypatch.setattr("bot.middlewares.rate_limit_middleware.RATE_LIMIT_HITS", bad_metric)
        event = _message_update()
        assert await mw(handler, event, {}) is None
