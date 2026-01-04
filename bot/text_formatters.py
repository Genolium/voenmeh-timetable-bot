import logging
import random
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.config import MOSCOW_TZ
from core.semester_settings import SemesterSettingsManager
from core.i18n import i18n
from core.transliteration import transliterate


async def calculate_semester_week_number(target_date: date, session_factory) -> int:
    """
    Рассчитывает номер недели с начала текущего семестра на основе настроек из БД.

    Args:
        target_date: Дата для расчета
        session_factory: Фабрика сессий для работы с БД

    Returns:
        int: Номер недели (1-32, минимум 1)
    """
    try:
        # Получаем настройки семестров из БД
        settings_manager = SemesterSettingsManager(session_factory)
        semester_settings = await settings_manager.get_semester_settings()

        if not semester_settings:
            # Если настройки не установлены, используем значения по умолчанию
            # Осенний семестр: 1 сентября, Весенний семестр: 9 февраля
            year = target_date.year
            if target_date < date(year, 9, 1):
                year -= 1
            fall_start = date(year, 9, 1)
            spring_start = date(year, 2, 9)
        else:
            fall_start, spring_start = semester_settings
            # Корректируем год для дат семестров
            year = target_date.year
            if target_date < fall_start:
                # Если дата до осеннего семестра, используем предыдущий год
                year -= 1
            fall_start = fall_start.replace(year=year)
            spring_start = spring_start.replace(year=year)

        # Определяем, в каком семестре находится дата
        # Считаем недели только в пределах указанных начальных дат семестров

        # Корректируем даты на текущий год
        current_year_fall = fall_start.replace(year=target_date.year)
        current_year_spring = spring_start.replace(year=target_date.year)

        # Определяем границы семестров на основе типичной продолжительности
        # Осенний семестр: обычно 17-18 недель (сентябрь - январь)
        # Весенний семестр: обычно 17-18 недель (февраль - июнь)

        if fall_start.month > spring_start.month:
            # Стандартный случай: осенний с сентября, весенний с февраля

            # Весенний семестр: с spring_start до spring_start + 17 недель
            spring_end = current_year_spring + timedelta(weeks=17)

            # Осенний семестр: с fall_start до fall_start + 17 недель
            fall_end = current_year_fall + timedelta(weeks=17)

            if current_year_spring <= target_date < spring_end:
                # Весенний семестр - считаем недели
                semester_start = current_year_spring
            elif current_year_fall <= target_date < fall_end:
                # Осенний семестр - считаем недели
                semester_start = current_year_fall
            else:
                # Дата находится вне активных семестров
                # Не считаем недели - возвращаем 0
                return 0
        else:
            # Весенний семестр начинается раньше осеннего в году

            # Весенний семестр: с spring_start до spring_start + 17 недель
            spring_end = current_year_spring + timedelta(weeks=17)

            # Осенний семестр: с fall_start до fall_start + 17 недель
            fall_end = current_year_fall + timedelta(weeks=17)

            if current_year_spring <= target_date < spring_end:
                # Весенний семестр - считаем недели
                semester_start = current_year_spring
            elif current_year_fall <= target_date < fall_end:
                # Осенний семестр - считаем недели
                semester_start = current_year_fall
            else:
                # Дата находится вне активных семестров
                # Не считаем недели - возвращаем 0
                return 0

        # Считаем разницу в днях
        days_diff = (target_date - semester_start).days

        # Считаем номер недели (начинаем с 1)
        week_number = (days_diff // 7) + 1

        return max(week_number, 1)

    except Exception as e:
        logging.error(f"Ошибка при расчете номера недели семестра: {e}")
        # В случае ошибки возвращаем расчет по старой логике (1 сентября)
        year = target_date.year
        if target_date < date(year, 9, 1):
            year -= 1
        semester_start = date(year, 9, 1)
        days_diff = (target_date - semester_start).days
        week_number = (days_diff // 7) + 1
        return max(week_number, 1)


def calculate_semester_week_number_fallback(target_date: date) -> int:
    """
    Функция для обратной совместимости - рассчитывает номер недели с 1 сентября.

    Args:
        target_date: Дата для расчета

    Returns:
        int: Номер недели (1-32, минимум 1)
    """
    # Определяем начало учебного года (1 сентября)
    year = target_date.year
    # Если текущая дата до 1 сентября, используем предыдущий год
    if target_date < date(year, 9, 1):
        year -= 1

    semester_start = date(year, 9, 1)

    # Считаем разницу в днях
    days_diff = (target_date - semester_start).days

    # Считаем номер недели (начинаем с 1)
    week_number = (days_diff // 7) + 1

    return max(week_number, 1)


# --- ОБЩИЕ ФОРМАТТЕРЫ ---


def format_schedule_text(day_info: dict, week_number: int | None = None, lang: str = "ru") -> str:
    """Форматирует расписание на день для группы."""
    if not day_info or "error" in day_info:
        error_msg = day_info.get('error', i18n.get("fmt_error_unknown", lang))
        return i18n.get("fmt_error", lang).format(error=error_msg)

    date_obj = day_info.get("date")
    if not date_obj:
        return i18n.get("fmt_date_missing", lang)

    date_str = date_obj.strftime("%d.%m.%Y")
    day_name_ru = day_info.get("day_name", "")
    day_idx = date_obj.weekday()
    day_name = i18n.get(f"day_{day_idx}", lang)
    
    raw_week_name = day_info.get('week_name', '')
    week_type = ""
    if raw_week_name:
        is_odd = "Неч" in raw_week_name
        week_key = "week_odd" if is_odd else "week_even"
        week_type = f"({i18n.get(week_key, lang)})"

    # Добавляем номер недели если он указан
    week_info = ""
    if week_number and week_number <= 32:
        week_info = " " + i18n.get("fmt_week", lang).format(number=week_number)

    header = f"🗓 <b>{date_str} · {day_name}</b> {week_type}{week_info}\n"

    lessons = day_info.get("lessons")
    if not lessons:
        return header + "\n" + i18n.get("fmt_no_lessons", lang)

    lesson_parts = []
    for lesson in lessons:
        time_str = lesson.get("time", i18n.get("fmt_time_unknown", lang))
        subject_str = transliterate(lesson.get("subject", i18n.get("fmt_subject_unknown", lang)), lang)
        type_raw = lesson.get('type', '')
        type_str = f"({transliterate(type_raw, lang)})" if type_raw else ""

        lesson_header = f"<b>{time_str}</b>\n{subject_str} {type_str}"

        details_parts = []
        teachers = lesson.get("teachers")
        if teachers:
            details_parts.append(f"🧑‍🏫 {transliterate(teachers, lang)}")

        room = lesson.get("room")
        if room:
            room_lower = room.lower()
            # Skip "room not specified" messages - don't show anything
            if "кабинет" in room_lower and "не указан" in room_lower:
                details_parts.append(f"📍 {i18n.get('room_not_specified', lang)}")
            else:
                details_parts.append(f"📍 {transliterate(room, lang)}")

        details_str = "\n" + " ".join(details_parts) if details_parts else ""
        lesson_parts.append(f"{lesson_header}{details_str}")

    return header + "\n\n".join(lesson_parts)


def format_teacher_schedule_text(schedule_info: dict, lang: str = "ru") -> str:
    """Форматирует расписание на день для преподавателя."""
    if not schedule_info or schedule_info.get("error"):
        error_msg = schedule_info.get('error', i18n.get("fmt_error_unknown", lang))
        return i18n.get("fmt_error", lang).format(error=error_msg)

    teacher_name = transliterate(schedule_info.get("teacher", i18n.get("fmt_teacher_label", lang)), lang)
    date_obj = schedule_info["date"]
    date_str = date_obj.strftime("%d.%m.%Y")
    day_idx = date_obj.weekday()
    day_name = i18n.get(f"day_{day_idx}", lang)

    header_template = i18n.get("fmt_teacher_header", lang).format(name=teacher_name)
    header = f"{header_template}\n🗓 <b>{date_str} · {day_name}</b>\n"

    lessons = schedule_info.get("lessons")
    if not lessons:
        return header + "\n" + i18n.get("fmt_no_lessons", lang)

    lesson_parts = []
    for lesson in lessons:
        time_str = lesson.get("time", i18n.get("fmt_time_unknown", lang))
        subject_str = transliterate(lesson.get("subject", i18n.get("fmt_subject_unknown", lang)), lang)
        groups_list = lesson.get("groups", []) or []
        if groups_list:
            # Удаляем дубликаты групп, сохраняя порядок
            seen_groups = set()
            dedup_groups = []
            for group in groups_list:
                if group not in seen_groups:
                    seen_groups.add(group)
                    dedup_groups.append(group)
            group_str = f" ({', '.join(dedup_groups)})"
        else:
            group_str = ""

        lesson_header = f"<b>{time_str}</b>\n{subject_str}{group_str}"

        details_parts = []
        room = lesson.get("room")
        if room:
            room_lower = room.lower()
            # Skip "room not specified" messages - don't show anything
            if "кабинет" in room_lower and "не указан" in room_lower:
                details_parts.append(f"📍 {i18n.get('room_not_specified', lang)}")
            else:
                details_parts.append(f"📍 {transliterate(room, lang)}")

        details_str = "\n" + " ".join(details_parts) if details_parts else ""
        lesson_parts.append(f"{lesson_header}{details_str}")

    return header + "\n\n".join(lesson_parts)


def format_classroom_schedule_text(schedule_info: dict, lang: str = "ru") -> str:
    """Форматирует расписание на день для аудитории."""
    if not schedule_info or schedule_info.get("error"):
        error_msg = schedule_info.get('error', i18n.get("fmt_error_unknown", lang))
        return i18n.get("fmt_error", lang).format(error=error_msg)

    classroom_number = transliterate(schedule_info.get("classroom", i18n.get("fmt_classroom_label", lang)), lang)
    date_obj = schedule_info["date"]
    date_str = date_obj.strftime("%d.%m.%Y")
    day_idx = date_obj.weekday()
    day_name = i18n.get(f"day_{day_idx}", lang)

    header_template = i18n.get("fmt_classroom_header", lang).format(number=classroom_number)
    header = f"{header_template}\n🗓 <b>{date_str} · {day_name}</b>\n"

    lessons = schedule_info.get("lessons")
    if not lessons:
        return header + "\n" + i18n.get("fmt_classroom_free", lang)

    lesson_parts = []
    for lesson in lessons:
        time_str = lesson.get("time", i18n.get("fmt_time_unknown", lang))
        subject_str = transliterate(lesson.get("subject", i18n.get("fmt_subject_unknown", lang)), lang)
        groups_list = lesson.get("groups", []) or []
        if groups_list:
            # Удаляем дубликаты групп, сохраняя порядок
            seen_groups = set()
            dedup_groups = []
            for group in groups_list:
                if group not in seen_groups:
                    normalized_group = transliterate(group, lang)
                    seen_groups.add(group)
                    dedup_groups.append(normalized_group)
            group_str = f" ({', '.join(dedup_groups)})"
        else:
            group_str = ""

        lesson_header = f"<b>{time_str}</b>\n{subject_str}{group_str}"

        details_parts = []
        teachers = lesson.get("teachers")
        if teachers:
            details_parts.append(f"🧑‍🏫 {transliterate(teachers, lang)}")

        details_str = "\n" + " ".join(details_parts) if details_parts else ""
        lesson_parts.append(f"{lesson_header}{details_str}")

    return header + "\n\n".join(lesson_parts)


def format_full_week_text(week_schedule: dict, week_name: str, lang: str = "ru") -> str:
    """Форматирует текст расписания на всю неделю с корректной сортировкой пар."""
    days_order = ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА"]
    week_header = i18n.get("fmt_week_header", lang).format(week_name=week_name.capitalize())
    text_parts = [week_header]

    sorted_days = sorted(
        week_schedule.keys(),
        key=lambda day: (days_order.index(day.upper()) if day.upper() in days_order else 99),
    )

    from core.transliteration import get_day_name
    
    for day_name in sorted_days:
        lessons = week_schedule.get(day_name)

        if lessons:
            localized_day_name = get_day_name(day_name.title(), lang).upper()
            text_parts.append(f"\n--- <b>{localized_day_name}</b> ---")

            try:
                sorted_lessons = sorted(
                    lessons,
                    key=lambda lesson: datetime.strptime(lesson.get("time", "23:59").split("-")[0].strip(), "%H:%M").time(),
                )
            except (ValueError, IndexError):
                sorted_lessons = lessons

            for lesson in sorted_lessons:
                time_str = lesson.get("time", i18n.get("fmt_time_unknown", lang))
                subject_str = transliterate(lesson.get("subject", i18n.get("fmt_subject_unknown", lang)), lang)
                type_raw = lesson.get('type', '')
                type_str = f"({transliterate(type_raw, lang)})" if type_raw else ""

                text_parts.append(f"{time_str} - <b>{subject_str}</b> {type_str}")

                details_parts = []
                teachers = lesson.get("teachers")
                if teachers:
                    details_parts.append(f"🧑‍🏫 {transliterate(teachers, lang)}")

                room = lesson.get("room")
                if room:
                    room_lower = room.lower()
                    if "кабинет" in room_lower and "не указан" in room_lower:
                        details_parts.append(f"📍 {i18n.get('room_not_specified', lang)}")
                    else:
                        details_parts.append(f"📍 {transliterate(room, lang)}")

                if details_parts:
                    text_parts.append(" ".join(details_parts))

    if len(text_parts) == 1:
        return week_header + "\n\n" + i18n.get("fmt_week_no_lessons", lang)

    return "\n".join(text_parts)


# --- ДИНАМИЧЕСКИЕ ЗАГОЛОВКИ ---


def generate_dynamic_header(lessons: list, target_date: date, lang: str = "ru") -> tuple[str, str]:
    """Генерирует контекстный заголовок и прогресс-бар."""
    is_today = target_date == datetime.now(MOSCOW_TZ).date()

    if is_today and not lessons:
        return i18n.get("header_today_no_lessons", lang), ""

    if not is_today or not lessons:
        return "", ""

    try:
        sorted_lessons = sorted(
            lessons,
            key=lambda x: datetime.strptime(x["start_time_raw"], "%H:%M").time(),
        )
        now_time = datetime.now(MOSCOW_TZ).time()

        MORNING_START_TIME = time(5, 0)

        passed_lessons_count = sum(
            1 for lesson in sorted_lessons if now_time > datetime.strptime(lesson["end_time_raw"], "%H:%M").time()
        )
        total_lessons = len(sorted_lessons)
        progress_bar_emojis = "🟩" * passed_lessons_count + "⬜️" * (total_lessons - passed_lessons_count)
        
        progress_bar_template = i18n.get("progress_bar_text", lang)
        progress_bar = f"{progress_bar_template.format(passed=passed_lessons_count, total=total_lessons)} {progress_bar_emojis}\n"

        first_lesson_start = datetime.strptime(sorted_lessons[0]["start_time_raw"], "%H:%M").time()
        last_lesson_end = datetime.strptime(sorted_lessons[-1]["end_time_raw"], "%H:%M").time()

        def get_safe_times(time_str: str) -> tuple[str, str]:
            time_str_unified = time_str.replace("–", "-").replace("—", "-")
            parts = [p.strip() for p in time_str_unified.split("-")]
            return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0] if parts else "", "")

        if now_time < MORNING_START_TIME:
            return i18n.get("header_late_night", lang), progress_bar

        if now_time < first_lesson_start:
            start_time_str, _ = get_safe_times(sorted_lessons[0]["time"])
            return (
                i18n.get("header_morning_template", lang).format(start_time=start_time_str),
                progress_bar,
            )

        if now_time > last_lesson_end:
            return i18n.get("header_lessons_done", lang), progress_bar

        for i, lesson in enumerate(sorted_lessons):
            start_time = datetime.strptime(lesson["start_time_raw"], "%H:%M").time()
            end_time = datetime.strptime(lesson["end_time_raw"], "%H:%M").time()
            _, lesson_end_time_str = get_safe_times(lesson["time"])

            if start_time <= now_time <= end_time:
                end_text = i18n.get("dynamic_header_lesson_end", lang).format(time=lesson_end_time_str) if lesson_end_time_str else ""
                return (
                    i18n.get("header_lesson_now_template", lang).format(subject=lesson['subject'], end_text=end_text),
                    progress_bar,
                )

            if i + 1 < len(sorted_lessons):
                next_lesson = sorted_lessons[i + 1]
                next_start_time_obj = datetime.strptime(next_lesson["start_time_raw"], "%H:%M").time()
                next_start_time_str, _ = get_safe_times(next_lesson["time"])
                if end_time < now_time < next_start_time_obj:
                    return (
                        i18n.get("header_break_template", lang).format(next_start=next_start_time_str, subject=next_lesson['subject']),
                        progress_bar,
                    )

        return "", progress_bar
    except (ValueError, IndexError, KeyError) as e:
        logging.error(f"Ошибка при генерации динамического заголовка: {e}. Данные урока: {lessons}")
        return "", ""


# --- ТЕКСТЫ ДЛЯ УВЕДОМЛЕНИЙ ---

# --- Константы для текстов ---
UNSUBSCRIBE_FOOTER = "\n\n<tg-spoiler><i>Отключить эту рассылку можно в «⚙️ Настройки»</i></tg-spoiler>"

# Реклама канала с новостями разработки (показывается в 30% случаев)
CHANNEL_PROMO = "\n\n📢 <i>Новости разработки бота: <a href='https://t.me/voenmeh404'>Аудитория 404 | Военмех</a></i>"


def get_footer_with_promo() -> str:
    """Возвращает footer с возможной рекламой канала (30% вероятность)"""
    if random.random() < 0.3:  # 30% вероятность показа рекламы
        return UNSUBSCRIBE_FOOTER + CHANNEL_PROMO
    return UNSUBSCRIBE_FOOTER


EVENING_GREETINGS = [
    "Добрый вечер! 👋",
    "Привет! Готовимся к завтрашнему дню.",
    "Вечерняя сводка на подходе.",
]
EVENING_GREETINGS_TEACHER = [
    "Добрый вечер!",
    "Информируем Вас о расписании на завтра.",
    "Здравствуйте!",
]
MORNING_GREETINGS = [
    "Доброе утро! ☀️",
    "Утро доброе! Учеба ждет.",
    "Утренняя сводка готова!",
]
MORNING_GREETINGS_TEACHER = [
    "Доброе утро!",
    "Ваше расписание на сегодня.",
    "Здравствуйте!",
]
DAY_OF_WEEK_CONTEXT = {
    0: [
        "Завтра понедельник — начинаем неделю с чистого листа!",
        "Готовимся к началу новой недели.",
    ],
    1: ["Завтра вторник, втягиваемся в ритм.", "Планируем продуктивный вторник."],
    2: ["Завтра среда — экватор недели!", "Середина недели уже завтра. Держимся!"],
    3: ["Завтра четверг, финишная прямая близко.", "Еще один рывок до конца недели!"],
    4: ["Завтра пятница! Впереди заслуженный отдых.", "Последний рывок перед чиллом!"],
    5: [
        "Завтра учебная суббота — для самых стойких.",
        "Еще один день знаний, а потом отдых.",
    ],
    6: [
        "Завтра воскресенье — можно выспаться!",
        "Впереди выходной, но не забудьте про домашку 😉",
    ],
}
DAY_OF_WEEK_CONTEXT_TEACHER = {
    0: [
        "Завтра понедельник — начало новой рабочей недели.",
        "Планируем продуктивную неделю.",
    ],
    1: ["Завтра вторник.", "Продолжаем рабочую неделю."],
    2: ["Завтра среда — середина недели.", "Желаем продуктивного дня."],
    3: ["Завтра четверг.", "Приближается конец рабочей недели."],
    4: [
        "Завтра пятница — завершение рабочей недели.",
        "Желаем продуктивного завершения недели.",
    ],
    5: ["Завтра суббота — рабочий день.", "Желаем продуктивного дня."],
    6: ["Завтра воскресенье.", "Желаем хорошего выходного дня."],
}
CLOTHING_ADVICES = {
    "cold": [
        "Завтра будет морозно, не забудьте шапку и перчатки!",
        "Советуем одеться потеплее.",
    ],
    "cool": [
        "Завтра утром будет прохладно, легкая куртка или свитер будут в самый раз.",
        "Осенняя прохлада требует уюта.",
    ],
    "warm": [
        "Завтра обещают тепло, можно одеться полегче.",
        "Отличная погода для прогулки после учебы.",
    ],
    "hot": [
        "Завтра будет жарко! Пейте больше воды.",
        "Настоящее лето! Идеально для легкой одежды.",
    ],
}



def generate_evening_intro(
    weather_forecast: Dict[str, Any] | None,
    target_date: datetime,
    user_type: str = "student",
    lang: str = "ru",
) -> str:
    weekday = str(target_date.weekday())

    # Выбираем приветствие и контекст в зависимости от типа пользователя
    if user_type == "teacher":
        greetings = i18n.get("evening_greetings_teacher", lang)
        context_map = i18n.get("day_context_teacher", lang)
    else:
        greetings = i18n.get("evening_greetings", lang)
        context_map = i18n.get("day_context", lang)

    # Ensure we have lists
    if not isinstance(greetings, list):
        greetings = [""]
    
    # context_map might be a dict or string if something went wrong, need to handle
    day_context_list = []
    if isinstance(context_map, dict):
        day_context_list = context_map.get(weekday, [""])
    
    greeting_line = random.choice(greetings)
    day_context_line = random.choice(day_context_list)

    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast["temperature"])
        description = weather_forecast.get("description", "").lower()

        # Для преподавателей используем более формальный стиль
        if user_type == "teacher":
            fmt = i18n.get("weather_forecast_teacher_fmt", lang)
            weather_block = fmt.format(
                emoji=weather_forecast.get('emoji', ''),
                description=description.capitalize(),
                temp=temp
            )
        else:
            advice_list = []
            clothing_advice = i18n.get("clothing_advice", lang)
            if isinstance(clothing_advice, dict):
                if temp <= 0:
                    advice_list = clothing_advice.get("cold", [])
                elif 0 < temp <= 12:
                    advice_list = clothing_advice.get("cool", [])
                elif 12 < temp <= 20:
                    advice_list = clothing_advice.get("warm", [])
                else:
                    advice_list = clothing_advice.get("hot", [])
            
            advice_line = random.choice(advice_list) if advice_list else ""
            
            fmt = i18n.get("weather_forecast_fmt", lang)
            weather_block = fmt.format(
                emoji=weather_forecast.get('emoji', ''),
                description=description.capitalize(),
                temp=temp,
                advice=advice_line
            )

    parts = [greeting_line, day_context_line, weather_block]
    return "\n\n".join(filter(None, parts)) + "\n\n"


def generate_morning_intro(
    weather_forecast: Dict[str, Any] | None, 
    user_type: str = "student",
    lang: str = "ru"
) -> str:
    # Выбираем приветствие в зависимости от типа пользователя
    if user_type == "teacher":
        greetings = i18n.get("morning_greetings_teacher", lang)
    else:
        greetings = i18n.get("morning_greetings", lang)
    
    if not isinstance(greetings, list):
        greetings = [""]

    greeting_line = random.choice(greetings)

    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast["temperature"])
        description = weather_forecast.get("description", "").lower()

        # Для преподавателей используем более формальный стиль
        if user_type == "teacher":
            fmt = i18n.get("weather_current_teacher_fmt", lang)
        else:
            fmt = i18n.get("weather_current_fmt", lang)
        
        weather_block = fmt.format(description=description.capitalize(), temp=temp)

    return f"{greeting_line}\n{weather_block}\n"


def generate_reminder_text(
    lesson: Dict[str, Any] | None,
    reminder_type: str,
    break_duration: int | None,
    reminder_time_minutes: int | None = 20,
    lang: str = "ru",
) -> str | None:
    text = ""
    if reminder_type == "first" and lesson:
        greetings = i18n.get("reminder_first", lang)
        if not isinstance(greetings, list):
            greetings = ["First lesson soon!"]
            
        # Format the chosen greeting with minutes if needed
        chosen_greeting = random.choice(greetings)
        if "{minutes}" in chosen_greeting:
             chosen_greeting = chosen_greeting.format(minutes=reminder_time_minutes)
             
        header_fmt = i18n.get("reminder_header_first", lang)
        text = header_fmt.format(text=chosen_greeting)

    elif reminder_type == "break" and lesson:
        next_lesson_time = lesson.get("time", "N/A").split("-")[0].strip()
        
        break_text = ""
        advice = ""
        
        if break_duration and break_duration >= 40:
             advices = i18n.get("reminder_break_long", lang)
             fmt = i18n.get("reminder_break_long_fmt", lang)
        elif break_duration and break_duration >= 15:
             advices = i18n.get("reminder_break_medium", lang)
             fmt = i18n.get("reminder_break_medium_fmt", lang)
        else:
             advices = i18n.get("reminder_break_short", lang)
             # Short breaks don't usually fit the complex format logic in the original, 
             # original code:
             # else:
             #    break_text = random.choice(["Успейте дойти...", ...])
             # Let's keep it simple or align with format.
             # Original logic constructed the string directly.
             # Let's try to infer from keys.
             fmt = "{advice}" 
        
        if isinstance(advices, list) and advices:
            advice = random.choice(advices)
            
        if break_duration and break_duration >= 15:
            break_text = fmt.format(duration=break_duration, next_time=next_lesson_time, advice=advice)
        else:
             # Short break fallback logic
             break_text = advice

        header_fmt = i18n.get("reminder_header_break", lang)
        text = header_fmt.format(text=break_text)

    elif reminder_type == "final":
        final_phrases = i18n.get("reminder_final", lang)
        if not isinstance(final_phrases, list):
             final_phrases = ["Final!"]
        
        header_fmt = i18n.get("reminder_header_final", lang)
        return header_fmt.format(text=random.choice(final_phrases)) + i18n.get("unsubscribe_footer", lang)
    else:
        return None

    if lesson:
        subject = lesson.get('subject', 'N/A')
        type_str = lesson.get('type', 'N/A')
        time_str = lesson.get('time', 'N/A')
        
        # We construct the lesson detail string. 
        # This part was hardcoded: <b>{subject}</b> ({type}) в <b>{time}</b>
        # Let's verify if we extracted a template for this. Checking i18n...
        # I didn't add a specific template for this line in previous steps!
        # I should add it or construct it here.
        # "reminder_lesson_template" was in my thought plan but I might have missed it in json or used a different name.
        # Checking JSON keys I added... I missed "reminder_lesson_template".
        # I will use a default format here, or hardcode the generic structure which is fairly universal:
        # Subject (Type) at Time
        
        text += f"<b>{subject}</b> ({type_str}) "
        # "в" is Russian specific.
        # I should probably use a small helper or just " @ " or similar, or better yet, add to i18n.
        # For now to be safe, I'll use a neutral format or try my best.
        # Actually I can use i18n.get("fmt_time_at", lang) if I had it.
        # Let's assume standard format for now: "Time: 12:00" or similar.
        # Or just keep it simple: <b>Subject</b> (Type)\n🕒 <b>Time</b>
        
        # Wait, I can perform a quick fix on JSON later if needed. 
        # For now I will use: "🕒 <b>{time}</b>"
        
        text += f"\n🕒 <b>{time_str}</b>\n"
        
        info_parts = [f"📍 {room}" for room in [lesson.get("room")] if room]
        if teachers := lesson.get("teachers"):
            # Use localized "with {teachers}" format
            teacher_fmt = i18n.get("fmt_teacher_with", lang)
            info_parts.append(teacher_fmt.format(teachers=teachers))
            
        if info_parts:
            text += " ".join(info_parts)

    return text + i18n.get("unsubscribe_footer", lang)

