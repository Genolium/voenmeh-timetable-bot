"""Branch-coverage tests for the second half of ``core/*``.

Covered modules:

* ``core.parser``              -- retry loop, outdated-year guard, index building
* ``core.subscription_filter`` -- DB filter fallbacks and cache invalidation
* ``core.telemetry``           -- shutdown without a tracer provider
* ``core.transliteration``     -- lessons with missing/non-string fields
* ``core.user_data``           -- cache invalidation and daily statistics
* ``core.weather_api``         -- outdated forecast cache

No network, no Docker, no real Redis/PostgreSQL: every collaborator is faked.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from core import parser
from core import weather_api as weather_api_module
from core.db.models import Base
from core.subscription_filter import SubscriptionFilterManager, is_user_subscribed
from core.telemetry import TelemetryHandles, shutdown_telemetry
from core.transliteration import transliterate_schedule_data
from core.user_data import UserDataManager
from core.weather_api import WeatherAPI

# ---------------------------------------------------------------------------
# core.parser
# ---------------------------------------------------------------------------


class _Resp:
    """Fake aiohttp response usable as an async context manager."""

    def __init__(self, status=200, content=b"", headers=None, raise_exc=None):
        self.status = status
        self._content = content
        self.headers = headers or {}
        self._raise = raise_exc

    async def read(self):
        return self._content

    def raise_for_status(self):
        if self._raise:
            raise self._raise


class _Ctx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp

    async def __aexit__(self, *args):
        return False


class _XmlSession:
    """Session double that serves the prepared XML documents one by one."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.closed = False

    def get(self, url, **kwargs):
        return _Ctx(self._responses.pop(0))

    async def close(self):
        self.closed = True


class _SuppressingCtx:
    """Context manager whose ``__aexit__`` swallows the exception."""

    async def __aenter__(self):
        return _FailingResp()

    async def __aexit__(self, exc_type, exc, tb):
        # Returning True suppresses the exception, so the retry loop never
        # sees it and simply re-evaluates its condition.
        return True


class _FailingResp:
    status = 200
    headers: dict = {}

    def raise_for_status(self):
        raise RuntimeError("network down")

    async def read(self):  # pragma: no cover - never reached
        return b""


class _SuppressingSession:
    def get(self, url, **kwargs):
        return _SuppressingCtx()


DUPLICATE_LESSON_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<Timetable>"
    '<Period StartYear="2026" StartMonth="9" StartDay="1" />'
    '<Group Number="G1">'
    "<Days>"
    '<Day Title="Понедельник">'
    "<GroupLessons>"
    "<Lesson>"
    "<Time>09:00</Time>"
    "<Discipline>лек Математика</Discipline>"
    "<Classroom>101</Classroom>"
    "<WeekCode>1</WeekCode>"
    "<Lecturers><Lecturer><ShortName>Иванов</ShortName></Lecturer></Lecturers>"
    "</Lesson>"
    "<Lesson>"
    "<Time>09:00</Time>"
    "<Discipline>лек Математика</Discipline>"
    "<Classroom>101</Classroom>"
    "<WeekCode>1</WeekCode>"
    "<Lecturers><Lecturer><ShortName>Иванов</ShortName></Lecturer></Lecturers>"
    "</Lesson>"
    "</GroupLessons>"
    "</Day>"
    '<Day Title="Вторник">'
    "<GroupLessons>"
    "<Lesson>"
    "<Time>10:00</Time>"
    "<Discipline>пр Физика</Discipline>"
    "<Classroom>202</Classroom>"
    "<WeekCode>2</WeekCode>"
    "<Lecturers><Lecturer><ShortName>Петров</ShortName></Lecturer></Lecturers>"
    "</Lesson>"
    "</GroupLessons>"
    "</Day>"
    "</Days>"
    "</Group>"
    "</Timetable>"
).encode("utf-8")

SIMPLE_XML = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<Timetable>"
    '<Period StartYear="2026" StartMonth="9" StartDay="1" />'
    '<Group Number="G1"><Days/></Group>'
    "</Timetable>"
).encode("utf-8")


@pytest.fixture(autouse=True)
def _reset_parser_globals():
    parser._LAST_ETAG = None
    parser._LAST_MODIFIED = None
    yield
    parser._LAST_ETAG = None
    parser._LAST_MODIFIED = None


async def test_legacy_xml_leaves_retry_loop_when_exceptions_are_suppressed():
    """The retry loop is left through its condition when nothing propagates.

    ``aiohttp``-like context managers may swallow the exception raised inside
    the block.  In that case the retry loop simply re-evaluates its condition
    instead of re-raising, and it is left once the attempt budget is used up.
    """
    session = _SuppressingSession()

    result = await parser._fetch_and_parse_legacy_xml(session=session)

    assert result is None


async def test_legacy_xml_skips_outdated_year_guard_for_other_urls(monkeypatch):
    """The outdated-year guard only applies to the TimetableGroup50 feed."""
    monkeypatch.setattr(parser, "API_URL", "https://example.com/other-timetable.xml")
    session = _XmlSession([_Resp(200, content=SIMPLE_XML)])

    result = await parser._fetch_and_parse_legacy_xml(session=session)

    assert result is not None
    assert "G1" in result
    assert result["__metadata__"]["period"]["StartYear"] == "2026"


async def test_legacy_xml_duplicate_lesson_of_the_same_group():
    """A repeated lesson of the same group must not duplicate the teacher index."""
    session = _XmlSession([_Resp(200, content=DUPLICATE_LESSON_XML)])

    result = await parser._fetch_and_parse_legacy_xml(session=session)

    assert result is not None

    # The teacher index keeps a single entry with the group listed once.
    teacher_lessons = result["__teachers_index__"]["Иванов"]
    assert len(teacher_lessons) == 1
    assert teacher_lessons[0]["groups"] == ["G1"]

    # Tuesday only has an even-week lesson, so the odd bucket stays empty.
    assert "Вторник" not in result["G1"]["odd"]
    assert result["G1"]["even"]["Вторник"]


# ---------------------------------------------------------------------------
# core.subscription_filter
# ---------------------------------------------------------------------------


async def test_is_user_subscribed_when_db_filter_is_disabled():
    """A disabled DB filter falls through to the legacy channel check."""
    bot = AsyncMock()

    with patch("core.subscription_filter.SubscriptionFilterManager") as manager_cls:
        manager = manager_cls.return_value
        manager.is_filter_enabled = AsyncMock(return_value=False)

        assert await is_user_subscribed(1, bot, MagicMock(), None) is True

    bot.get_chat_member.assert_not_awaited()


async def test_is_user_subscribed_when_no_required_channels():
    """An enabled filter without required channels lets everybody through."""
    with patch("core.subscription_filter.SubscriptionFilterManager") as manager_cls:
        manager = manager_cls.return_value
        manager.is_filter_enabled = AsyncMock(return_value=True)
        manager.get_required_channels = AsyncMock(return_value=[])

        assert await is_user_subscribed(1, AsyncMock(), MagicMock(), None) is True


async def test_check_user_subscription_without_redis_reports_missing_channels():
    """Without Redis there is no cache entry to invalidate."""
    manager = SubscriptionFilterManager(None, None)
    bot = AsyncMock()
    member = MagicMock()
    member.status = "left"
    bot.get_chat_member.return_value = member

    subscribed, missing = await manager.check_user_subscription(bot, 1, [{"channel_id": "@news"}])

    assert subscribed is False
    assert missing == [{"channel_id": "@news"}]


# ---------------------------------------------------------------------------
# core.telemetry
# ---------------------------------------------------------------------------


def test_shutdown_telemetry_without_tracer_provider():
    """Shutting down handles without a tracer provider must be a no-op."""
    handles = TelemetryHandles(
        tracer_provider=None,
        meter_provider=None,
        logger_provider=None,
        log_handler=None,
    )

    shutdown_telemetry(handles)  # must not raise


# ---------------------------------------------------------------------------
# core.transliteration
# ---------------------------------------------------------------------------


def test_transliterate_schedule_data_keeps_missing_and_non_string_fields():
    """Only string fields are transliterated, the rest is copied verbatim."""
    schedule = {
        "Понедельник": [
            {"subject": "Математика", "teacher": 42},
        ]
    }

    result = transliterate_schedule_data(schedule, lang="en")

    (day_name,) = result.keys()
    lesson = result[day_name][0]
    assert lesson["subject"].isascii()
    assert lesson["subject"] != "Математика"
    assert lesson["teacher"] == 42
    assert "title" not in lesson


# ---------------------------------------------------------------------------
# core.user_data
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def user_data_manager():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    manager = UserDataManager(TEST_DB_URL, redis_url=None)
    manager.engine = engine
    manager.async_session_maker.configure(bind=engine)
    try:
        yield manager
    finally:
        await engine.dispose()


async def test_clear_user_cache_when_no_key_was_deleted(user_data_manager):
    """Nothing was removed -> the debug log is skipped for every key."""
    redis = AsyncMock()
    redis.delete = AsyncMock(return_value=0)
    user_data_manager._redis_client = redis

    await user_data_manager.clear_user_cache(user_id=42)

    assert redis.delete.await_count == 3


async def test_clear_user_cache_full_scan_continues_until_cursor_is_zero(user_data_manager):
    """The SCAN loop repeats while Redis reports a non-zero cursor."""
    redis = AsyncMock()
    scans = {"count": 0}

    async def _scan(cursor=0, match=None, **kwargs):
        scans["count"] += 1
        if scans["count"] == 1:
            return 5, ["cache:some-key"]
        return 0, []

    redis.scan = AsyncMock(side_effect=_scan)
    redis.delete = AsyncMock(return_value=1)
    user_data_manager._redis_client = redis

    await user_data_manager.clear_user_cache()

    assert scans["count"] > 1
    redis.delete.assert_awaited_with("cache:some-key")


async def test_register_user_and_get_keeps_unblocked_user(user_data_manager):
    """An existing, non-blocked user is returned without touching the block flags."""
    await user_data_manager.register_user(7, "tester")

    user = await user_data_manager.register_user_and_get(7, "tester")

    assert user is not None
    assert user.user_id == 7
    assert not user.is_blocked


async def test_get_daily_dynamics_without_yesterday_dau(user_data_manager):
    """A yesterday snapshot without DAU leaves the DAU delta untouched."""
    redis = AsyncMock()
    snapshot = json.dumps({"total_users": 10, "dau": 0, "subscribed_total": 2}).encode("utf-8")
    redis.get = AsyncMock(return_value=snapshot)
    user_data_manager._redis_client = redis

    result = await user_data_manager.get_daily_dynamics(
        total_users=12,
        dau=5,
        subscribed_total=3,
        new_users_day=1,
    )

    assert result["delta_users_str"] == "+2"
    assert result["delta_subs_str"] == "+1"
    assert result["delta_dau_pct_str"] == "+0%"


# ---------------------------------------------------------------------------
# core.weather_api
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_weather_cache():
    saved = dict(WeatherAPI._cache)
    WeatherAPI._cache.clear()
    yield WeatherAPI._cache
    WeatherAPI._cache.clear()
    WeatherAPI._cache.update(saved)


async def test_get_forecast_ignores_outdated_cache(monkeypatch, clean_weather_cache):
    """An expired cache entry is refreshed instead of being returned."""
    api = WeatherAPI("api-key", "2643743")
    target = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    cache_key = f"{target.date().isoformat()}_{((target.hour // 3) * 3):02d}h_ru"
    clean_weather_cache[cache_key] = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5),
        "data": {"cached": True},
    }

    class _NoNetwork:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(weather_api_module.aiohttp, "ClientSession", _NoNetwork)

    result = await api.get_forecast_for_time(target)

    assert result is None
