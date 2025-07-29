import pytest
from datetime import datetime
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

@pytest.mark.asyncio
class TestWeatherAPI:
    @pytest.fixture(autouse=True)
    def clear_cache(self, monkeypatch):
        monkeypatch.setattr(WeatherAPI, '_cache', {})

    def test_init_raises_error_on_no_api_key(self):
        with pytest.raises(ValueError):
            WeatherAPI(api_key="", city_id="123")

    async def test_get_forecast_for_time_success(self, mock_aiohttp_get):
        weather_api = WeatherAPI(api_key="fake_key", city_id="123")
        target_time = datetime(2023, 1, 1, 13, 0)

        forecast = await weather_api.get_forecast_for_time(target_time)

        assert forecast is not None
        assert forecast["temperature"] == -6
        assert forecast["description"] == "пасмурно"
        assert forecast["emoji"] == "☁️"
        mock_aiohttp_get.assert_called_once()

    async def test_get_forecast_uses_cache(self, mock_aiohttp_get):
        weather_api = WeatherAPI(api_key="fake_key", city_id="123")
        target_time = datetime(2023, 1, 1, 13, 0)
        
        await weather_api.get_forecast_for_time(target_time)
        await weather_api.get_forecast_for_time(target_time)

        mock_aiohttp_get.assert_called_once()

    async def test_get_forecast_http_error(self, mocker):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock(side_effect=pytest.importorskip("aiohttp").ClientError("Test Error"))
        mock_session_get = AsyncMock()
        mock_session_get.__aenter__.return_value = mock_response
        mocker.patch('aiohttp.ClientSession.get', return_value=mock_session_get)

        weather_api = WeatherAPI(api_key="fake_key", city_id="123")
        forecast = await weather_api.get_forecast_for_time(datetime.now())
        
        assert forecast is None