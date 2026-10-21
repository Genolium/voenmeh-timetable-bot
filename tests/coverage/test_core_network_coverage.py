"""Coverage tests for network/service oriented ``core`` modules.

Modules covered here:

* ``core.weather_api``
* ``core.alert_sender``
* ``core.business_alerts``
* ``core.alert_webhook``
* ``core.telemetry``

All aiohttp sessions, sockets and OTLP exporters are mocked. No real network
or DB access happens.
"""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from core import business_alerts, telemetry, weather_api
from core.alert_sender import AlertSender
from core.alert_webhook import create_alert_app, format_alertmanager_message, run_alert_webhook_server
from core.business_alerts import AlertSeverity, BusinessMetricsMonitor
from core.telemetry import TelemetryHandles

# ---------------------------------------------------------------------------
# Shared aiohttp fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status=200, data=None, raise_exc=None, json_exc=None):
        self.status = status
        self._data = data if data is not None else {}
        self._raise = raise_exc
        self._json_exc = json_exc

    async def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._data

    def raise_for_status(self):
        if self._raise:
            raise self._raise


class _FakeGetCtx:
    def __init__(self, response=None, enter_exc=None):
        self._response = response
        self._enter_exc = enter_exc

    async def __aenter__(self):
        if self._enter_exc:
            raise self._enter_exc
        return self._response

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    """Minimal ``aiohttp.ClientSession`` replacement driven by a response queue."""

    def __init__(self, items):
        self._items = list(items)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, *args, **kwargs):
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        self.closed = True


def _patch_session(monkeypatch, items):
    monkeypatch.setattr(weather_api.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(items))


def _forecast_item(dt, desc="clear sky", icon="01d", temp=10.0, humidity=50, wind=3.0):
    return {
        "dt": dt,
        "weather": [{"description": desc, "icon": icon}],
        "main": {"temp": temp, "humidity": humidity},
        "wind": {"speed": wind},
    }


def _ts(year, month, day, hour):
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _clear_weather_cache():
    weather_api.WeatherAPI._cache.clear()
    yield
    weather_api.WeatherAPI._cache.clear()


# ---------------------------------------------------------------------------
# core.weather_api
# ---------------------------------------------------------------------------


def test_weather_enabled_property():
    assert weather_api.WeatherAPI("key", "1").enabled is True
    assert weather_api.WeatherAPI(None, "1").enabled is False


@pytest.mark.asyncio
async def test_weather_disabled_returns_none():
    api = weather_api.WeatherAPI(None, "1")
    assert await api.get_forecast_for_time(datetime(2026, 5, 1, 12, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_weather_cache_miss_then_hit(monkeypatch):
    target = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    payload = {"list": [_forecast_item(_ts(2026, 5, 1, 12))]}
    _patch_session(monkeypatch, [_FakeGetCtx(_FakeResponse(200, payload))])

    api = weather_api.WeatherAPI("key", "1")
    first = await api.get_forecast_for_time(target)
    assert first is not None
    assert first["emoji"] == "☀️"

    # Second call must be served from cache; a fresh session would explode.
    monkeypatch.setattr(weather_api.aiohttp, "ClientSession", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))
    second = await api.get_forecast_for_time(target)
    assert second == first


@pytest.mark.asyncio
async def test_weather_invalid_api_key_401(monkeypatch):
    _patch_session(monkeypatch, [_FakeGetCtx(_FakeResponse(401))])

    class _FakeAlertSender:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, data):
            return True

    monkeypatch.setattr("core.alert_sender.AlertSender", _FakeAlertSender)
    api = weather_api.WeatherAPI("bad", "1")
    assert await api.get_forecast_for_time(datetime(2026, 5, 1, 12, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_weather_timeout_then_success(monkeypatch):
    target = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    payload = {"list": [_forecast_item(_ts(2026, 5, 1, 12))]}
    _patch_session(
        monkeypatch,
        [
            _FakeGetCtx(enter_exc=asyncio.TimeoutError()),
            _FakeGetCtx(enter_exc=asyncio.TimeoutError()),
            _FakeGetCtx(_FakeResponse(200, payload)),
        ],
    )
    api = weather_api.WeatherAPI("key", "1")
    result = await api.get_forecast_for_time(target)
    assert result is not None


@pytest.mark.asyncio
async def test_weather_all_timeouts(monkeypatch):
    _patch_session(
        monkeypatch,
        [
            _FakeGetCtx(enter_exc=asyncio.TimeoutError()),
            _FakeGetCtx(enter_exc=asyncio.TimeoutError()),
            _FakeGetCtx(enter_exc=asyncio.TimeoutError()),
        ],
    )
    api = weather_api.WeatherAPI("key", "1")
    assert await api.get_forecast_for_time(datetime(2026, 5, 1, 12, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_weather_http_error(monkeypatch):
    err = aiohttp.ClientResponseError(MagicMock(), (), status=500, message="boom")
    _patch_session(monkeypatch, [_FakeGetCtx(_FakeResponse(500, raise_exc=err))])
    api = weather_api.WeatherAPI("key", "1")
    assert await api.get_forecast_for_time(datetime(2026, 5, 1, 12, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_weather_http_auth_error(monkeypatch):
    # The response itself reports 500, but raise_for_status raises an auth error.
    err = aiohttp.ClientResponseError(MagicMock(), (), status=403, message="forbidden")
    _patch_session(monkeypatch, [_FakeGetCtx(_FakeResponse(500, raise_exc=err))])
    api = weather_api.WeatherAPI("key", "1")
    assert await api.get_forecast_for_time(datetime(2026, 5, 1, 12, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_weather_generic_error(monkeypatch):
    _patch_session(monkeypatch, [_FakeGetCtx(_FakeResponse(200, raise_exc=ValueError("boom")))])
    api = weather_api.WeatherAPI("key", "1")
    assert await api.get_forecast_for_time(datetime(2026, 5, 1, 12, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_weather_session_constructor_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("cannot create session")

    monkeypatch.setattr(weather_api.aiohttp, "ClientSession", _boom)
    api = weather_api.WeatherAPI("key", "1")
    assert await api.get_forecast_for_time(datetime(2026, 5, 1, 12, tzinfo=timezone.utc)) is None


@pytest.mark.asyncio
async def test_weather_closest_forecast_and_empty(monkeypatch):
    target = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    # No item in the exact 3h block; nearest is at 09:00 UTC.
    payload = {
        "list": [
            _forecast_item(_ts(2026, 5, 1, 9), desc="clouds", icon="04d"),
            _forecast_item(_ts(2026, 5, 1, 18), desc="rain", icon="10d"),
        ]
    }
    _patch_session(monkeypatch, [_FakeGetCtx(_FakeResponse(200, payload))])
    api = weather_api.WeatherAPI("key", "1")
    result = await api.get_forecast_for_time(target)
    assert result is not None
    assert result["emoji"] == "☁️"

    # Empty forecast list -> None.
    _patch_session(monkeypatch, [_FakeGetCtx(_FakeResponse(200, {"list": []}))])
    api2 = weather_api.WeatherAPI("key", "2")
    assert await api2.get_forecast_for_time(datetime(2026, 6, 1, 12, tzinfo=timezone.utc)) is None


def test_weather_emoji_mapping():
    api = weather_api.WeatherAPI("key", "1")
    cases = {
        "01d": "☀️",
        "02d": "🌤️",
        "03d": "☁️",
        "04n": "☁️",
        "09d": "🌧️",
        "10d": "🌧️",
        "11d": "⛈️",
        "13d": "🌨️",
        "50d": "🌫️",
        "99d": "❓",
    }
    for code, emoji in cases.items():
        assert api._get_weather_emoji(code) == emoji


# ---------------------------------------------------------------------------
# core.alert_sender
# ---------------------------------------------------------------------------


class _PostResponse:
    def __init__(self, status=200, data=None):
        self.status = status
        self._data = data if data is not None else {}

    async def json(self):
        return self._data


class _PostCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return False


class _FakePostSession:
    def __init__(self, status=200, data=None, raise_exc=None, coroutine=False):
        self.calls = []
        self._status = status
        self._data = data
        self._raise = raise_exc
        self._coroutine = coroutine

    def _make(self, url, kwargs):
        self.calls.append((url, kwargs))
        if self._raise:
            raise self._raise
        return _PostCtx(_PostResponse(self._status, self._data))

    def post(self, url, **kwargs):
        if self._coroutine:

            async def _coro():
                return self._make(url, kwargs)

            return _coro()
        return self._make(url, kwargs)

    async def close(self):
        pass


FULL_SETTINGS = {
    "SLACK_WEBHOOK_URL": "https://slack.example",
    "DISCORD_WEBHOOK_URL": "https://discord.example",
    "TELEGRAM_ALERT_BOT_TOKEN": "token",
    "TELEGRAM_ALERT_CHAT_ID": "42",
    "ALERT_WEBHOOK_URL": "https://http.example",
    "ALERT_WEBHOOK_API_KEY": "secret-key",
}


@pytest.mark.asyncio
async def test_alert_sender_context_manager(monkeypatch):
    monkeypatch.setattr("core.alert_sender.aiohttp.ClientSession", _FakePostSession)
    async with AlertSender(FULL_SETTINGS) as sender:
        assert sender.session is not None


@pytest.mark.asyncio
async def test_alert_sender_send_all_channels():
    sender = AlertSender(FULL_SETTINGS)
    sender.session = _FakePostSession(status=200, data={"ok": True})
    ok = await sender.send({"severity": "critical", "title": "T", "message": "M", "tags": {"a": "b"}})
    assert ok is True
    assert len(sender.session.calls) == 4


@pytest.mark.asyncio
async def test_alert_sender_send_info_skips_telegram():
    sender = AlertSender(FULL_SETTINGS)
    sender.session = _FakePostSession(status=200, data={"ok": True})
    ok = await sender.send({"severity": "info", "title": "T", "message": "M"})
    assert ok is True
    # Only the HTTP channel accepts "info"; slack/discord/telegram are filtered out.
    assert len(sender.session.calls) == 1


@pytest.mark.asyncio
async def test_alert_sender_send_no_webhooks():
    sender = AlertSender({})
    sender.session = _FakePostSession()
    assert await sender.send({"severity": "critical"}) is False


@pytest.mark.asyncio
async def test_alert_sender_send_creates_session(monkeypatch):
    monkeypatch.setattr("core.alert_sender.aiohttp.ClientSession", lambda *a, **k: _FakePostSession(status=200))
    sender = AlertSender({"SLACK_WEBHOOK_URL": "https://slack.example"})
    assert await sender.send({"severity": "warning"}) is True
    assert sender.session is not None


@pytest.mark.asyncio
async def test_alert_sender_send_exception_path():
    sender = AlertSender({"SLACK_WEBHOOK_URL": "https://slack.example"})
    sender.session = _FakePostSession()

    async def _boom(_data):
        raise RuntimeError("kaboom")

    sender._send_slack = _boom
    assert await sender.send({"severity": "critical"}) is False


@pytest.mark.asyncio
async def test_alert_sender_normalize_ctx_coroutine():
    sender = AlertSender({"SLACK_WEBHOOK_URL": "https://slack.example"})
    sender.session = _FakePostSession(status=200, coroutine=True)
    assert await sender._send_slack({"severity": "warning", "title": "T"}) is True


@pytest.mark.asyncio
async def test_alert_sender_normalize_ctx_exception():
    class _BadCtx:
        async def __aenter__(self):
            raise RuntimeError("nope")

        async def __aexit__(self, *a):
            return False

    class _Session:
        def post(self, *a, **k):
            async def _coro():
                raise RuntimeError("await failed")

            return _coro()

    sender = AlertSender({"SLACK_WEBHOOK_URL": "https://slack.example"})
    sender.session = _Session()
    # The coroutine await fails inside _normalize_ctx -> returns original object,
    # and the outer handler returns False.
    assert await sender._send_slack({"severity": "warning"}) is False


@pytest.mark.asyncio
async def test_alert_sender_slack_status_and_tags():
    sender = AlertSender({"SLACK_WEBHOOK_URL": "https://slack.example"})
    sender.session = _FakePostSession(status=500)
    assert await sender._send_slack({"severity": "warning", "tags": {"k": "v"}}) is False

    sender.session = _FakePostSession(status=200)
    assert await sender._send_slack({"severity": "warning", "tags": {"k": "v"}}) is True

    sender.session = _FakePostSession(raise_exc=RuntimeError("x"))
    assert await sender._send_slack({"severity": "warning"}) is False


@pytest.mark.asyncio
async def test_alert_sender_discord_branches():
    sender = AlertSender({"DISCORD_WEBHOOK_URL": "https://discord.example"})
    sender.session = _FakePostSession(status=200)
    assert await sender._send_discord({"severity": "warning", "tags": {"k": "v"}}) is True

    sender.session = _FakePostSession(status=204)
    assert await sender._send_discord({"severity": "warning"}) is True

    sender.session = _FakePostSession(status=500)
    assert await sender._send_discord({"severity": "warning"}) is False

    sender.session = _FakePostSession(raise_exc=RuntimeError("x"))
    assert await sender._send_discord({"severity": "warning"}) is False


@pytest.mark.asyncio
async def test_alert_sender_telegram_branches():
    sender = AlertSender({"TELEGRAM_ALERT_BOT_TOKEN": "token"})
    # No chat id -> early False.
    sender.session = _FakePostSession(status=200, data={"ok": True})
    assert await sender._send_telegram({"severity": "critical"}) is False

    sender = AlertSender({"TELEGRAM_ALERT_BOT_TOKEN": "token", "TELEGRAM_ALERT_CHAT_ID": "42"})
    sender.session = _FakePostSession(status=200, data={"ok": True})
    assert await sender._send_telegram({"severity": "critical", "tags": {"k": "v"}}) is True

    sender.session = _FakePostSession(status=200, data={"ok": False})
    assert await sender._send_telegram({"severity": "critical"}) is False

    sender.session = _FakePostSession(status=400)
    assert await sender._send_telegram({"severity": "critical"}) is False

    sender.session = _FakePostSession(raise_exc=RuntimeError("x"))
    assert await sender._send_telegram({"severity": "critical"}) is False


@pytest.mark.asyncio
async def test_alert_sender_http_branches():
    sender = AlertSender({"ALERT_WEBHOOK_URL": "https://http.example", "ALERT_WEBHOOK_API_KEY": "k"})
    sender.session = _FakePostSession(status=201)
    assert await sender._send_http({"severity": "info"}) is True
    assert sender.session.calls[0][1]["headers"]["Authorization"] == "Bearer k"

    sender.session = _FakePostSession(status=202)
    assert await sender._send_http({"severity": "info"}) is True

    sender.session = _FakePostSession(status=500)
    assert await sender._send_http({"severity": "info"}) is False

    sender.session = _FakePostSession(raise_exc=RuntimeError("x"))
    assert await sender._send_http({"severity": "info"}) is False


# ---------------------------------------------------------------------------
# core.business_alerts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_business_start_monitoring_loop_and_error(monkeypatch):
    monitor = BusinessMetricsMonitor()
    monitor._check_business_metrics = AsyncMock()

    async def _stop(_seconds):
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(business_alerts.asyncio, "sleep", _stop)
    await monitor.start_monitoring()
    assert monitor._check_business_metrics.await_count == 1


@pytest.mark.asyncio
async def test_business_check_metrics_disabled(monkeypatch):
    monitor = BusinessMetricsMonitor()
    monitor.prometheus_url = None
    await monitor._check_business_metrics()  # returns immediately


@pytest.mark.asyncio
async def test_business_check_metrics_success_and_failure(monkeypatch):
    monitor = BusinessMetricsMonitor()
    monitor.prometheus_url = "http://prom"
    monitor._send_alert = AsyncMock()

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **k: _FakeSession([_FakeGetCtx(_FakeResponse(200, {"status": "success"}))])
    )
    await monitor._check_business_metrics()
    assert monitor._send_alert.await_count == 0

    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **k: _FakeSession([_FakeGetCtx(_FakeResponse(200, {"status": "error"}))])
    )
    await monitor._check_business_metrics()
    assert monitor._send_alert.await_count == 1


@pytest.mark.asyncio
async def test_business_check_metrics_exception(monkeypatch):
    monitor = BusinessMetricsMonitor()
    monitor.prometheus_url = "http://prom"

    def _boom(*a, **k):
        raise RuntimeError("no session")

    monkeypatch.setattr(aiohttp, "ClientSession", _boom)
    await monitor._check_business_metrics()  # exception swallowed


@pytest.mark.asyncio
async def test_business_send_alert_cooldown_and_exception(monkeypatch):
    monitor = BusinessMetricsMonitor()
    monitor.cooldown_minutes = 30

    sent = {}

    class _FakeSender:
        def __init__(self, settings):
            self.settings = settings

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, payload):
            sent["payload"] = payload
            return True

    monkeypatch.setattr(business_alerts, "AlertSender", _FakeSender)
    await monitor._send_alert("T", "M", AlertSeverity.WARNING, "metric", {"k": "v"})
    assert sent["payload"]["title"] == "T"

    # Second call is within cooldown -> skipped.
    sent.clear()
    await monitor._send_alert("T", "M", AlertSeverity.WARNING, "metric", {"k": "v"})
    assert sent == {}

    # Sender raising is swallowed.
    class _BadSender:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            raise RuntimeError("nope")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(business_alerts, "AlertSender", _BadSender)
    monitor.alert_cooldown.clear()
    await monitor._send_alert("T2", "M", AlertSeverity.CRITICAL, "metric2", {})


@pytest.mark.asyncio
async def test_start_business_monitoring(monkeypatch):
    started = AsyncMock()
    monkeypatch.setattr(business_alerts.business_monitor, "start_monitoring", started)
    await business_alerts.start_business_monitoring()
    started.assert_awaited_once()


# ---------------------------------------------------------------------------
# core.alert_webhook
# ---------------------------------------------------------------------------


def _get_handler(app, path, method):
    for route in app.router.routes():
        if route.resource is not None and route.resource.canonical == path and route.method == method:
            return route.handler
    raise AssertionError(f"handler {method} {path} not found")


def _request(headers=None, payload=None, json_exc=None):
    req = MagicMock()
    req.headers = headers if headers is not None else {}
    if json_exc is not None:
        req.json = AsyncMock(side_effect=json_exc)
    else:
        req.json = AsyncMock(return_value=payload if payload is not None else {})
    return req


@pytest.mark.asyncio
async def test_webhook_health_check():
    app = create_alert_app(MagicMock(), [1])
    resp = await _get_handler(app, "/health", "GET")(_request())
    assert resp.status == 200


@pytest.mark.asyncio
async def test_webhook_readiness_all_ok():
    redis_client = AsyncMock()
    redis_client.ping = AsyncMock()

    session = MagicMock()
    session.execute = AsyncMock()

    class _CM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    app = create_alert_app(MagicMock(), [1], redis_client=redis_client, db_session_maker=lambda: _CM())
    resp = await _get_handler(app, "/readiness", "GET")(_request())
    assert resp.status == 200


@pytest.mark.asyncio
async def test_webhook_readiness_without_clients():
    app = create_alert_app(MagicMock(), [1])
    resp = await _get_handler(app, "/readiness", "GET")(_request())
    assert resp.status == 503


@pytest.mark.asyncio
async def test_webhook_readiness_with_errors():
    redis_client = AsyncMock()
    redis_client.ping = AsyncMock(side_effect=RuntimeError("redis down"))

    class _CM:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    app = create_alert_app(MagicMock(), [1], redis_client=redis_client, db_session_maker=lambda: _CM())
    resp = await _get_handler(app, "/readiness", "GET")(_request())
    assert resp.status == 503


@pytest.mark.asyncio
async def test_webhook_alerts_auth(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_API_KEY", "secret")
    bot = MagicMock()
    bot.send_message = AsyncMock()
    app = create_alert_app(bot, [1])
    handler = _get_handler(app, "/alerts", "POST")

    resp = await handler(_request(payload={"status": "firing", "alerts": []}))
    assert resp.status == 401

    resp = await handler(_request(headers={"Authorization": "Bearer wrong"}, payload={"status": "firing", "alerts": []}))
    assert resp.status == 401

    resp = await handler(_request(headers={"Authorization": "Bearer secret"}, payload={"status": "firing", "alerts": []}))
    assert resp.status == 200


@pytest.mark.asyncio
async def test_webhook_alerts_invalid_json():
    bot = MagicMock()
    app = create_alert_app(bot, [1])
    handler = _get_handler(app, "/alerts", "POST")
    resp = await handler(_request(json_exc=ValueError("bad json")))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_webhook_alerts_send_failure_swallowed():
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("bot down"))
    app = create_alert_app(bot, [1, 2])
    handler = _get_handler(app, "/alerts", "POST")
    payload = {
        "status": "firing",
        "alerts": [{"labels": {"alertname": "A", "severity": "critical"}, "annotations": {"description": "d"}}],
    }
    resp = await handler(_request(payload=payload))
    assert resp.status == 200
    assert bot.send_message.await_count == 2


def test_format_alertmanager_message_variants():
    payload = {
        "status": "firing",
        "alerts": [
            {
                "labels": {"alertname": "A", "severity": "warning", "component": "comp"},
                "annotations": {"summary": "sum"},
                "startsAt": "t0",
                "endsAt": "t1",
            },
            {"labels": {}, "annotations": {}},
        ],
    }
    message = format_alertmanager_message(payload)
    assert "ALERTMANAGER: FIRING (2 alert(s))" in message
    assert "source=comp" in message
    assert "sum" in message
    assert "unknown [unknown]" in message

    assert "ALERTMANAGER: UNKNOWN (0 alert(s))" in format_alertmanager_message({})


@pytest.mark.asyncio
async def test_run_alert_webhook_server(monkeypatch):
    runner = MagicMock()
    runner.setup = AsyncMock()
    site = MagicMock()
    site.start = AsyncMock()

    monkeypatch.setattr("core.alert_webhook.web.AppRunner", lambda app: runner)
    monkeypatch.setattr("core.alert_webhook.web.TCPSite", lambda *a, **k: site)

    await run_alert_webhook_server(MagicMock(), [1], port=9999)
    runner.setup.assert_awaited_once()
    site.start.assert_awaited_once()


# ---------------------------------------------------------------------------
# core.telemetry
# ---------------------------------------------------------------------------


def test_telemetry_parse_headers():
    assert telemetry._parse_headers(None) == {}
    assert telemetry._parse_headers("") == {}
    assert telemetry._parse_headers("a=1, ,bad,c=2") == {"a": "1", "c": "2"}


def test_telemetry_is_endpoint_reachable(monkeypatch):
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(telemetry.socket, "create_connection", lambda addr, timeout=None: _Ctx())
    assert telemetry._is_endpoint_reachable("https://collector:4318") is True

    def _oserror(addr, timeout=None):
        raise OSError("unreachable")

    monkeypatch.setattr(telemetry.socket, "create_connection", _oserror)
    assert telemetry._is_endpoint_reachable("https://collector:4318") is False

    assert telemetry._is_endpoint_reachable("not-a-url") is False


def test_telemetry_setup_disabled_when_unreachable(monkeypatch):
    monkeypatch.setattr(telemetry, "_is_endpoint_reachable", lambda *a, **k: False)
    assert telemetry.setup_telemetry(service_name="svc", endpoint="http://collector:4318") is None


def test_telemetry_setup_all_disabled(monkeypatch):
    handles = telemetry.setup_telemetry(
        service_name="svc",
        enable_traces=False,
        enable_metrics=False,
        enable_logs=False,
    )
    assert handles is None


def test_telemetry_setup_success(monkeypatch):
    monkeypatch.setattr(telemetry, "_is_endpoint_reachable", lambda *a, **k: True)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **k: MagicMock())
    monkeypatch.setattr(telemetry, "OTLPMetricExporter", lambda **k: MagicMock())
    monkeypatch.setattr(telemetry, "OTLPLogExporter", lambda **k: MagicMock())

    handles = telemetry.setup_telemetry(
        service_name="svc",
        environment="test",
        endpoint="http://collector:4318",
        headers="x-auth=token",
    )
    assert isinstance(handles, TelemetryHandles)
    assert handles.tracer_provider is not None
    assert handles.meter_provider is not None
    assert handles.logger_provider is not None
    telemetry.shutdown_telemetry(handles)


def test_telemetry_setup_without_instrumentor(monkeypatch):
    monkeypatch.setattr(telemetry, "_is_endpoint_reachable", lambda *a, **k: True)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **k: MagicMock())
    monkeypatch.setattr(telemetry, "OTLPMetricExporter", lambda **k: MagicMock())
    monkeypatch.setattr(telemetry, "OTLPLogExporter", lambda **k: MagicMock())
    monkeypatch.setattr(telemetry, "LoggingInstrumentor", None)

    handles = telemetry.setup_telemetry(
        service_name="svc",
        endpoint="http://collector:4318",
        enable_metrics=False,
        enable_logs=False,
    )
    assert handles is not None
    assert handles.meter_provider is None
    telemetry.shutdown_telemetry(handles)


def test_telemetry_shutdown_none():
    telemetry.shutdown_telemetry(None)


def test_telemetry_shutdown_removes_log_handler():
    root_logger = logging.getLogger()
    handler = logging.Handler()
    root_logger.addHandler(handler)

    handles = TelemetryHandles(
        tracer_provider=MagicMock(),
        meter_provider=MagicMock(),
        logger_provider=MagicMock(),
        log_handler=handler,
    )
    telemetry.shutdown_telemetry(handles)
    assert handler not in root_logger.handlers
    assert handles.tracer_provider.shutdown.called
    assert handles.meter_provider.shutdown.called
    assert handles.logger_provider.shutdown.called
