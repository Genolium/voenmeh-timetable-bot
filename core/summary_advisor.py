"""Умный советник для утренних и вечерних сводок (погода под окно пар, сезонные советы, нагрузка дня)."""

from __future__ import annotations

import html
from datetime import date, datetime, time
from typing import Any


def _parse_hhmm(raw: str) -> time | None:
    try:
        return datetime.strptime(raw.strip(), "%H:%M").time()
    except (ValueError, AttributeError):
        return None


def analyze_lessons_window(lessons: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Извлекает окно присутствия (начало первой — конец последней пары) и плотность дня."""
    if not lessons:
        return {
            "count": 0,
            "first_start": "",
            "last_end": "",
            "start_hour": 10,
            "end_hour": 18,
            "max_gap_minutes": 0,
            "is_dense": False,
        }

    parsed: list[tuple[time, time, str, str]] = []
    for lesson in lessons:
        s_raw = str(lesson.get("start_time_raw") or "").strip()
        e_raw = str(lesson.get("end_time_raw") or "").strip()
        if (not s_raw or not e_raw) and lesson.get("time"):
            parts = [p.strip() for p in str(lesson["time"]).replace("–", "-").replace("—", "-").split("-")]
            if len(parts) >= 2:
                s_raw, e_raw = parts[0], parts[1]
        st = _parse_hhmm(s_raw)
        et = _parse_hhmm(e_raw)
        if st and et:
            parsed.append((st, et, s_raw, e_raw))

    if not parsed:
        return {
            "count": len(lessons),
            "first_start": "",
            "last_end": "",
            "start_hour": 9,
            "end_hour": 18,
            "max_gap_minutes": 0,
            "is_dense": False,
        }

    parsed.sort(key=lambda item: (item[0], item[1]))
    max_gap = 0
    running_end = parsed[0][1]
    last_et, last_raw = parsed[0][1], parsed[0][3]
    for idx in range(1, len(parsed)):
        start_next, end_next, _, end_next_raw = parsed[idx]
        gap = (start_next.hour * 60 + start_next.minute) - (running_end.hour * 60 + running_end.minute)
        if gap > max_gap:
            max_gap = gap
        if end_next > running_end:
            running_end = end_next
            last_et, last_raw = end_next, end_next_raw

    first_st, _, first_raw, _ = parsed[0]
    return {
        "count": len(parsed),
        "first_start": first_raw,
        "last_end": last_raw,
        "start_hour": first_st.hour,
        "end_hour": min(23, last_et.hour + (1 if last_et.minute >= 30 else 0)),
        "max_gap_minutes": max_gap,
        "is_dense": len(parsed) >= 3 and max_gap <= 25,
    }


def _plural_lessons(count: int, lang: str) -> str:
    if lang == "en":
        return "class" if count == 1 else "classes"
    if lang == "zh":
        return "节课"
    mod10, mod100 = count % 10, count % 100
    if mod10 == 1 and mod100 != 11:
        return "пара"
    if 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
        return "пары"
    return "пар"


def _format_duration(minutes: int, lang: str) -> str:
    hours, mins = divmod(minutes, 60)
    if lang == "en":
        return f"{hours}h {mins}m" if hours else f"{mins}m"
    if lang == "zh":
        return f"{hours}小时{mins}分" if hours else f"{mins}分钟"
    if hours and mins:
        return f"{hours} ч {mins} мин"
    if hours:
        return f"{hours} ч"
    return f"{mins} мин"


def build_schedule_briefing(
    lessons: list[dict[str, Any]] | None,
    *,
    user_type: str = "student",
    lang: str = "ru",
) -> str:
    """Формирует компактный брифинг учебной нагрузки дня."""
    stats = analyze_lessons_window(lessons)
    count = stats["count"]
    if count <= 0 or not stats["first_start"]:
        return ""

    first_start = stats["first_start"]
    last_end = stats["last_end"]
    unit = _plural_lessons(count, lang)

    notes: list[str] = []
    start_t = _parse_hhmm(first_start)
    end_t = _parse_hhmm(last_end)
    if user_type != "teacher":
        if start_t and start_t >= time(12, 0):
            notes.append({"ru": "можно выспаться", "en": "late start", "zh": "可多睡一会儿"}.get(lang, "можно выспаться"))
        elif start_t and start_t <= time(9, 0):
            notes.append({"ru": "ранний подъём", "en": "early start", "zh": "早课"}.get(lang, "ранний подъём"))

    if stats["max_gap_minutes"] >= 45:
        gap_str = _format_duration(stats["max_gap_minutes"], lang)
        notes.append({"ru": f"окно {gap_str}", "en": f"{gap_str} gap", "zh": f"空堂 {gap_str}"}.get(lang, f"окно {gap_str}"))
    elif stats["is_dense"]:
        notes.append({"ru": "без окон", "en": "no gaps", "zh": "连堂"}.get(lang, "без окон"))

    if end_t and end_t >= time(19, 30):
        notes.append({"ru": "допоздна", "en": "late finish", "zh": "晚归"}.get(lang, "допоздна"))

    base = (
        f"📊 <b>{count} {unit}</b> · {first_start}–{last_end}"
        if lang != "zh"
        else f"📊 <b>{count}{unit}</b> · {first_start}–{last_end}"
    )
    if notes:
        return f"{base} <i>({', '.join(notes[:2])})</i>"
    return base


def _seasonal_temp_advice(eff_temp: int, month: int, precip_kind: str | None, user_type: str, lang: str) -> str:
    if lang == "en":
        if precip_kind == "storm":
            return "Thunderstorms expected — plan extra travel time."
        if precip_kind == "snow":
            return "Snow and slick sidewalks — wear warm non-slip shoes."
        if precip_kind == "rain":
            return "Rain likely — bring an umbrella."
        if eff_temp <= -5:
            return "Freezing cold — bundle up with a hat and gloves."
        if eff_temp <= 5:
            return "Chilly out — a warm jacket and scarf are recommended."
        if eff_temp <= 15:
            return "Cool weather — a light jacket or sweater fits well."
        if eff_temp <= 23:
            return "Comfortable weather for campus."
        return "Warm and sunny — dress lightly and stay hydrated."

    if lang == "zh":
        if precip_kind == "storm":
            return "预计有雷阵雨，出行注意安全。"
        if precip_kind == "snow":
            return "有降雪且路面可能湿滑，注意保暖防滑。"
        if precip_kind == "rain":
            return "可能有雨，建议随身携带雨伞。"
        if eff_temp <= -5:
            return "天气严寒，请注意防寒保暖，戴好帽子和手套。"
        if eff_temp <= 5:
            return "天气偏冷，建议穿厚外套或羽绒服。"
        if eff_temp <= 15:
            return "气温凉爽，穿件夹克或卫衣正合适。"
        if eff_temp <= 23:
            return "温度舒适，适合上课和出行。"
        return "天气较热，注意防晒和补充水分。"

    # Russian (сезонно-осознанные советы)
    is_cold_season = month in (11, 12, 1, 2, 3)
    if precip_kind == "storm":
        return "Ожидается гроза — лучше захватить дождевик или зонт."
    if precip_kind == "snow":
        return (
            "На улице снег и возможна гололедица — выбирайте тёплую непромокаемую обувь."
            if user_type == "teacher"
            else "Ожидается снег и скользкие тротуары — обувайтесь поустойчивее и закладывайте +10 мин на дорогу."
        )
    if precip_kind == "rain":
        return (
            "Вероятны осадки — рекомендуется взять зонт."
            if user_type == "teacher"
            else "Возможен дождь — не забудьте зонт или куртку с капюшоном."
        )
    if eff_temp <= -10:
        return (
            "Сильный мороз — одевайтесь максимально тепло."
            if user_type == "teacher"
            else "Крепкий мороз! Без шапки, шарфа и тёплых перчаток лучше не выходить."
        )
    if eff_temp <= 0:
        return (
            "Около или ниже нуля — одевайтесь по-зимнему тепло."
            if user_type == "teacher"
            else "Морозно — пуховик, шарф и тёплая обувь будут очень кстати."
        )
    if eff_temp <= 7:
        if is_cold_season:
            return (
                "Сыро и прохладно — подойдёт тёплое пальто или куртка."
                if user_type == "teacher"
                else "Зябко и сыро — тёплая куртка и шарф точно не будут лишними."
            )
        return (
            "Прохладная погода — подойдёт лёгкая куртка или плащ."
            if user_type == "teacher"
            else "Прохладная погода — ветровка или пальто будут в самый раз."
        )
    if eff_temp <= 16:
        return (
            "Свежая погода — подойдёт лёгкая верхняя одежда."
            if user_type == "teacher"
            else "Свежая погода — комфортно в худи, свитере или лёгкой куртке."
        )
    if eff_temp <= 23:
        return (
            "Комфортная погода."
            if user_type == "teacher"
            else "Комфортная температура — отличный день для прогулки после пар."
        )
    return "Жаркая погода." if user_type == "teacher" else "Жарко — одевайтесь легче и не забывайте воду."


def build_weather_summary_block(
    weather: dict[str, Any] | None,
    *,
    target_date: date,
    user_type: str = "student",
    lang: str = "ru",
    is_morning: bool = False,
) -> str:
    """Форматирует детализированный погодный блок (диапазон под пары, feels_like, ветер/осадки, совет)."""
    if not weather:
        return ""

    start_raw = weather.get("start")
    start: dict[str, Any] = start_raw if isinstance(start_raw, dict) else weather
    end_raw = weather.get("end")
    end: dict[str, Any] | None = end_raw if isinstance(end_raw, dict) else None

    temp = start.get("temperature", weather.get("temperature", "N/A"))
    feels = start.get("feels_like", weather.get("feels_like", temp))
    desc = str(start.get("description") or weather.get("description") or "").strip()
    emoji = str(start.get("emoji") or weather.get("emoji") or "🌤️")
    wind = weather.get("wind_speed", start.get("wind_speed", "N/A"))
    max_pop = int(weather.get("max_pop", start.get("pop", 0)) or 0)
    precip_kind = weather.get("precip_kind") or start.get("precip_kind")

    temp_str = f"{temp}°C" if temp != "N/A" else ""
    if end and isinstance(end.get("temperature"), (int, float)) and isinstance(temp, (int, float)):
        end_temp = end["temperature"]
        if abs(end_temp - temp) >= 2:
            temp_str = f"{temp}°C → {end_temp}°C"

    details: list[str] = []
    if isinstance(feels, (int, float)) and isinstance(temp, (int, float)) and feels <= temp - 3:
        lbl = {"ru": f"ощущается как {feels}°C", "en": f"feels like {feels}°C", "zh": f"体感 {feels}°C"}.get(
            lang, f"ощущается как {feels}°C"
        )
        details.append(lbl)
    if isinstance(wind, (int, float)) and wind >= 8:
        w_lbl = {"ru": f"ветер до {wind} м/с", "en": f"wind {wind} m/s", "zh": f"风速 {wind} m/s"}.get(
            lang, f"ветер до {wind} м/с"
        )
        details.append(w_lbl)
    if max_pop >= 45:
        p_lbl = {"ru": f"осадки {max_pop}%", "en": f"precip {max_pop}%", "zh": f"降水概率 {max_pop}%"}.get(
            lang, f"осадки {max_pop}%"
        )
        details.append(p_lbl)

    if lang == "en":
        prefix = "Weather today" if is_morning else "Forecast for tomorrow"
    elif lang == "zh":
        prefix = "今日天气" if is_morning else "明日天气"
    else:
        prefix = "Погода на день" if is_morning else "Прогноз на завтра"

    desc_cap = html.escape(desc[:1].upper() + desc[1:], quote=False) if desc else ""
    safe_emoji = html.escape(emoji, quote=False)
    summary_parts = [part for part in (desc_cap, temp_str) if part]
    main_line = f"{safe_emoji} {prefix}: {', '.join(summary_parts)}" if summary_parts else f"{safe_emoji} {prefix}"
    if details:
        main_line += f" ({', '.join(details)})"

    eff_temp = feels if isinstance(feels, (int, float)) else (temp if isinstance(temp, (int, float)) else 10)
    if precip_kind is None and max_pop >= 60:
        precip_kind = "snow" if eff_temp <= 1 else "rain"

    advice = _seasonal_temp_advice(int(eff_temp), target_date.month, precip_kind, user_type, lang)
    return f"{main_line}\n<i>{advice}</i>"
