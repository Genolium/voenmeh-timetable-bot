"""Coverage tests for ``core.admin_reports``.

The bot, user data manager and DB backup are mocked.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.admin_reports import AdminReportsGenerator, send_daily_reports, send_monthly_reports, send_weekly_reports

STATS = (
    1000,
    500,
    700,
    900,
    800,
    50,
    {"evening": 400, "morning": 300, "reminders": 200},
    [("О735Б", 150)],
    {"1 студент": 5},
)


@pytest.fixture
def bot():
    mock = MagicMock()
    mock.send_message = AsyncMock()
    mock.send_document = AsyncMock()
    return mock


@pytest.fixture
def user_data_manager():
    manager = AsyncMock()
    manager.gather_stats = AsyncMock(return_value=STATS)
    manager.get_new_users_count = AsyncMock(return_value=10)
    manager.get_active_users_by_period = AsyncMock(return_value=500)
    manager.get_blocked_users_count = AsyncMock(return_value=2)
    manager.get_users_without_group_count = AsyncMock(return_value=3)
    manager.get_daily_dynamics = AsyncMock(
        return_value={"delta_users_str": "+10", "delta_dau_pct_str": "+5%", "delta_subs_str": "+1"}
    )
    manager._get_redis_client = AsyncMock(return_value=MagicMock())
    manager.get_admin_users = AsyncMock(return_value=[1, 2])
    return manager


@pytest.fixture
def generator(bot, user_data_manager):
    return AdminReportsGenerator(bot, user_data_manager)


# ---------------------------------------------------------------------------
# generate_daily_report
# ---------------------------------------------------------------------------


async def test_generate_daily_report_success(generator, user_data_manager):
    with patch("core.admin_reports.get_top_features", AsyncMock(return_value=[("day_nav", "Переход", 5)])):
        report = await generator.generate_daily_report()
    assert "Ежедневный отчёт" in report
    assert "Переход" in report
    assert "Заблокировали бота: <b>2</b>" in report


async def test_generate_daily_report_non_dict_dynamics(generator, user_data_manager):
    user_data_manager.get_daily_dynamics = AsyncMock(return_value="not-a-dict")
    with patch("core.admin_reports.get_top_features", AsyncMock(return_value=[])):
        report = await generator.generate_daily_report()
    assert "Ежедневный отчёт" in report


async def test_generate_daily_report_top_features_error(generator, user_data_manager):
    user_data_manager._get_redis_client = AsyncMock(side_effect=Exception("redis down"))
    report = await generator.generate_daily_report()
    assert "Ежедневный отчёт" in report


async def test_generate_daily_report_error(generator, user_data_manager):
    user_data_manager.gather_stats = AsyncMock(side_effect=Exception("db down"))
    report = await generator.generate_daily_report()
    assert "❌ Ошибка" in report


async def test_generate_daily_report_minimal_manager(bot):
    class MinimalManager:
        async def gather_stats(self):
            return STATS

        async def get_new_users_count(self, days):
            return 5

        async def _get_redis_client(self):
            return None

    report = await AdminReportsGenerator(bot, MinimalManager()).generate_daily_report()
    assert "Ежедневный отчёт" in report


# ---------------------------------------------------------------------------
# generate_weekly_report / generate_monthly_report
# ---------------------------------------------------------------------------


async def test_generate_weekly_report_success(generator):
    report = await generator.generate_weekly_report()
    assert "Еженедельный отчёт" in report


async def test_generate_weekly_report_error(generator, user_data_manager):
    user_data_manager.gather_stats = AsyncMock(side_effect=Exception("db down"))
    report = await generator.generate_weekly_report()
    assert "❌ Ошибка" in report


async def test_generate_monthly_report_success(generator):
    report = await generator.generate_monthly_report()
    assert "Ежемесячный отчёт" in report


async def test_generate_monthly_report_error(generator, user_data_manager):
    user_data_manager.gather_stats = AsyncMock(side_effect=Exception("db down"))
    report = await generator.generate_monthly_report()
    assert "❌ Ошибка" in report


async def test_prometheus_metrics_helpers(generator):
    assert "tasks_sent" in await generator._get_prometheus_metrics()
    assert "tasks_sent_total" in await generator._get_prometheus_metrics_weekly()
    assert "avg_daily_users" in await generator._get_prometheus_metrics_monthly()


async def test_prometheus_http_queries_and_fallbacks(generator, monkeypatch):
    from types import SimpleNamespace

    from core import admin_reports

    class _FakeResp:
        def __init__(self, status=200, payload=None):
            self.status = status
            self._payload = payload or {}

        async def json(self):
            return self._payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _FakeSession:
        def __init__(self, mapping):
            self.mapping = mapping

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, params=None, timeout=None):
            q = (params or {}).get("query", "")
            for substr, resp in self.mapping:
                if substr in q:
                    return resp
            return _FakeResp(200, {"status": "success", "data": {"result": []}})

    monkeypatch.setenv("PROMETHEUS_URL", "http://prom:9090/")
    mapping = [
        ("bot_tasks_sent_total", _FakeResp(200, {"status": "success", "data": {"result": [{"value": [0, "42"]}]}})),
        ("bot_errors_total", _FakeResp(200, {"status": "success", "data": {"result": [{"value": [0, "3"]}]}})),
        ("bot_retries_total", _FakeResp(200, {"status": "success", "data": {"result": [{"value": [0, "7"]}]}})),
        ("bot_parser_operations_total", _FakeResp(200, {"status": "success", "data": {"result": [{"value": [0, "4"]}]}})),
        (
            "bot_last_schedule_update_timestamp",
            _FakeResp(200, {"status": "success", "data": {"result": [{"value": [0, "1700000000"]}]}}),
        ),
        (
            "bot_user_activity_hourly_total",
            _FakeResp(200, {"status": "success", "data": {"result": [{"metric": {"hour_of_day": "9"}, "value": [0, "15"]}]}}),
        ),
        ("bot_user_activity_daily_total", _FakeResp(200, {"status": "success", "data": {"result": [{"value": [0, "12.4"]}]}})),
    ]
    monkeypatch.setattr(admin_reports.aiohttp, "ClientSession", lambda: _FakeSession(mapping))

    with patch("core.admin_reports.get_top_features", AsyncMock(return_value=[("day_nav", "Переход", 9)])):
        daily = await generator._get_prometheus_metrics()
        weekly = await generator._get_prometheus_metrics_weekly()
        monthly = await generator._get_prometheus_metrics_monthly()

    assert daily["tasks_sent"] == 42
    assert daily["last_update"] != "Нет данных"
    assert weekly["peak_activity"] == "09:00-10:00"
    assert monthly["avg_daily_users"] == 12
    assert monthly["top_features"] == ["Переход"]

    # Local helper branches + invalid Prometheus responses
    fake_metric = MagicMock()
    fake_metric.collect.return_value = [
        SimpleNamespace(
            samples=[
                SimpleNamespace(name="x_created", labels={}, value=1.0),
                SimpleNamespace(name="x_total", labels={"status": "failed"}, value=2.0),
                SimpleNamespace(name="x_total", labels={"status": "success"}, value=5.0),
            ]
        )
    ]
    assert admin_reports._local_counter_sum(fake_metric, ("status", "success")) == 5

    fake_ts = MagicMock()
    fake_ts.collect.return_value = [SimpleNamespace(samples=[SimpleNamespace(name="ts", labels={}, value=1700000000.0)])]
    monkeypatch.setattr(admin_reports, "LAST_SCHEDULE_UPDATE_TS", fake_ts)
    assert admin_reports._local_last_update_str() != "Нет данных"

    assert generator._extract_scalar([]) is None
    assert generator._extract_scalar([{"value": ["only_one"]}]) is None
    assert generator._extract_scalar([{"value": [0, "not-a-float"]}]) is None
    assert generator._extract_scalar([{"value": [0, "NaN"]}]) is None

    # Non-200 / error status / bad hour / exception fallback
    bad_mapping = [
        ("bot_tasks_sent_total", _FakeResp(500, {})),
        ("bot_errors_total", _FakeResp(200, {"status": "error"})),
        (
            "bot_user_activity_hourly_total",
            _FakeResp(200, {"status": "success", "data": {"result": [{"metric": {"hour_of_day": "bad"}}]}}),
        ),
    ]
    monkeypatch.setattr(admin_reports.aiohttp, "ClientSession", lambda: _FakeSession(bad_mapping))
    weekly_bad = await generator._get_prometheus_metrics_weekly()
    assert weekly_bad["peak_activity"] == "Нет данных"

    no_hour_mapping = [
        ("bot_user_activity_hourly_total", _FakeResp(200, {"status": "success", "data": {"result": [{"metric": {}}]}})),
    ]
    monkeypatch.setattr(admin_reports.aiohttp, "ClientSession", lambda: _FakeSession(no_hour_mapping))
    assert (await generator._get_prometheus_metrics_weekly())["peak_activity"] == "Нет данных"

    monkeypatch.setattr(admin_reports.aiohttp, "ClientSession", lambda: _FakeSession([]))
    assert (await generator._get_prometheus_metrics_weekly())["peak_activity"] == "Нет данных"

    monkeypatch.setattr(admin_reports.aiohttp, "ClientSession", MagicMock(side_effect=RuntimeError("prom down")))
    generator.user_data_manager._get_redis_client = AsyncMock(side_effect=RuntimeError("redis down"))
    monthly_err = await generator._get_prometheus_metrics_monthly()
    assert monthly_err["top_features"] == ["Нет данных"]

    # Enrich avg_daily_users from active_users_month when Prometheus is unset
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    report_m = await generator.generate_monthly_report()
    assert "Среднесуточная активность: <b>17</b>" in report_m


# ---------------------------------------------------------------------------
# formatters
# ---------------------------------------------------------------------------


def test_format_daily_report_empty_sections(generator):
    report = generator._format_daily_report(
        "01.01.2026",
        0,
        0,
        0,
        0,
        0,
        0,
        {},
        [],
        {},
        {},
        new_users_day=0,
        dynamics=None,
        blocked_today=0,
        users_without_group=0,
        top_features=None,
    )
    assert "Нет данных" in report
    assert "Нет данных за сутки" in report


def test_format_daily_report_negative_values(generator):
    report = generator._format_daily_report(
        "01.01.2026",
        100,
        10,
        20,
        30,
        5,
        1,
        {"evening": 1},
        [("G", 1)],
        {"cat": 1},
        {},
        new_users_day=1,
        dynamics={"delta_users_str": "-5", "delta_dau_pct_str": "-2%", "delta_subs_str": "-1"},
        blocked_today=10,
        users_without_group=0,
        top_features=[("a", "A", 1)],
    )
    assert "Чистый прирост (Net Growth): <b>-9</b>" in report


def test_format_weekly_report_empty_sections(generator):
    report = generator._format_weekly_report(0, 0, 0, 0, [], {})
    assert "Нет данных" in report


def test_format_monthly_report_empty_sections(generator):
    report = generator._format_monthly_report(0, 0, 0, 0, [], {})
    assert "Нет данных" in report


# ---------------------------------------------------------------------------
# send_daily_reports
# ---------------------------------------------------------------------------


async def test_send_daily_reports_no_admins(bot, user_data_manager):
    user_data_manager.get_admin_users = AsyncMock(return_value=[])
    await send_daily_reports(bot, user_data_manager)
    bot.send_message.assert_not_awaited()


async def test_send_daily_reports_with_backup(bot, user_data_manager, tmp_path):
    backup = tmp_path / "db_backup.sql"
    backup.write_text("SELECT 1;")
    with patch("core.admin_reports.create_db_backup", AsyncMock(return_value=backup)):
        await send_daily_reports(bot, user_data_manager)
    assert bot.send_message.await_count == 2
    assert bot.send_document.await_count == 2
    assert not backup.exists()


async def test_send_daily_reports_backup_error(bot, user_data_manager):
    with patch("core.admin_reports.create_db_backup", AsyncMock(side_effect=Exception("pg_dump down"))):
        await send_daily_reports(bot, user_data_manager)
    assert bot.send_message.await_count == 2
    bot.send_document.assert_not_awaited()


async def test_send_daily_reports_message_and_document_errors(bot, user_data_manager, tmp_path):
    backup = tmp_path / "db_backup.sql"
    backup.write_text("SELECT 1;")
    bot.send_message.side_effect = Exception("send down")
    bot.send_document.side_effect = Exception("doc down")
    with patch("core.admin_reports.create_db_backup", AsyncMock(return_value=backup)):
        await send_daily_reports(bot, user_data_manager)
    assert bot.send_document.await_count == 2


async def test_send_daily_reports_unlink_error(bot, user_data_manager, tmp_path, monkeypatch):
    backup = tmp_path / "db_backup.sql"
    backup.write_text("SELECT 1;")

    def boom(self, *args, **kwargs):
        raise OSError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", boom)
    with patch("core.admin_reports.create_db_backup", AsyncMock(return_value=backup)):
        await send_daily_reports(bot, user_data_manager)


async def test_send_daily_reports_outer_exception(bot, user_data_manager):
    with patch("core.admin_reports.AdminReportsGenerator") as generator_cls:
        instance = MagicMock()
        instance.generate_daily_report = AsyncMock(side_effect=Exception("boom"))
        generator_cls.return_value = instance
        await send_daily_reports(bot, user_data_manager)  # must not raise


# ---------------------------------------------------------------------------
# send_weekly_reports / send_monthly_reports
# ---------------------------------------------------------------------------


async def test_send_weekly_reports_success(bot, user_data_manager):
    await send_weekly_reports(bot, user_data_manager)
    assert bot.send_message.await_count == 2


async def test_send_weekly_reports_send_error(bot, user_data_manager):
    bot.send_message.side_effect = Exception("send down")
    await send_weekly_reports(bot, user_data_manager)
    assert bot.send_message.await_count == 2


async def test_send_weekly_reports_outer_exception(bot, user_data_manager):
    with patch("core.admin_reports.AdminReportsGenerator") as generator_cls:
        instance = MagicMock()
        instance.generate_weekly_report = AsyncMock(side_effect=Exception("boom"))
        generator_cls.return_value = instance
        await send_weekly_reports(bot, user_data_manager)


async def test_send_monthly_reports_success(bot, user_data_manager):
    await send_monthly_reports(bot, user_data_manager)
    assert bot.send_message.await_count == 2


async def test_send_monthly_reports_send_error(bot, user_data_manager):
    bot.send_message.side_effect = Exception("send down")
    await send_monthly_reports(bot, user_data_manager)
    assert bot.send_message.await_count == 2


async def test_send_monthly_reports_outer_exception(bot, user_data_manager):
    with patch("core.admin_reports.AdminReportsGenerator") as generator_cls:
        instance = MagicMock()
        instance.generate_monthly_report = AsyncMock(side_effect=Exception("boom"))
        generator_cls.return_value = instance
        await send_monthly_reports(bot, user_data_manager)


def test_local_last_update_str_empty():
    from core.admin_reports import _local_last_update_str

    empty_family = MagicMock()
    empty_family.samples = []
    with patch("core.admin_reports.LAST_SCHEDULE_UPDATE_TS") as metric:
        metric.collect.return_value = [empty_family]
        assert _local_last_update_str() == "Нет данных"
