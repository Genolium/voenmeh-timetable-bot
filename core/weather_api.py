import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, cast

import aiohttp

from core.config import MOSCOW_TZ
from core.metrics import ERRORS_TOTAL, RETRIES_TOTAL


class WeatherAPI:
    BASE_URL = "https://api.openweathermap.org/data/2.5/forecast"
    CACHE_DURATION_HOURS = 1  # Кэш погоды будет храниться 1 час
    _cache: Dict[str, Dict[str, Any]] = {}  # Словарь для хранения кэша: {'дата_час_прогноза': {'timestamp': ..., 'data': ...}}

    def __init__(self, api_key: str | None, city_id: str, units: str = "metric"):
        self.api_key = api_key or ""
        self.city_id = city_id
        self.units = units

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def get_forecast_for_time(self, target_datetime: datetime, lang: str = "ru") -> Optional[Dict[str, Any]]:
        """
        Получает прогноз погоды для ближайшего доступного временного интервала
        к target_datetime. Использует кэш.
        """
        if not self.api_key:
            return None
        # Формируем ключ кэша на основе даты, часа, города, единиц и языка прогноза
        if target_datetime.tzinfo is None:
            target_utc_dt = MOSCOW_TZ.localize(target_datetime).astimezone(timezone.utc)
        else:
            target_utc_dt = target_datetime.astimezone(timezone.utc)
        target_hour_rounded = (target_utc_dt.hour // 3) * 3
        cache_key = f"{self.city_id}_{self.units}_{target_utc_dt.date().isoformat()}_{target_hour_rounded:02d}h_{lang}"

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        max_age = timedelta(hours=self.CACHE_DURATION_HOURS)
        expired_keys = [k for k, v in self._cache.items() if now_utc - v["timestamp"] >= max_age]
        for k in expired_keys:
            self._cache.pop(k, None)

        cached_entry = self._cache.get(cache_key)
        if cached_entry:
            logging.info(f"Использую кэшированный прогноз для {cache_key}.")
            return cast(dict[str, Any] | None, cached_entry["data"])

        raw_cache_key = f"raw:{self.city_id}_{self.units}_{lang}"
        raw_entry = self._cache.get(raw_cache_key)
        if raw_entry and now_utc - raw_entry["timestamp"] < max_age:
            data = raw_entry["data"]
        else:
            params = {
                "id": self.city_id,
                "appid": self.api_key,
                "units": self.units,
                "lang": lang,  # Язык описания погоды
            }

            try:
                async with aiohttp.ClientSession() as session:
                    # Исправление таймаута в session.get
                    for attempt in range(3):
                        try:
                            logging.info(f"Выполняю запрос к OpenWeatherMap API для {cache_key} (попытка {attempt + 1})...")
                            async with session.get(
                                self.BASE_URL,
                                params=params,
                                timeout=aiohttp.ClientTimeout(total=5),
                            ) as response:
                                if response.status in (401, 403):
                                    logging.error("Invalid API key for OpenWeatherMap.")
                                    # Send alert
                                    from core.alert_sender import AlertSender

                                    alert_settings: dict[str, Any] = {}  # Load settings
                                    async with AlertSender(alert_settings) as sender:
                                        await sender.send(
                                            {
                                                "severity": "critical",
                                                "summary": "Invalid OpenWeatherMap API key",
                                            }
                                        )
                                    ERRORS_TOTAL.labels(source="weather").inc()
                                    return None
                                response.raise_for_status()
                                data = await response.json()
                                break
                        except asyncio.TimeoutError:
                            logging.warning(f"Таймаут при получении погоды (попытка {attempt+1})")
                            RETRIES_TOTAL.labels(component="weather").inc()
                        except aiohttp.ClientResponseError as e:
                            if e.status in (401, 403):
                                # Handle auth error as above
                                pass
                            logging.error(f"HTTP error: {e}")
                            ERRORS_TOTAL.labels(source="weather").inc()
                            return None
                        except Exception as e:
                            # На практике aiohttp.raise_for_status поднимает ClientResponseError (наследник ClientError),
                            # но для устойчивости обработаем и общий Exception из тестов/моков.
                            logging.error(f"Неожиданная ошибка при запросе погоды: {e}")
                            ERRORS_TOTAL.labels(source="weather").inc()
                            return None
                    else:
                        logging.error("Все попытки получить прогноз погоды завершились неудачей.")
                        ERRORS_TOTAL.labels(source="weather").inc()
                        return None
            except Exception as e:
                logging.error(f"Критическая ошибка при обращении к погодному API: {e}")
                ERRORS_TOTAL.labels(source="weather").inc()
                return None

            self._cache[raw_cache_key] = {
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
                "data": data,
            }

        closest_forecast_item = None
        min_diff = timedelta(days=999)

        for forecast_item in data.get("list", []):
            forecast_utc_dt = datetime.fromtimestamp(forecast_item["dt"], tz=timezone.utc)  # OWM время всегда в UTC

            # Ищем прогноз, который находится в том же 3-часовом блоке, что и target_datetime
            # Или ближайший в будущем, если target_datetime очень близко к границе блока
            if forecast_utc_dt.date() == target_utc_dt.date() and (forecast_utc_dt.hour // 3) * 3 == target_hour_rounded:
                closest_forecast_item = forecast_item
                break  # Нашли точный блок, дальше не ищем

            # Если не нашли точный блок, ищем ближайший по времени
            time_diff = abs(target_utc_dt - forecast_utc_dt)
            if time_diff < min_diff:
                closest_forecast_item = forecast_item
                min_diff = time_diff

        if closest_forecast_item:
            main_block = closest_forecast_item.get("main", {})
            weather_block = closest_forecast_item.get("weather", [{}])[0]
            weather_description = weather_block.get("description", "неизвестно")
            temp = self._safe_number(main_block.get("temp"))
            feels_like = self._safe_number(main_block.get("feels_like"))
            humidity = self._safe_number(main_block.get("humidity"))
            wind_speed = self._safe_number(closest_forecast_item.get("wind", {}).get("speed"))
            pop_raw = closest_forecast_item.get("pop", 0)
            try:
                pop = max(0, min(100, round(float(pop_raw) * 100)))
            except (TypeError, ValueError):
                pop = 0

            icon_code = weather_block.get("icon")
            emoji = self._get_weather_emoji(icon_code)
            precip_kind = self._get_precip_kind(icon_code)

            result_data = {
                "temperature": temp,
                "feels_like": feels_like if feels_like != "N/A" else temp,
                "description": weather_description,
                "emoji": emoji,
                "humidity": humidity,
                "wind_speed": wind_speed,
                "pop": pop,
                "precip_kind": precip_kind,
                "forecast_time": datetime.fromtimestamp(closest_forecast_item["dt"], tz=MOSCOW_TZ).strftime("%H:%M"),
            }
            self._cache[cache_key] = {
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
                "data": result_data,
            }
            return result_data
        return None

    async def get_day_weather_window(
        self,
        target_date: Any,
        start_hour: int = 9,
        end_hour: int = 18,
        lang: str = "ru",
    ) -> Optional[Dict[str, Any]]:
        """Возвращает погодную сводку на учебное окно (начало и конец пар)."""
        if not self.api_key:
            return None

        start_dt = MOSCOW_TZ.localize(
            datetime(target_date.year, target_date.month, target_date.day, max(0, min(23, start_hour)), 0)
        )
        end_hour_clamped = max(start_hour, min(23, end_hour))
        end_dt = MOSCOW_TZ.localize(datetime(target_date.year, target_date.month, target_date.day, end_hour_clamped, 0))

        start_forecast = await self.get_forecast_for_time(start_dt, lang=lang)
        if not start_forecast:
            return None

        slots = [start_forecast]
        end_forecast = None
        if end_hour_clamped - start_hour >= 3:
            end_forecast = await self.get_forecast_for_time(end_dt, lang=lang)
            if end_forecast:
                slots.append(end_forecast)

        temps = [s["temperature"] for s in slots if isinstance(s.get("temperature"), (int, float))]
        feels = [s["feels_like"] for s in slots if isinstance(s.get("feels_like"), (int, float))]
        winds = [s["wind_speed"] for s in slots if isinstance(s.get("wind_speed"), (int, float))]
        pops = [int(s.get("pop", 0) or 0) for s in slots]

        precip_priority = {"storm": 3, "snow": 2, "rain": 1}
        precip_kind = None
        best_prio = 0
        for s in slots:
            kind = s.get("precip_kind")
            prio = precip_priority.get(kind or "", 0)
            if prio > best_prio:
                best_prio = prio
                precip_kind = kind

        return {
            "start": start_forecast,
            "end": end_forecast,
            "temperature": start_forecast.get("temperature", "N/A"),
            "feels_like": start_forecast.get("feels_like", start_forecast.get("temperature", "N/A")),
            "description": start_forecast.get("description", ""),
            "emoji": start_forecast.get("emoji", "❓"),
            "temp_min": min(temps) if temps else start_forecast.get("temperature", "N/A"),
            "temp_max": max(temps) if temps else start_forecast.get("temperature", "N/A"),
            "feels_like_min": min(feels) if feels else start_forecast.get("feels_like", "N/A"),
            "wind_speed": max(winds) if winds else start_forecast.get("wind_speed", "N/A"),
            "max_pop": max(pops) if pops else 0,
            "precip_kind": precip_kind,
        }

    @staticmethod
    def _get_precip_kind(icon_code: str | None) -> str | None:
        if not icon_code:
            return None
        if icon_code.startswith("11"):
            return "storm"
        if icon_code.startswith("13"):
            return "snow"
        if icon_code.startswith(("09", "10")):
            return "rain"
        return None

    @staticmethod
    def _safe_number(value):
        try:
            return round(float(value))
        except (TypeError, ValueError):
            return "N/A"

    def _get_weather_emoji(self, icon_code: str) -> str:
        """Преобразует код иконки OpenWeatherMap в эмодзи."""
        if not icon_code:
            return "❓"
        if icon_code.startswith("01"):
            return "☀️"  # Clear sky
        if icon_code.startswith("02"):
            return "🌤️"  # Few clouds
        if icon_code.startswith("03") or icon_code.startswith("04"):
            return "☁️"  # Scattered / Broken clouds
        if icon_code.startswith("09") or icon_code.startswith("10"):
            return "🌧️"  # Shower rain / Rain
        if icon_code.startswith("11"):
            return "⛈️"  # Thunderstorm
        if icon_code.startswith("13"):
            return "🌨️"  # Snow
        if icon_code.startswith("50"):
            return "🌫️"  # Mist / Fog
        return "❓"  # Unknown
