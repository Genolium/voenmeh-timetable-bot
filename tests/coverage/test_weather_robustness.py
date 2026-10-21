from core.weather_api import WeatherAPI


def test_safe_number_handles_invalid_values():
    assert WeatherAPI._safe_number("N/A") == "N/A"
    assert WeatherAPI._safe_number(None) == "N/A"
    assert WeatherAPI._safe_number("12.6") == 13


def test_weather_emoji_handles_missing_icon():
    api = WeatherAPI(api_key=None, city_id="1")
    assert api._get_weather_emoji(None) == "❓"
