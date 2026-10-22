"""Coverage tests for ``core.user_data``.

Every external dependency (SQLAlchemy engine, Redis) is mocked or uses an
in-memory SQLite database, so no real infrastructure is required.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from core.db.models import Base, User
from core.user_data import UserDataManager, cached

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


async def _make_manager() -> UserDataManager:
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    manager = UserDataManager(TEST_DB_URL, redis_url=None)
    manager.engine = engine
    manager.async_session_maker.configure(bind=engine)
    manager._redis_client = _fake_redis()
    return manager


def _fake_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.scan = AsyncMock(return_value=(0, []))
    redis.lrem = AsyncMock()
    redis.lpush = AsyncMock()
    redis.ltrim = AsyncMock()
    redis.expire = AsyncMock()
    redis.lrange = AsyncMock(return_value=[])
    return redis


@pytest.fixture
async def mgr():
    manager = await _make_manager()
    try:
        yield manager
    finally:
        await manager.engine.dispose()


# ---------------------------------------------------------------------------
# __init__ / engine setup
# ---------------------------------------------------------------------------


def test_init_postgres_asyncpg_sets_connect_args():
    with (
        patch("core.user_data.create_async_engine") as ce,
        patch("core.user_data.async_sessionmaker"),
        patch("core.user_data.event") as ev,
    ):
        ce.return_value = MagicMock()
        UserDataManager("postgresql+asyncpg://u:p@h:5432/db")
        kwargs = ce.call_args.kwargs
        assert kwargs["pool_size"] == 20
        assert kwargs["connect_args"]["server_settings"]["TimeZone"]


def test_init_postgres_sync_registers_timezone_listener():
    with (
        patch("core.user_data.create_async_engine") as ce,
        patch("core.user_data.async_sessionmaker"),
        patch("core.user_data.event") as ev,
    ):
        ce.return_value = MagicMock()
        UserDataManager("postgresql://u:p@h:5432/db")
        decorator = ev.listens_for.return_value
        timezone_fn = decorator.call_args[0][0]

        # success branch
        dbapi = MagicMock()
        timezone_fn(dbapi, object())
        assert dbapi.cursor.return_value.execute.called

        # error branch (cursor creation fails)
        dbapi_bad = MagicMock()
        dbapi_bad.cursor.side_effect = Exception("boom")
        timezone_fn(dbapi_bad, object())  # must not raise


def test_init_listener_registration_error_is_swallowed():
    with (
        patch("core.user_data.create_async_engine") as ce,
        patch("core.user_data.async_sessionmaker"),
        patch("core.user_data.event") as ev,
    ):
        ce.return_value = MagicMock()
        ev.listens_for.side_effect = Exception("listener boom")
        UserDataManager("postgresql://u:p@h:5432/db")  # must not raise


def test_init_engine_kwargs_exception_is_swallowed():
    class BadUrl(str):
        def startswith(self, *args, **kwargs):  # noqa: D102
            raise Exception("bad url")

    with (
        patch("core.user_data.create_async_engine") as ce,
        patch("core.user_data.async_sessionmaker"),
        patch("core.user_data.event"),
    ):
        ce.return_value = MagicMock()
        UserDataManager(BadUrl("weird://db"))
        assert ce.called


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


async def test_init_db_sqlite_creates_tables():
    engine = create_async_engine(TEST_DB_URL)
    manager = UserDataManager(TEST_DB_URL, redis_url=None)
    manager.engine = engine
    await manager.init_db()
    await engine.dispose()


async def test_init_db_non_sqlite_skips_create_all():
    manager = UserDataManager.__new__(UserDataManager)
    manager.engine = MagicMock()
    manager.engine.url = "postgresql+asyncpg://u:p@h/db"
    with patch("core.db.migrate.assert_schema_current", new=AsyncMock()) as check:
        await manager.init_db()
    check.assert_awaited_once_with(manager.engine)


async def test_init_db_error_is_logged():
    manager = UserDataManager.__new__(UserDataManager)
    manager.engine = MagicMock()
    manager.engine.url = "sqlite:///x.db"
    manager.engine.begin.side_effect = Exception("cannot begin")
    with pytest.raises(Exception, match="cannot begin"):
        await manager.init_db()


# ---------------------------------------------------------------------------
# _get_redis_client
# ---------------------------------------------------------------------------


async def test_get_redis_client_uses_url():
    manager = UserDataManager.__new__(UserDataManager)
    manager._redis_url = "redis://localhost:6379/0"
    manager._redis_client = None
    with patch("core.user_data.Redis") as redis_cls:
        client = await manager._get_redis_client()
    redis_cls.from_url.assert_called_once_with("redis://localhost:6379/0")
    assert client is redis_cls.from_url.return_value


async def test_get_redis_client_fallback_to_config():
    manager = UserDataManager.__new__(UserDataManager)
    manager._redis_url = None
    manager._redis_client = None
    with patch("core.config.get_redis_client") as getter:
        await manager._get_redis_client()
    getter.assert_called_once()


# ---------------------------------------------------------------------------
# cached decorator
# ---------------------------------------------------------------------------


async def test_cached_decorator_hit(mgr):
    mgr._redis_client.get = AsyncMock(return_value=json.dumps("MYGROUP").encode())
    assert await mgr.get_user_group(1) == "MYGROUP"


async def test_cached_decorator_miss(mgr):
    await mgr.register_user(1, "u")
    async with mgr.async_session_maker() as session:
        await session.execute(User.__table__.update().where(User.user_id == 1).values(group="G1"))
        await session.commit()
    assert await mgr.get_user_group(1) == "G1"


async def test_cached_decorator_get_error(mgr):
    mgr._redis_client.get = AsyncMock(side_effect=Exception("redis down"))
    await mgr.register_user(2, "u")
    assert await mgr.get_user_group(2) is None


async def test_cached_decorator_setex_error(mgr):
    mgr._redis_client.setex = AsyncMock(side_effect=Exception("cannot set"))
    await mgr.register_user(3, "u")
    assert await mgr.get_user_group(3) is None


def test_get_cache_key_is_deterministic():
    manager = UserDataManager.__new__(UserDataManager)
    first = manager._get_cache_key("fn", 1, key="v")
    second = manager._get_cache_key("fn", 1, key="v")
    assert first == second
    assert first.startswith("cache:fn:")


# ---------------------------------------------------------------------------
# clear_user_cache
# ---------------------------------------------------------------------------


async def test_clear_user_cache_single(mgr):
    mgr._redis_client.delete = AsyncMock(return_value=1)
    await mgr.clear_user_cache(42)
    assert mgr._redis_client.delete.call_count == 3


async def test_clear_user_cache_full(mgr):
    mgr._redis_client.scan = AsyncMock(return_value=(0, ["cache:a", "cache:b"]))
    await mgr.clear_user_cache()
    mgr._redis_client.delete.assert_called_with("cache:a", "cache:b")


async def test_clear_user_cache_lazy_client_and_no_client():
    manager = UserDataManager.__new__(UserDataManager)
    manager._redis_client = None
    redis = _fake_redis()

    async def fake_get_redis():
        manager._redis_client = redis
        return redis

    manager._get_redis_client = fake_get_redis
    await manager.clear_user_cache(1)
    redis.delete.assert_called()

    manager2 = UserDataManager.__new__(UserDataManager)
    manager2._redis_client = None
    manager2._get_redis_client = AsyncMock(return_value=None)
    await manager2.clear_user_cache(1)  # returns early


async def test_clear_user_cache_error_is_logged(mgr):
    mgr._redis_client.delete = AsyncMock(side_effect=Exception("boom"))
    await mgr.clear_user_cache(7)  # must not raise


# ---------------------------------------------------------------------------
# register / block / group / type
# ---------------------------------------------------------------------------


async def test_register_user_new_and_existing(mgr):
    await mgr.register_user(100, "new")
    await mgr.register_user(100, "new")
    async with mgr.async_session_maker() as session:
        user = await session.get(User, 100)
        assert user is not None
        user.is_blocked = True
        user.blocked_date = datetime.now()
        await session.commit()
    await mgr.register_user(100, "new")
    async with mgr.async_session_maker() as session:
        user = await session.get(User, 100)
        assert user.is_blocked is False
        assert user.blocked_date is None


async def test_register_user_and_get(mgr):
    user = await mgr.register_user_and_get(200, "u200")
    assert user.user_id == 200
    await mgr.set_user_blocked(200, True)
    user = await mgr.register_user_and_get(200, "u200")
    assert user.is_blocked is False


async def test_set_user_blocked_unblock(mgr):
    await mgr.register_user(300, "u")
    await mgr.set_user_blocked(300, True)
    async with mgr.async_session_maker() as session:
        assert (await session.get(User, 300)).is_blocked is True
    await mgr.set_user_blocked(300, False)
    async with mgr.async_session_maker() as session:
        assert (await session.get(User, 300)).is_blocked is False
    # unknown user -> no-op
    await mgr.set_user_blocked(999, True)


async def test_set_user_group_and_type(mgr):
    await mgr.register_user(400, "u")
    await mgr.set_user_group(400, "o735b")
    assert await mgr.get_user_group(400) == "O735B"
    await mgr.set_user_group(400, None)
    assert await mgr.get_user_group(400) is None

    await mgr.set_user_type(400, "teacher")
    assert await mgr.get_user_type(400) == "teacher"


async def test_get_user_type_missing(mgr):
    assert await mgr.get_user_type(123456) is None


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


async def test_get_user_settings_existing_and_missing(mgr):
    await mgr.register_user(500, "u")
    settings = await mgr.get_user_settings(500)
    assert settings["reminder_time_minutes"] == 20
    defaults = await mgr.get_user_settings(999999)
    assert defaults["theme"] == "standard"


async def test_update_setting_invalid_and_valid(mgr):
    await mgr.register_user(600, "u")
    await mgr.update_setting(600, "nope", False)
    await mgr.update_setting(600, "evening_notify", False)
    settings = await mgr.get_user_settings(600)
    assert settings["evening_notify"] is False


async def test_set_times_and_reminder(mgr):
    await mgr.register_user(700, "u")
    await mgr.set_reminder_time(700, 45)
    await mgr.set_morning_time(700, "07:30")
    await mgr.set_evening_time(700, "19:30")
    settings = await mgr.get_user_settings(700)
    assert settings["reminder_time_minutes"] == 45
    assert settings["morning_time"] == "07:30"
    assert settings["evening_time"] == "19:30"


async def test_get_full_user_info(mgr):
    await mgr.register_user(800, "u")
    assert (await mgr.get_full_user_info(800)).user_id == 800
    assert await mgr.get_full_user_info(801) is None


async def test_get_and_set_user_theme(mgr):
    await mgr.register_user(900, "u")
    assert await mgr.get_user_theme(900) == "standard"
    await mgr.set_user_theme(900, "dark")
    assert await mgr.get_user_theme(900) == "dark"
    # invalid theme falls back to default
    await mgr.set_user_theme(900, "not-a-theme")
    assert await mgr.get_user_theme(900) == "standard"


async def test_get_user_theme_missing_user(mgr):
    assert await mgr.get_user_theme(901) == "standard"


async def test_get_and_set_user_language(mgr):
    await mgr.register_user(1000, "u")
    assert await mgr.get_user_language(1000) == "ru"
    await mgr.set_user_language(1000, "en")
    assert await mgr.get_user_language(1000) == "en"
    await mgr.set_user_language(1000, "invalid")
    assert await mgr.get_user_language(1000) == "ru"


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


async def test_statistics_queries(mgr):
    await mgr.register_user(1100, "u")
    await mgr.set_user_group(1100, "G1")
    await mgr.set_user_blocked(1100, True)

    assert await mgr.get_total_users_count() >= 1
    assert await mgr.get_new_users_count(7) >= 1
    assert await mgr.get_subscribed_users_count() >= 0
    assert await mgr.get_active_users_by_period(1) >= 1
    assert await mgr.get_blocked_users_count() >= 1
    assert await mgr.get_blocked_users_count(days=7) >= 0
    assert await mgr.get_users_without_group_count() >= 0
    assert await mgr.get_dau_previous_day() >= 0
    assert await mgr.get_unsubscribed_count() >= 0
    assert isinstance(await mgr.get_subscription_breakdown(), dict)
    assert isinstance(await mgr.get_group_distribution(), dict)
    assert isinstance(await mgr.get_all_user_ids(), list)
    assert isinstance(await mgr.get_all_users_with_groups(), list)
    assert isinstance(await mgr.get_top_groups(limit=5), list)


async def test_get_subscription_breakdown_none_row(mgr):
    manager = UserDataManager.__new__(UserDataManager)
    result = MagicMock()
    result.one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    manager.async_session_maker = _session_factory_mock(session)
    assert await manager.get_subscription_breakdown() == {}


async def test_get_daily_dynamics_with_previous_snapshot(mgr):
    redis = mgr._redis_client
    redis.get = AsyncMock(return_value=json.dumps({"total_users": 50, "dau": 100, "subscribed_total": 200}).encode())
    redis.set = AsyncMock()
    result = await mgr.get_daily_dynamics(10, 50, 100, 5)
    assert result["delta_users_str"] == "-40"
    assert result["delta_dau_pct_str"] == "-50%"
    assert result["delta_subs_str"] == "-100"
    redis.set.assert_called_once()


async def test_get_daily_dynamics_no_previous_snapshot(mgr):
    redis = mgr._redis_client
    redis.get = AsyncMock(return_value=None)
    mgr.get_dau_previous_day = AsyncMock(return_value=10)
    result = await mgr.get_daily_dynamics(100, 20, 50, 3)
    assert result["delta_users_str"] == "+3"
    assert result["delta_dau_pct_str"] == "+100%"


async def test_get_daily_dynamics_read_and_write_errors(mgr):
    redis = mgr._redis_client
    redis.get = AsyncMock(side_effect=Exception("read boom"))
    redis.set = AsyncMock(side_effect=Exception("write boom"))
    mgr.get_dau_previous_day = AsyncMock(return_value=0)
    result = await mgr.get_daily_dynamics(10, 5, 2, 1)
    assert result["delta_dau_pct_str"] == "+0%"


async def test_get_daily_dynamics_without_redis(mgr):
    mgr._get_redis_client = AsyncMock(return_value=None)
    mgr.get_dau_previous_day = AsyncMock(return_value=4)
    result = await mgr.get_daily_dynamics(10, 8, 2, 1)
    assert result["delta_dau_pct_str"] == "+100%"


async def test_get_activity_history(mgr):
    await mgr.register_user(1200, "u")
    dates, new_users, active_users = await mgr.get_activity_history(days=3)
    assert len(dates) == 3
    assert len(new_users) == 3
    assert len(active_users) == 3


async def test_gather_stats(mgr):
    await mgr.register_user(1300, "u")
    await mgr.set_user_group(1300, "G2")
    stats = await mgr.gather_stats()
    assert len(stats) == 9


# ---------------------------------------------------------------------------
# broadcast helpers
# ---------------------------------------------------------------------------


async def test_get_users_for_notifications(mgr):
    async with mgr.async_session_maker() as session:
        session.add(
            User(
                user_id=1400,
                username="u",
                group="G1",
                evening_notify=True,
                morning_summary=True,
                lesson_reminders=True,
                evening_time="20:00",
                morning_time="08:00",
                user_type="student",
            )
        )
        await session.commit()
    assert await mgr.get_users_for_evening_notify(20)
    assert await mgr.get_users_for_morning_summary(8)
    assert await mgr.get_users_for_lesson_reminders()


async def test_get_users_for_lesson_reminders_none_minutes(mgr):
    manager = UserDataManager.__new__(UserDataManager)
    manager._redis_client = _fake_redis()
    result = MagicMock()
    result.all.return_value = [(1, "G", None, "ru"), (2, "G2", 15, "en")]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    manager.async_session_maker = _session_factory_mock(session)
    rows = await manager.get_users_for_lesson_reminders()
    assert rows[0][2] == 20
    assert rows[1][2] == 15


async def test_get_admin_users(mgr):
    assert isinstance(await mgr.get_admin_users(), list)


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


async def test_add_to_history_success_and_error(mgr):
    redis = mgr._redis_client
    await mgr.add_to_history(1, "group", "G1")
    redis.lpush.assert_called_with("history:1:group", "G1")
    redis.lrem.assert_called_with("history:1:group", 0, "G1")
    redis.ltrim.assert_called_with("history:1:group", 0, 9)
    redis.expire.assert_called_with("history:1:group", 86400 * 7)

    redis.lpush = AsyncMock(side_effect=Exception("boom"))
    await mgr.add_to_history(1, "group", "G2")  # must not raise


async def test_add_to_history_without_redis(mgr):
    mgr._get_redis_client = AsyncMock(return_value=None)
    await mgr.add_to_history(1, "group", "G1")


async def test_get_history_success_error_and_no_redis(mgr):
    redis = mgr._redis_client
    redis.lrange = AsyncMock(return_value=[b"G1", "G2"])
    assert await mgr.get_history(1, "group") == ["G1", "G2"]

    redis.lrange = AsyncMock(side_effect=Exception("boom"))
    assert await mgr.get_history(1, "group") == []

    mgr._get_redis_client = AsyncMock(return_value=None)
    assert await mgr.get_history(1, "group") == []


async def test_clear_history_variants(mgr):
    redis = mgr._redis_client
    await mgr.clear_history(1, "group")
    redis.delete.assert_called_with("history:1:group")

    redis.delete.reset_mock()
    await mgr.clear_history(1)
    assert redis.delete.call_count == 3

    redis.delete = AsyncMock(side_effect=Exception("boom"))
    await mgr.clear_history(1)  # must not raise

    mgr._get_redis_client = AsyncMock(return_value=None)
    await mgr.clear_history(1)


def _session_factory_mock(session: AsyncMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=cm)
    return factory
