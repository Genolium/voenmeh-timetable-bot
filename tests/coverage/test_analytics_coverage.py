"""Coverage tests for ``core.analytics``."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.analytics import FEATURE_KEY_PREFIX, generate_dynamics_chart, get_top_features, track_feature_click


def _daily_key(days_ago: int) -> str:
    from core.time_utils import now_msk_naive

    date_str = (now_msk_naive() - timedelta(days=days_ago)).strftime("%Y%m%d")
    return f"{FEATURE_KEY_PREFIX}:daily:{date_str}"


# ---------------------------------------------------------------------------
# track_feature_click
# ---------------------------------------------------------------------------


async def test_track_feature_click_without_redis():
    await track_feature_click(None, "day_nav")  # returns early


async def test_track_feature_click_sync_pipeline():
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)
    await track_feature_click(redis, "day_nav")
    pipe.hincrby.assert_called()
    pipe.expire.assert_called()
    pipe.execute.assert_awaited_once()


async def test_track_feature_click_async_pipeline():
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    redis = MagicMock()
    redis.pipeline = AsyncMock(return_value=pipe)
    await track_feature_click(redis, "day_nav")
    pipe.execute.assert_awaited_once()


async def test_track_feature_click_error():
    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=Exception("boom"))
    await track_feature_click(redis, "day_nav")  # must not raise


# ---------------------------------------------------------------------------
# get_top_features
# ---------------------------------------------------------------------------


class FakeRedis:
    def __init__(self, daily=None, total=None):
        self.daily = daily or {}
        self.total = total or {}
        self.calls = []

    async def hgetall(self, key):
        self.calls.append(key)
        if key.endswith(":total"):
            return self.total
        return self.daily.get(key, {})


async def test_get_top_features_without_redis():
    assert await get_top_features(None) == []


async def test_get_top_features_daily_aggregation():
    redis = FakeRedis(daily={_daily_key(0): {b"day_nav": 3, "teacher_search": "2"}, _daily_key(1): {"day_nav": 1}})
    result = await get_top_features(redis, days=2, limit=5)
    assert result[0] == ("day_nav", "Переход по дням недели", 4)
    assert result[1][0] == "teacher_search"


async def test_get_top_features_invalid_count_defaults_to_one():
    redis = FakeRedis(daily={_daily_key(0): {"day_nav": "not-a-number"}})
    result = await get_top_features(redis, days=1)
    assert result[0][2] == 1


async def test_get_top_features_fallback_to_total():
    redis = FakeRedis(total={b"image_export": 7})
    result = await get_top_features(redis, days=3)
    assert result == [("image_export", "Генерация расписания картинкой", 7)]


async def test_get_top_features_fallback_total_invalid_count():
    redis = FakeRedis(total={"image_export": "nope"})
    result = await get_top_features(redis, days=3)
    assert result == [("image_export", "Генерация расписания картинкой", 1)]


async def test_get_top_features_empty():
    redis = FakeRedis()
    assert await get_top_features(redis, days=3) == []


async def test_get_top_features_unknown_feature_name():
    redis = FakeRedis(daily={_daily_key(0): {"mystery": 2}})
    result = await get_top_features(redis, days=1)
    assert result == [("mystery", "mystery", 2)]


async def test_get_top_features_error():
    redis = MagicMock()
    redis.hgetall = AsyncMock(side_effect=Exception("boom"))
    assert await get_top_features(redis, days=2) == []


# ---------------------------------------------------------------------------
# generate_dynamics_chart
# ---------------------------------------------------------------------------


def test_generate_dynamics_chart_with_notifications():
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    buf = generate_dynamics_chart(dates, [1, 2, 3], [3, 4, 5], notifications_sent=[1, 1, 2])
    assert buf.read(8) == b"\x89PNG\r\n\x1a\n"


def test_generate_dynamics_chart_without_notifications():
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    buf = generate_dynamics_chart(dates, [1, 2, 3], [3, 4, 5])
    assert buf.read(8) == b"\x89PNG\r\n\x1a\n"


def test_generate_dynamics_chart_notifications_length_mismatch():
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(3)]
    buf = generate_dynamics_chart(dates, [1, 2, 3], [3, 4, 5], notifications_sent=[1])
    assert buf.read(8) == b"\x89PNG\r\n\x1a\n"


def test_generate_dynamics_chart_empty_dates():
    buf = generate_dynamics_chart([], [], [])
    assert buf.read(8) == b"\x89PNG\r\n\x1a\n"
