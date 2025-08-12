import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
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
                "wind": {"speed": 3.1}
            }
        ]
    }
    mock_session_get = AsyncMock()
    mock_session_get.__aenter__.return_value = mock_response
    return mocker.patch('aiohttp.ClientSession.get', return_value=mock_session_get)

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

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, *a, **k):
            return GoodResp()

    monkeypatch.setattr('aiohttp.ClientSession', lambda *a, **k: Session())

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

    monkeypatch.setattr('aiohttp.ClientSession', lambda *a, **k: Session())

    res = await api.get_forecast_for_time(datetime.now(timezone.utc))
    assert res is None

@pytest.mark.asyncio
async def test_weather_api_cache_hit(monkeypatch):
    api = WeatherAPI(api_key="k", city_id="1")
    # Подготовим кэш на ближайший слот
    target = datetime.now(timezone.utc)
    key = f"{target.date().isoformat()}_{(target.hour // 3)*3:02d}h"
    WeatherAPI._cache[key] = {
        'timestamp': datetime.now(timezone.utc).replace(tzinfo=None),
        'data': {"temperature": 1, "description": "ok", "emoji": "☀️", "humidity": 1, "wind_speed": 1, "forecast_time": "12:00"}
    }
    result = await api.get_forecast_for_time(target)
    assert result is not None