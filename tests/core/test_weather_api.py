from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.weather_api import WeatherAPI


@pytest.fixture
def mock_aiohttp_get(mocker):
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "list": [
            {
                "dt": 1672560000,
                "main": {"temp": -5.5, "humidity": 80},
                "weather": [{"description": "пасмурно", "icon": "04d"}],
                "wind": {"speed": 3.1},
            }
        ]
    }
    mock_session_get = AsyncMock()
    mock_session_get.__aenter__.return_value = mock_response
    return mocker.patch("aiohttp.ClientSession.get", return_value=mock_session_get)


def test_weather_api_emoji_mapping():
    api = WeatherAPI(api_key="k", city_id="1")
    assert api._get_weather_emoji("01d") == "☀️"
    assert api._get_weather_emoji("02n") == "🌤️"
    assert api._get_weather_emoji("03d") == "☁️"
    assert api._get_weather_emoji("09d") == "🌧️"
    assert api._get_weather_emoji("10n") == "🌧️"
    assert api._get_weather_emoji("11d") == "⛈️"
    assert api._get_weather_emoji("13d") == "🌨️"
    assert api._get_weather_emoji("50d") == "🌫️"
    assert api._get_weather_emoji("xx") == "❓"


@pytest.mark.asyncio
async def test_weather_api_success(monkeypatch):
    # Очистка кэша, чтобы не влиял
    WeatherAPI._cache.clear()
    api = WeatherAPI(api_key="k", city_id="1")
    now = datetime.now(timezone.utc)
    dt_val = int(now.timestamp())

    class GoodResp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            return None

        async def json(self):
            return {
                "list": [
                    {
                        "dt": dt_val,
                        "weather": [{"description": "clear", "icon": "01d"}],
                        "main": {"temp": 12.7, "humidity": 55},
                        "wind": {"speed": 3.2},
                    }
                ]
            }

        status = 200

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            return GoodResp()

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Session())

    res = await api.get_forecast_for_time(now)
    assert res is not None and res["emoji"]


@pytest.mark.asyncio
async def test_weather_api_http_error(monkeypatch):
    # Очистим кэш, чтобы не мешал этому сценарию
    WeatherAPI._cache.clear()
    api = WeatherAPI(api_key="k", city_id="1")

    class BadResp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            raise Exception("boom")

        async def json(self):
            return {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            return BadResp()

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Session())

    res = await api.get_forecast_for_time(datetime.now(timezone.utc))
    assert res is None


@pytest.mark.asyncio
async def test_weather_api_cache_hit(monkeypatch):
    api = WeatherAPI(api_key="k", city_id="1")
    # Подготовим кэш на ближайший слот
    target = datetime.now(timezone.utc)
    key = f"1_metric_{target.date().isoformat()}_{(target.hour // 3)*3:02d}h_ru"
    WeatherAPI._cache[key] = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        "data": {
            "temperature": 1,
            "description": "ok",
            "emoji": "☀️",
            "humidity": 1,
            "wind_speed": 1,
            "forecast_time": "12:00",
        },
    }
    result = await api.get_forecast_for_time(target)
    assert result is not None


@pytest.mark.asyncio
async def test_weather_api_raw_cache_bad_pop_and_day_window(monkeypatch):
    api = WeatherAPI(api_key="k", city_id="2")
    target = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    raw_key = "raw:2_metric_ru"
    WeatherAPI._cache[raw_key] = {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        "data": {
            "list": [
                {
                    "dt": int(target.timestamp()),
                    "main": {"temp": 2, "feels_like": -1, "humidity": 80},
                    "wind": {"speed": 5},
                    "pop": "bad_pop",
                    "weather": [{"description": "снег", "icon": "13d"}],
                }
            ]
        },
    }
    res = await api.get_forecast_for_time(target)
    assert res is not None
    assert res["pop"] == 0
    assert res["precip_kind"] == "snow"

    # _get_precip_kind branches
    assert WeatherAPI._get_precip_kind(None) is None
    assert WeatherAPI._get_precip_kind("11d") == "storm"
    assert WeatherAPI._get_precip_kind("13n") == "snow"
    assert WeatherAPI._get_precip_kind("09d") == "rain"
    assert WeatherAPI._get_precip_kind("01d") is None

    # get_day_weather_window branches
    no_key_api = WeatherAPI(api_key="", city_id="2")
    assert await no_key_api.get_day_weather_window(target.date()) is None

    async def _fake_forecast_none(_dt, lang="ru"):
        return None

    monkeypatch.setattr(api, "get_forecast_for_time", _fake_forecast_none)
    assert await api.get_day_weather_window(target.date(), 9, 18) is None

    async def _fake_forecast_ok(dt, lang="ru"):
        return {
            "temperature": 1 if dt.hour < 12 else -1,
            "feels_like": -3,
            "wind_speed": 6,
            "pop": 70,
            "precip_kind": "rain" if dt.hour < 12 else "storm",
            "description": "тест",
            "emoji": "⛈️",
        }

    monkeypatch.setattr(api, "get_forecast_for_time", _fake_forecast_ok)
    win = await api.get_day_weather_window(target.date(), start_hour=9, end_hour=18, lang="ru")
    assert win is not None
    assert win["precip_kind"] == "storm"
    assert win["end"] is not None

    win_short = await api.get_day_weather_window(target.date(), start_hour=9, end_hour=10, lang="ru")
    assert win_short is not None and win_short["end"] is None

    # end_forecast is None branch
    async def _fake_start_only(dt, lang="ru"):
        if dt.hour >= 12:
            return None
        return {
            "temperature": 5,
            "feels_like": 5,
            "wind_speed": 2,
            "pop": 0,
            "precip_kind": "storm",
            "description": "s",
            "emoji": "⛈️",
        }

    monkeypatch.setattr(api, "get_forecast_for_time", _fake_start_only)
    win_no_end = await api.get_day_weather_window(target.date(), start_hour=9, end_hour=18, lang="ru")
    assert win_no_end is not None and win_no_end["end"] is None

    # Second slot has lower precip_kind priority than first
    async def _fake_storm_then_rain(dt, lang="ru"):
        return {
            "temperature": 5,
            "feels_like": 5,
            "wind_speed": 2,
            "pop": 50,
            "precip_kind": "storm" if dt.hour < 12 else "rain",
            "description": "s",
            "emoji": "⛈️",
        }

    monkeypatch.setattr(api, "get_forecast_for_time", _fake_storm_then_rain)
    win_prio = await api.get_day_weather_window(target.date(), start_hour=9, end_hour=18, lang="ru")
    assert win_prio is not None and win_prio["precip_kind"] == "storm"
