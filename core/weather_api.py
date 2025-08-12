import aiohttp
import logging
import asyncio
from datetime import datetime, time, timedelta, timezone 
from typing import Optional, Dict, Any

from core.config import OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS, MOSCOW_TZ
from core.metrics import ERRORS_TOTAL, RETRIES_TOTAL

class WeatherAPI:
    BASE_URL = "https://api.openweathermap.org/data/2.5/forecast"
    CACHE_DURATION_HOURS = 1 # Кэш погоды будет храниться 1 час
    _cache: Dict[str, Dict[str, Any]] = {} # Словарь для хранения кэша: {'дата_час_прогноза': {'timestamp': ..., 'data': ...}}

    def __init__(self, api_key: str, city_id: str, units: str = "metric"):
        if not api_key:
            logging.error("OpenWeatherMap API key is not provided!")
            raise ValueError("API key cannot be empty.")
        self.api_key = api_key
        self.city_id = city_id
        self.units = units

    async def get_forecast_for_time(self, target_datetime: datetime) -> Optional[Dict[str, Any]]:
        """
        Получает прогноз погоды для ближайшего доступного временного интервала
        к target_datetime. Использует кэш.
        """
        # Формируем ключ кэша на основе даты и часа, для которого нужен прогноз
        # Округляем до ближайшего 3-часового интервала, т.к. OWM дает каждые 3 часа
        # Чтобы избежать проблем с часовыми поясами, работаем с UTC для ключа кэша
        target_utc_dt = target_datetime.astimezone(timezone.utc)
        target_hour_rounded = (target_utc_dt.hour // 3) * 3
        cache_key = f"{target_utc_dt.date().isoformat()}_{target_hour_rounded:02d}h"

        cached_entry = self._cache.get(cache_key)
        if cached_entry:
            cache_timestamp = cached_entry['timestamp']
            # Если кэш свежий (младше CACHE_DURATION_HOURS часов)
            if datetime.now(timezone.utc).replace(tzinfo=None) - cache_timestamp < timedelta(hours=self.CACHE_DURATION_HOURS):
                logging.info(f"Использую кэшированный прогноз для {cache_key}.")
                return cached_entry['data']

        params = {
            "id": self.city_id,
            "appid": self.api_key,
            "units": self.units,
            "lang": "ru" # Русский язык
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                # Исправление таймаута в session.get
                for attempt in range(3):
                    try:
                        logging.info(f"Выполняю запрос к OpenWeatherMap API для {cache_key} (попытка {attempt + 1})...")
                        async with session.get(self.BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=5)) as response:
                            response.raise_for_status()
                            data = await response.json()
                            break
                    except asyncio.TimeoutError:
                        logging.warning(f"Таймаут при получении погоды (попытка {attempt+1})")
                        RETRIES_TOTAL.labels(component='weather').inc()
                    except aiohttp.ClientError as e:
                        logging.error(f"Ошибка HTTP при запросе погоды: {e}")
                        ERRORS_TOTAL.labels(source='weather').inc()
                        return None
                    except Exception as e:
                        # На практике aiohttp.raise_for_status поднимает ClientResponseError (наследник ClientError),
                        # но для устойчивости обработаем и общий Exception из тестов/моков.
                        logging.error(f"Неожиданная ошибка при запросе погоды: {e}")
                        ERRORS_TOTAL.labels(source='weather').inc()
                        return None
                else:
                    logging.error("Все попытки получить прогноз погоды завершились неудачей.")
                    ERRORS_TOTAL.labels(source='weather').inc()
                    return None
        except Exception as e:
            logging.error(f"Критическая ошибка при обращении к погодному API: {e}")
            ERRORS_TOTAL.labels(source='weather').inc()
            return None

        closest_forecast_item = None
        min_diff = timedelta(days=999)

        for forecast_item in data.get("list", []):
            forecast_utc_dt = datetime.fromtimestamp(forecast_item["dt"], tz=timezone.utc) # OWM время всегда в UTC
            
            # Ищем прогноз, который находится в том же 3-часовом блоке, что и target_datetime
            # Или ближайший в будущем, если target_datetime очень близко к границе блока
            if forecast_utc_dt.date() == target_utc_dt.date() and (forecast_utc_dt.hour // 3) * 3 == target_hour_rounded:
                closest_forecast_item = forecast_item
                break # Нашли точный блок, дальше не ищем
            
            # Если не нашли точный блок, ищем ближайший по времени
            time_diff = abs(target_utc_dt - forecast_utc_dt)
            if time_diff < min_diff:
                closest_forecast_item = forecast_item
                min_diff = time_diff


        if closest_forecast_item:
            weather_description = closest_forecast_item.get("weather", [{}])[0].get("description", "неизвестно")
            temp = closest_forecast_item.get("main", {}).get("temp", "N/A")
            humidity = closest_forecast_item.get("main", {}).get("humidity", "N/A")
            wind_speed = closest_forecast_item.get("wind", {}).get("speed", "N/A")

            icon_code = closest_forecast_item.get("weather", [{}])[0].get("icon")
            emoji = self._get_weather_emoji(icon_code)

            result_data = {
                "temperature": round(temp),
                "description": weather_description,
                "emoji": emoji,
                "humidity": round(humidity),
                "wind_speed": round(wind_speed),
                "forecast_time": datetime.fromtimestamp(closest_forecast_item["dt"], tz=MOSCOW_TZ).strftime('%H:%M'),
            }
            self._cache[cache_key] = {'timestamp': datetime.now(timezone.utc).replace(tzinfo=None), 'data': result_data}
            return result_data
        return None

    def _get_weather_emoji(self, icon_code: str) -> str:
        """Преобразует код иконки OpenWeatherMap в эмодзи."""
        if icon_code.startswith("01"): return "☀️" # Clear sky
        if icon_code.startswith("02"): return "🌤️" # Few clouds
        if icon_code.startswith("03") or icon_code.startswith("04"): return "☁️" # Scattered / Broken clouds
        if icon_code.startswith("09") or icon_code.startswith("10"): return "🌧️" # Shower rain / Rain
        if icon_code.startswith("11"): return "⛈️" # Thunderstorm
        if icon_code.startswith("13"): return "🌨️" # Snow
        if icon_code.startswith("50"): return "🌫️" # Mist / Fog
        return "❓" # Unknown