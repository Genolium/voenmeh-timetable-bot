"""Регрессионные тесты на исправления безопасности, гонок, утечек ресурсов и багов аудита."""

from __future__ import annotations

import asyncio
import gzip
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe, SendMessage
from aiohttp.test_utils import make_mocked_request

from bot.handlers.inline_handlers import parse_day_from_query
from bot.middlewares.custom_emoji_middleware import _is_html_parse_mode
from bot.middlewares.network_retry_middleware import NetworkRetryRequestMiddleware
from bot.text_formatters import format_full_week_text, format_schedule_text
from core.alert_webhook import create_alert_app, format_alertmanager_message
from core.db.backups import (
    BackupError,
    DatabaseConnectionInfo,
    _prepare_connection_env,
    _sanitize_dump_file,
    create_db_backup,
    maybe_decompress_gzip,
)
from core.ics_exporter import _escape_ics_text, generate_schedule_ics
from core.image_cache_manager import ImageCacheManager, sanitize_cache_key, sanitize_group_key
from core.semester_calendar import SemesterInfo
from core.summary_advisor import analyze_lessons_window, build_weather_summary_block
from core.weather_api import WeatherAPI, _scrub_appid


def test_backup_rejects_psql_shell_meta_commands(tmp_path: Path):
    sql = tmp_path / "evil.sql"
    sql.write_text("SELECT 1;\n\\! id\n", encoding="utf-8")
    with pytest.raises(BackupError, match="disallowed psql meta-command"):
        _sanitize_dump_file(sql)


def test_backup_allows_backslash_dot_inside_copy_block(tmp_path: Path):
    sql = tmp_path / "copy.sql"
    sql.write_text(
        "\ufeffCOPY public.users (id, name) FROM stdin;\n1\t\\! harmless data\n\\.\nSELECT 1;\n",
        encoding="utf-8",
    )
    _sanitize_dump_file(sql)
    assert "COPY public.users" in sql.read_text(encoding="utf-8")


def test_backup_gzip_bomb_limit_and_corrupt_archive(tmp_path: Path):
    gz_path = tmp_path / "bomb.sql.gz"
    with gzip.open(gz_path, "wb") as f:
        f.write(b"A" * 2048)

    with pytest.raises(BackupError, match="maximum allowed size"):
        maybe_decompress_gzip(gz_path, max_bytes=1024)
    assert not (tmp_path / "bomb.sql").exists()

    bad_gz = tmp_path / "corrupt.sql.gz"
    bad_gz.write_bytes(b"not-a-gzip")
    with pytest.raises(BackupError, match="Failed to decompress"):
        maybe_decompress_gzip(bad_gz)
    assert not (tmp_path / "corrupt.sql").exists()


@pytest.mark.asyncio
async def test_backup_rejects_invalid_prefix_and_minimizes_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/test_db")
    monkeypatch.setenv("BOT_TOKEN", "SUPER_SECRET_BOT_TOKEN")

    with pytest.raises(BackupError, match="Invalid backup filename prefix"):
        await create_db_backup(output_dir=tmp_path, filename_prefix="../escape")

    info = DatabaseConnectionInfo.from_url("postgresql://user:secret@localhost:5432/test_db")
    assert "secret" not in repr(info)
    env = _prepare_connection_env(info)
    assert env["PGPASSWORD"] == "secret"
    assert "BOT_TOKEN" not in env


@pytest.mark.asyncio
async def test_alert_webhook_non_ascii_auth_and_html_escape(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_API_KEY", "secret-key")
    bot = MagicMock()
    bot.send_message = AsyncMock()
    app = create_alert_app(bot, [123])

    handler = next(r.handler for r in app.router.routes() if r.resource.canonical == "/alerts")

    # Non-ASCII Bearer token must return 401 (never TypeError 500)
    req_bad = make_mocked_request("POST", "/alerts", headers={"Authorization": "Bearer секрет"}, app=app)
    resp_bad = await handler(req_bad)
    assert resp_bad.status == 401

    # Valid auth with <script> / PromQL < threshold gets HTML-escaped
    req_ok = make_mocked_request("POST", "/alerts", headers={"Authorization": "Bearer secret-key"}, app=app)
    req_ok.json = AsyncMock(
        return_value={
            "status": "firing",
            "alerts": [
                {"labels": {"alertname": "LowRate"}, "annotations": {"description": "rate < 0.5 & <b>x</b>"}},
                "malformed_non_dict_entry",
            ],
        }
    )
    resp_ok = await handler(req_ok)
    assert resp_ok.status == 200
    sent_text = bot.send_message.call_args[0][1]
    assert "&lt; 0.5 &amp; &lt;b&gt;x&lt;/b&gt;" in sent_text
    assert format_alertmanager_message({"status": None, "alerts": "not-a-list"}).startswith("ALERTMANAGER: UNKNOWN (0")


def test_weather_scrub_appid_and_clamped_start_hour():
    scrubbed = _scrub_appid(
        "401 for url 'https://api.openweathermap.org/data/2.5/forecast?id=1&appid=MY_KEY_123&units=metric'"
    )
    assert "MY_KEY_123" not in scrubbed
    assert "appid=***" in scrubbed


@pytest.mark.asyncio
async def test_weather_day_window_high_start_hour_no_value_error(monkeypatch):
    api = WeatherAPI(api_key="k", city_id="1")

    async def fake_forecast(_dt, lang="ru"):
        return {
            "temperature": 10,
            "feels_like": 10,
            "description": "ясно",
            "emoji": "☀️",
            "wind_speed": 2,
            "pop": 0,
            "precip_kind": None,
        }

    monkeypatch.setattr(api, "get_forecast_for_time", fake_forecast)
    res = await api.get_day_weather_window(date(2026, 11, 25), start_hour=25, end_hour=26)
    assert res is not None
    assert res["temperature"] == 10


def test_ics_exporter_uses_teachers_groups_utc_dtstamp_and_strips_cr():
    assert "\\n" in _escape_ics_text("a\rb")
    assert "\r" not in _escape_ics_text("a\rb")

    info = SemesterInfo(semester="fall", semester_start=date(2026, 9, 1), week_number=13, week_key="odd")
    student_weeks = {
        "odd": {
            "Понедельник": [
                {
                    "subject": "Алгебра",
                    "time": "09:00-10:30",
                    "teachers": "Иванов И.И.",
                    "room": "101",
                }
            ]
        },
        "even": {},
    }
    ics = generate_schedule_ics("О735Б", student_weeks, info, reference_date=date(2026, 11, 23))
    assert ics is not None
    unfolded = ics.decode("utf-8").replace("\r\n ", "")
    assert "Преподаватель: Иванов И.И." in unfolded

    teacher_weeks = {
        "odd": {
            "Понедельник": [
                {
                    "subject": "Сети",
                    "time": "10:45-12:15",
                    "groups": ["О734Б", "О735Б"],
                }
            ]
        },
        "even": {},
    }
    ics_t = generate_schedule_ics("Скобеев", teacher_weeks, info, reference_date=date(2026, 11, 23), is_teacher=True)
    assert ics_t is not None
    assert "Группа: О734Б\\, О735Б" in ics_t.decode("utf-8").replace("\r\n ", "")


def test_image_cache_path_traversal_blocked(tmp_path: Path):
    assert sanitize_group_key("О735Б/1") == "О735Б_1"
    with pytest.raises(ValueError):
        sanitize_cache_key("../secret")
    with pytest.raises(ValueError):
        sanitize_cache_key("sub/dir")

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock()
    mock_redis.set = AsyncMock()
    mgr = ImageCacheManager(mock_redis)
    mgr.cache_dir = tmp_path
    with pytest.raises(ValueError):
        mgr.get_file_path("../../etc/passwd")


def test_summary_advisor_overlapping_lessons_and_html_escape():
    stats = analyze_lessons_window(
        [
            {"start_time_raw": "09:00", "end_time_raw": "12:15"},
            {"start_time_raw": "10:00", "end_time_raw": "11:00"},
            {"start_time_raw": "12:30", "end_time_raw": "23:45"},
        ]
    )
    assert stats["max_gap_minutes"] == 15
    assert stats["last_end"] == "23:45"
    assert stats["end_hour"] == 23

    block = build_weather_summary_block(
        {"temperature": 5, "description": "<script>rain</script>", "emoji": "🌧️"},
        target_date=date(2026, 11, 25),
    )
    assert "&lt;script&gt;rain&lt;/script&gt;" in block


def test_text_formatters_escape_time_and_none_day_info():
    err = format_schedule_text(None)  # type: ignore[arg-type]
    assert isinstance(err, str)
    html_out = format_schedule_text(
        {
            "date": date(2026, 11, 25),
            "week_name": "Нечетная",
            "group": "О735Б",
            "lessons": [{"time": "09:00 <script>", "subject": "Математика"}],
        }
    )
    assert "&lt;script&gt;" in html_out
    week_out = format_full_week_text(
        {"Понедельник": [{"time": "09:00 <bad>", "subject": "Физика"}]},
        week_name="Нечетная <tag>",
    )
    assert "&lt;bad&gt;" in week_out
    assert "&lt;tag&gt;" in week_out


def test_inline_parse_day_uses_whole_tokens():
    today, rem = parse_day_from_query(["O735B", "not_today_group"])
    assert rem == ["O735B", "not_today_group"]


def test_custom_emoji_parse_mode_inheritance():
    bot = MagicMock()
    bot.default = MagicMock(parse_mode="HTML")
    assert _is_html_parse_mode(None, bot) is True
    assert _is_html_parse_mode("HTML", bot) is True
    assert _is_html_parse_mode("Markdown", bot) is False


@pytest.mark.asyncio
async def test_network_retry_does_not_retry_send_message_on_read_timeout():
    mw = NetworkRetryRequestMiddleware(max_attempts=4, backoff_base=0.0)
    calls = 0

    async def fake_make_request(bot, method):
        nonlocal calls
        calls += 1
        err = TelegramNetworkError(method=method, message="Request timeout error")
        raise err from asyncio.TimeoutError()

    method = SendMessage(chat_id=1, text="hello")
    with pytest.raises(TelegramNetworkError):
        await mw(fake_make_request, MagicMock(), method)
    assert calls == 1

    # Idempotent GetMe still retries on timeout
    calls_getme = 0

    async def fake_getme(bot, method):
        nonlocal calls_getme
        calls_getme += 1
        if calls_getme < 2:
            err = TelegramNetworkError(method=method, message="Request timeout error")
            raise err from asyncio.TimeoutError()
        return "ok"

    res = await mw(fake_getme, MagicMock(), GetMe())
    assert res == "ok"
    assert calls_getme == 2


@pytest.mark.asyncio
async def test_additional_security_branches_for_full_coverage(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace

    from core.alert_sender import AlertSender
    from core.alert_webhook import run_alert_webhook_server
    from core.db import backups
    from core.image_cache_manager import invalidate_generated_images_for_groups
    from core.image_service import ImageService
    from core.text_utils import transliterate_failed_search
    from core.user_data import UserDataManager

    # 1. text_utils empty string
    assert transliterate_failed_search("") == ""

    # 2. backups: invalid UTF-8 raises BackupError, CancelledError kills process, create_db_backup cleans partial file
    bad_utf8 = tmp_path / "bad.sql"
    bad_utf8.write_bytes(b"\xff\xfe\xfd")
    with pytest.raises(BackupError, match="valid UTF-8"):
        _sanitize_dump_file(bad_utf8)

    class _CancelProc:
        returncode = None

        def __init__(self):
            self.killed = False
            self.waited = False

        async def communicate(self):
            raise asyncio.CancelledError()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    proc = _CancelProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    with pytest.raises(asyncio.CancelledError):
        await backups._run_subprocess("pg_dump")
    assert proc.killed and proc.waited

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/test_db")

    async def _fail_after_touch(*cmd, **kwargs):
        args = list(cmd)
        Path(args[args.index("-f") + 1]).write_text("partial", encoding="utf-8")
        raise BackupError("failed mid-dump")

    monkeypatch.setattr(backups, "_run_subprocess", _fail_after_touch)
    with pytest.raises(BackupError, match="failed mid-dump"):
        await create_db_backup(output_dir=tmp_path, filename_prefix="partial_test")
    assert not list(tmp_path.glob("partial_test_*.sql"))

    # 3. alert_webhook: runner.cleanup() called when site.start() fails
    fake_runner = AsyncMock()
    fake_site = AsyncMock()
    fake_site.start = AsyncMock(side_effect=OSError("port in use"))
    with (
        patch("core.alert_webhook.web.AppRunner", return_value=fake_runner),
        patch("core.alert_webhook.web.TCPSite", return_value=fake_site),
    ):
        with pytest.raises(OSError):
            await run_alert_webhook_server(MagicMock(), [1])
    fake_runner.cleanup.assert_awaited_once()

    # 4. alert_sender: Telegram message > 4096 chars truncated
    sender = AlertSender({"TELEGRAM_ALERT_BOT_TOKEN": "tok", "TELEGRAM_ALERT_CHAT_ID": "1"})
    resp_ctx = AsyncMock()
    resp_ctx.__aenter__.return_value = SimpleNamespace(status=200, json=AsyncMock(return_value={"ok": True}))
    sender.session = MagicMock()
    sender.session.post = MagicMock(return_value=resp_ctx)
    assert await sender._send_telegram({"title": "T", "message": "X" * 5000}) is True
    sent_payload = sender.session.post.call_args.kwargs["json"]
    assert len(sent_payload["text"]) <= 4096

    # 5. ics_exporter: teachers as list
    info = SemesterInfo(semester="fall", semester_start=date(2026, 9, 1), week_number=1, week_key="odd")
    ics_list = generate_schedule_ics(
        "О735Б",
        {"odd": {"Понедельник": [{"subject": "С", "time": "09:00-10:30", "teachers": ["А.А.", "Б.Б."]}]}, "even": {}},
        info,
        reference_date=date(2026, 9, 1),
    )
    assert ics_list is not None and "А.А.\\, Б.Б." in ics_list.decode("utf-8").replace("\r\n ", "")

    # 6. image_service: rules_hash with no alnum chars
    cm = MagicMock()
    cm.is_cached = AsyncMock(return_value=True)
    p = tmp_path / "O735B_odd_ru.png"
    p.write_bytes(b"png")
    cm.get_file_path = MagicMock(return_value=p)
    svc = ImageService(cm, MagicMock())
    ok, _ = await svc.get_or_generate_week_image("О735Б", "odd", "Нечетная", {}, rules_hash="---")
    assert ok is True

    # 7. image_cache_manager: empty redis scan in invalidate_generated_images_for_groups & write error cleanup
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=b"0")
    mock_redis.set = AsyncMock()

    async def _empty_scan(_pat):
        if False:
            yield b""

    mock_redis.scan_iter = _empty_scan
    mock_redis.delete = AsyncMock()
    with patch("core.image_cache_manager.MEDIA_PATH", tmp_path):
        await invalidate_generated_images_for_groups(mock_redis, ["О735Б"])
    mock_redis.delete.assert_not_awaited()

    mgr = ImageCacheManager(mock_redis)
    mgr.cache_dir = tmp_path
    with patch("builtins.open", side_effect=OSError("disk full")):
        await mgr.cache_image("K1", b"123")

    # 8. weather_api: missing dt item and AlertSender failure on 401
    WeatherAPI._cache.clear()
    api = WeatherAPI(api_key="k", city_id="99")
    raw_key = "raw:99_metric_ru"
    WeatherAPI._cache[raw_key] = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        "data": {
            "list": [
                {"no_dt": True},
                {
                    "dt": int(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc).timestamp()),
                    "main": {"temp": 1},
                    "weather": [],
                },
            ]
        },
    }
    res_w = await api.get_forecast_for_time(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc))
    assert res_w is not None and res_w["description"] == "неизвестно"

    WeatherAPI._cache.clear()

    class _Resp401:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Sess401:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp401()

    with (
        patch("aiohttp.ClientSession", lambda *a, **k: _Sess401()),
        patch("core.alert_sender.AlertSender", side_effect=RuntimeError("alert broken")),
    ):
        assert await api.get_forecast_for_time(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)) is None

    # 9. worker_loop: re-entrant call, submit error closes coro, clean stop_worker_loop
    from bot.utils import worker_loop

    loop = worker_loop.get_worker_loop()

    async def _reentrant():
        with pytest.raises(RuntimeError, match="cannot be called from the worker event loop"):
            worker_loop.run_async(asyncio.sleep(0))

    worker_loop.run_async(_reentrant())
    with patch.object(worker_loop, "get_worker_loop", side_effect=RuntimeError("no loop")):
        coro = asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="no loop"):
            worker_loop.run_async(coro)
    assert loop is not None
    worker_loop.stop_worker_loop()

    # 10. user_data: close() closes redis + disposes engine (and tolerates redis close error)
    udm = UserDataManager(db_url="sqlite+aiosqlite:///:memory:")
    udm._redis_client = AsyncMock()
    udm._redis_client.aclose = AsyncMock(side_effect=RuntimeError("redis close err"))
    await udm.close()
    assert udm._redis_client is None

    # 11. image_cache_manager: cache_image unsafe key (tmp_path is None in except) & resolve escape
    await mgr.cache_image("../bad", b"123")
    with patch.object(Path, "is_relative_to", return_value=False):
        with pytest.raises(ValueError, match="escapes cache directory"):
            mgr.get_file_path("SAFE_KEY")

    # 12. manager: corrupt Redis cache falls through to fetch/fallback & tolerates delete error
    from core.manager import TimetableManager

    bad_redis = MagicMock()
    lock_cm = AsyncMock()
    lock_cm.__aenter__.return_value = None
    lock_cm.__aexit__.return_value = False
    bad_redis.lock = MagicMock(return_value=lock_cm)
    bad_redis.get = AsyncMock(return_value=b"not-valid-gzip-or-json")
    bad_redis.delete = AsyncMock(side_effect=RuntimeError("del failed"))
    bad_redis.set = AsyncMock()
    fresh_data = {"__metadata__": {}, "О735Б": {"odd": {}, "even": {}}}
    with patch("core.parser.fetch_and_parse_all_schedules", new=AsyncMock(return_value=fresh_data)):
        tm = await TimetableManager.create(bad_redis)
    assert tm is not None and "О735Б" in tm._schedules

    # 13. scheduler: handle_graduated_groups aborts when current_groups is empty
    from bot.scheduler import handle_graduated_groups

    udm_mock = MagicMock()
    udm_mock.get_students_with_groups = AsyncMock(return_value=[(1, "О735Б")])
    tm_empty = MagicMock(_schedules={"__metadata__": {}})
    await handle_graduated_groups(udm_mock, tm_empty, MagicMock())

    # 14. admin_menu: post-download file size > MAX_RESTORE_BYTES & _cleanup_restore_dir non-temp file
    from bot.dialogs import admin_menu

    plain_file = tmp_path / "plain.sql"
    plain_file.write_text("SELECT 1;", encoding="utf-8")
    admin_menu._cleanup_restore_dir(str(plain_file))
    assert not plain_file.exists()

    big_msg = MagicMock()
    big_msg.document = SimpleNamespace(file_name="big.sql", file_size=None)
    big_msg.answer = AsyncMock()

    async def _download_big(_doc, destination):
        Path(destination).write_bytes(b"0" * 32)

    big_msg.bot = MagicMock(download=AsyncMock(side_effect=_download_big))
    big_mgr = MagicMock(dialog_data={}, switch_to=AsyncMock())
    with patch.object(admin_menu, "MAX_RESTORE_BYTES", 10):
        await admin_menu.on_backup_upload_received(big_msg, MagicMock(), big_mgr)
    big_msg.answer.assert_awaited_once()
    assert "слишком большой" in big_msg.answer.await_args[0][0]

    # 15. schedule_view: blocked unsafe path & fail-closed answer exception swallowed
    from bot.dialogs import schedule_view as sv
    from bot.dialogs.constants import DialogDataKeys

    ctx = MagicMock(dialog_data={DialogDataKeys.GROUP: "О735Б", DialogDataKeys.CURRENT_DATE_ISO: "2025-09-01"})
    sv_dm = MagicMock()
    sv_dm.current_context.return_value = ctx
    sv_manager = MagicMock(redis=AsyncMock())
    sv_manager.get_academic_week_type = AsyncMock(return_value=("odd", "Нечётная"))
    sv_udm = MagicMock()
    sv_udm.get_user_language = AsyncMock(return_value="ru")
    sv_udm.get_user_theme = AsyncMock(return_value="standard")
    sv_dm.middleware_data = {"bot": AsyncMock(), "manager": sv_manager, "user_data_manager": sv_udm}
    sv_cb = MagicMock(from_user=SimpleNamespace(id=1), message=AsyncMock(), answer=AsyncMock())
    with (
        patch.object(sv, "_get_user_rules_for_target", new=AsyncMock(return_value=[])),
        patch.object(Path, "is_relative_to", return_value=False),
    ):
        await sv.on_send_original_file(sv_cb, None, sv_dm)

    sv_cb2 = MagicMock(
        from_user=SimpleNamespace(id=1),
        message=AsyncMock(),
        answer=AsyncMock(side_effect=RuntimeError("cb fail")),
    )
    with (
        patch.object(sv, "_get_user_rules_for_target", new=AsyncMock(return_value=[])),
        patch("core.subscription_filter.is_user_subscribed", side_effect=RuntimeError("sub err")),
    ):
        await sv.on_send_original_file(sv_cb2, None, sv_dm)

    # 16. feedback_reply_handler: non-admin user ignored when ADMIN_IDS is set
    import core.config as cfg
    from tests.coverage.test_feedback_reply_coverage import _load, _message

    monkeypatch.setattr(cfg, "ADMIN_IDS", [111])
    ns = _load("-1001234567890")
    non_admin_msg = _message(reply_to=SimpleNamespace(forward_from=SimpleNamespace(id=42)), text="hi")
    bot_mock = AsyncMock()
    await ns["handle_feedback_reply"](non_admin_msg, bot_mock, MagicMock())
    bot_mock.send_message.assert_not_awaited()
