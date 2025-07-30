import logging
import random
from datetime import datetime, time, date
from typing import Dict, Any

from core.config import MOSCOW_TZ

# --- ОБЩИЕ ФОРМАТТЕРЫ ---

def format_schedule_text(day_info: dict) -> str:
    """Форматирует расписание на день для группы."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_obj = day_info.get('date')
    if not date_obj:
        return "❌ <b>Ошибка:</b> Дата не найдена в данных расписания."

    date_str = date_obj.strftime('%d.%m.%Y')
    day_name = day_info.get('day_name', '')
    week_type = f"({day_info.get('week_name', '')})" if day_info.get('week_name') else ""

    header = f"🗓 <b>{date_str} · {day_name}</b> {week_type}\n"
    
    lessons = day_info.get('lessons')
    if not lessons:
        return header + "\n🎉 <b>Занятий нет!</b>"

    lesson_parts = []
    for lesson in lessons:
        time_str = lesson.get('time', 'Время не указано')
        subject_str = lesson.get('subject', 'Предмет не указан')
        type_str = f"({lesson.get('type', '')})" if lesson.get('type') else ""
        
        lesson_header = f"<b>{time_str}</b>\n{subject_str} {type_str}"
        
        details_parts = []
        teachers = lesson.get('teachers')
        if teachers:
            details_parts.append(f"🧑‍🏫 {teachers}")
        
        room = lesson.get('room')
        if room:
            details_parts.append(f"📍 {room}")
        
        details_str = "\n" + " ".join(details_parts) if details_parts else ""
        lesson_parts.append(f"{lesson_header}{details_str}")

    return header + "\n\n".join(lesson_parts)


def format_teacher_schedule_text(schedule_info: dict) -> str:
    """Форматирует расписание на день для преподавателя."""
    if not schedule_info or schedule_info.get('error'):
        return f"❌ <b>Ошибка:</b> {schedule_info.get('error', 'Не удалось получить расписание преподавателя.')}"

    teacher_name = schedule_info.get('teacher', 'Преподаватель')
    date_str = schedule_info['date'].strftime('%d.%m.%Y')
    day_name = schedule_info.get('day_name', '')

    header = f"🧑‍🏫 <b>{teacher_name}</b>\n🗓 <b>{date_str} · {day_name}</b>\n"
    
    lessons = schedule_info.get('lessons')
    if not lessons:
        return header + "\n🎉 <b>Занятий нет!</b>"

    lesson_parts = []
    for lesson in lessons:
        time_str = lesson.get('time', 'Время не указано')
        subject_str = lesson.get('subject', 'Предмет не указан')
        group_str = f" ({', '.join(lesson.get('groups', []))})"

        lesson_header = f"<b>{time_str}</b>\n{subject_str}{group_str}"
        
        details_parts = []
        room = lesson.get('room')
        if room:
            details_parts.append(f"📍 {room}")
        
        details_str = "\n" + " ".join(details_parts) if details_parts else ""
        lesson_parts.append(f"{lesson_header}{details_str}")

    return header + "\n\n".join(lesson_parts)


def format_classroom_schedule_text(schedule_info: dict) -> str:
    """Форматирует расписание на день для аудитории."""
    if not schedule_info or schedule_info.get('error'):
        return f"❌ <b>Ошибка:</b> {schedule_info.get('error', 'Не удалось получить расписание аудитории.')}"

    classroom_number = schedule_info.get('classroom', 'Аудитория')
    date_str = schedule_info['date'].strftime('%d.%m.%Y')
    day_name = schedule_info.get('day_name', '')

    header = f"🚪 <b>Аудитория {classroom_number}</b>\n🗓 <b>{date_str} · {day_name}</b>\n"
    
    lessons = schedule_info.get('lessons')
    if not lessons:
        return header + "\n✅ <b>Аудитория свободна весь день!</b>"

    lesson_parts = []
    for lesson in lessons:
        time_str = lesson.get('time', 'Время не указано')
        subject_str = lesson.get('subject', 'Предмет не указан')
        group_str = f" ({', '.join(lesson.get('groups', []))})"
        
        lesson_header = f"<b>{time_str}</b>\n{subject_str}{group_str}"
        
        details_parts = []
        teachers = lesson.get('teachers')
        if teachers:
            details_parts.append(f"🧑‍🏫 {teachers}")
        
        details_str = "\n" + " ".join(details_parts) if details_parts else ""
        lesson_parts.append(f"{lesson_header}{details_str}")

    return header + "\n\n".join(lesson_parts)


def format_full_week_text(week_schedule: dict, week_name: str) -> str:
    """Форматирует текст расписания на всю неделю с корректной сортировкой пар."""
    days_order = ["ПОНЕДЕЛЬНИК", "ВТОРНИК", "СРЕДА", "ЧЕТВЕРГ", "ПЯТНИЦА", "СУББОТА"]
    text_parts = [f"🗓 <b>{week_name.capitalize()}</b>"]
    
    sorted_days = sorted(week_schedule.keys(), key=lambda day: days_order.index(day.upper()) if day.upper() in days_order else 99)

    for day_name in sorted_days:
        lessons = week_schedule.get(day_name)
        
        if lessons:
            text_parts.append(f"\n--- <b>{day_name.upper()}</b> ---")
            
            try:
                sorted_lessons = sorted(
                    lessons, 
                    key=lambda lesson: datetime.strptime(lesson.get('time', '23:59').split('-')[0].strip(), '%H:%M').time()
                )
            except (ValueError, IndexError):
                sorted_lessons = lessons

            for lesson in sorted_lessons:
                time_str = lesson.get('time', 'Время не указано')
                subject_str = lesson.get('subject', 'Предмет не указан')
                type_str = f"({lesson.get('type', '')})" if lesson.get('type') else ""
                
                text_parts.append(f"{time_str} - <b>{subject_str}</b> {type_str}")
                
                details_parts = []
                teachers = lesson.get('teachers')
                if teachers:
                    details_parts.append(f"🧑‍🏫 {teachers}")
                
                room = lesson.get('room')
                if room:
                    details_parts.append(f"📍 {room}")
                
                if details_parts:
                    text_parts.append(" ".join(details_parts))
    
    if len(text_parts) == 1:
        return f"🗓 <b>{week_name.capitalize()}</b>\n\n🎉 На этой неделе занятий нет!"
        
    return "\n".join(text_parts)

# --- ДИНАМИЧЕСКИЕ ЗАГОЛОВКИ ---

def generate_dynamic_header(lessons: list, target_date: date) -> tuple[str, str]:
    """Генерирует контекстный заголовок и прогресс-бар."""
    is_today = target_date == datetime.now(MOSCOW_TZ).date()

    if is_today and not lessons:
        return "✨ <b>Сегодня занятий нет.</b> Отличного дня!", ""

    if not is_today or not lessons:
        return "", ""

    try:
        sorted_lessons = sorted(lessons, key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        now_time = datetime.now(MOSCOW_TZ).time()
        
        MORNING_START_TIME = time(5, 0)
        
        passed_lessons_count = sum(1 for lesson in sorted_lessons if now_time > datetime.strptime(lesson['end_time_raw'], '%H:%M').time())
        total_lessons = len(sorted_lessons)
        progress_bar_emojis = '🟩' * passed_lessons_count + '⬜️' * (total_lessons - passed_lessons_count)
        progress_bar = f"<i>Прогресс дня: {passed_lessons_count}/{total_lessons}</i> {progress_bar_emojis}\n"

        first_lesson_start = datetime.strptime(sorted_lessons[0]['start_time_raw'], '%H:%M').time()
        last_lesson_end = datetime.strptime(sorted_lessons[-1]['end_time_raw'], '%H:%M').time()
        
        def get_safe_times(time_str: str) -> tuple[str, str]:
            time_str_unified = time_str.replace('–', '-').replace('—', '-')
            parts = [p.strip() for p in time_str_unified.split('-')]
            return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0] if parts else "", "")

        if now_time < MORNING_START_TIME:
            return "🌙 <b>Поздняя ночь.</b> Скоро утро!", progress_bar
            
        if now_time < first_lesson_start:
            start_time_str, _ = get_safe_times(sorted_lessons[0]['time'])
            return f"☀️ <b>Доброе утро!</b> Первая пара в {start_time_str}.", progress_bar

        if now_time > last_lesson_end:
            return "✅ <b>Пары на сегодня закончились.</b> Отдыхайте!", progress_bar

        for i, lesson in enumerate(sorted_lessons):
            start_time = datetime.strptime(lesson['start_time_raw'], '%H:%M').time()
            end_time = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
            _, lesson_end_time_str = get_safe_times(lesson['time'])

            if start_time <= now_time <= end_time:
                end_text = f"Закончится в {lesson_end_time_str}." if lesson_end_time_str else ""
                return f"⏳ <b>Идет пара:</b> {lesson['subject']}.\n{end_text}", progress_bar
            
            if i + 1 < len(sorted_lessons):
                next_lesson = sorted_lessons[i+1]
                next_start_time_obj = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
                next_start_time_str, _ = get_safe_times(next_lesson['time'])
                if end_time < now_time < next_start_time_obj:
                    return f"☕️ <b>Перерыв до {next_start_time_str}.</b>\nСледующая пара: {next_lesson['subject']}.", progress_bar

        return "", progress_bar 
    except (ValueError, IndexError, KeyError) as e:
        logging.error(f"Ошибка при генерации динамического заголовка: {e}. Данные урока: {lessons}")
        return "", ""

# --- ТЕКСТЫ ДЛЯ УВЕДОМЛЕНИЙ ---

UNSUBSCRIBE_FOOTER = "\n\n<tg-spoiler><i>Отключить эту рассылку можно в «⚙️ Настройки»</i></tg-spoiler>"

EVENING_GREETINGS = ["Добрый вечер! 👋", "Привет! Готовимся к завтрашнему дню.", "Вечерняя сводка на подходе."]
MORNING_GREETINGS = ["Доброе утро! ☀️", "Утро доброе! Учеба ждет.", "Утренняя сводка готова!"]
DAY_OF_WEEK_CONTEXT = {
    0: ["Завтра понедельник — начинаем неделю с чистого листа!", "Готовимся к началу новой недели."],
    1: ["Завтра вторник, втягиваемся в ритм.", "Планируем продуктивный вторник."],
    2: ["Завтра среда — экватор недели!", "Середина недели уже завтра. Держимся!"],
    3: ["Завтра четверг, финишная прямая близко.", "Еще один рывок до конца недели!"],
    4: ["Завтра пятница! Впереди заслуженный отдых.", "Последний рывок перед чиллом!"],
    5: ["Завтра учебная суббота — для самых стойких.", "Еще один день знаний, а потом отдых."],
    6: ["Завтра воскресенье — можно выспаться!", "Впереди выходной, но не забудьте про домашку 😉"]
}
CLOTHING_ADVICES = {
    "cold": ["Завтра будет морозно, не забудьте шапку и перчатки!", "Советуем одеться потеплее."],
    "cool": ["Завтра утром будет прохладно, легкая куртка или свитер будут в самый раз.", "Осенняя прохлада требует уюта."],
    "warm": ["Завтра обещают тепло, можно одеться полегче.", "Отличная погода для прогулки после учебы."],
    "hot": ["Завтра будет жарко! Пейте больше воды.", "Настоящее лето! Идеально для легкой одежды."]
}
EVENING_ENGAGEMENT_BLOCKS = {
    "prep_tip": ["💡 Совет: Соберите рюкзак с вечера, чтобы утром было меньше суеты.", "💡 Совет: Хороший сон — залог продуктивного дня."],
    "planning_question": ["🤔 Вопрос: Какая пара завтра кажется самой сложной?", "🤔 Вопрос: Какие цели ставите на завтра?"],
    "quote": ["📖 Цитата: «Успех — это успеть».", "📖 Цитата: «Планы — ничто, планирование — всё»."]
}

def generate_evening_intro(weather_forecast: Dict[str, Any] | None, target_date: datetime) -> str:
    weekday = target_date.weekday()
    greeting_line = random.choice(EVENING_GREETINGS)
    day_context_line = random.choice(DAY_OF_WEEK_CONTEXT.get(weekday, [""]))
    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast['temperature'])
        description = weather_forecast.get('description', '').lower()
        advice_line = ""
        if temp <= 0: advice_line = random.choice(CLOTHING_ADVICES["cold"])
        elif 0 < temp <= 12: advice_line = random.choice(CLOTHING_ADVICES["cool"])
        elif 12 < temp <= 20: advice_line = random.choice(CLOTHING_ADVICES["warm"])
        else: advice_line = random.choice(CLOTHING_ADVICES["hot"])
        weather_block = f"{weather_forecast.get('emoji', '')} Прогноз на завтра: {description.capitalize()}, {temp}°C.\n<i>{advice_line}</i>"
    engagement_type = random.choice(list(EVENING_ENGAGEMENT_BLOCKS.keys()))
    engagement_line = random.choice(EVENING_ENGAGEMENT_BLOCKS[engagement_type])
    parts = [day_context_line, weather_block, engagement_line]
    random.shuffle(parts)
    return "\n\n".join(filter(None, [greeting_line] + parts)) + "\n\n"

def generate_morning_intro(weather_forecast: Dict[str, Any] | None) -> str:
    greeting_line = random.choice(MORNING_GREETINGS)
    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast['temperature'])
        description = weather_forecast.get('description', '').lower()
        weather_block = f"За окном сейчас {description.capitalize()}, {temp}°C."
    return f"{greeting_line}\n{weather_block}\n"

def generate_reminder_text(lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None, reminder_time_minutes: int | None = 20) -> str | None:
    text = ""
    if reminder_type == "first" and lesson:
        greetings = [f"Первая пара через {reminder_time_minutes} минут!", "Скоро начало, не опаздывайте!", "Готовимся к первой паре!"]
        text = f"🔔 <b>{random.choice(greetings)}</b>\n\n"
    elif reminder_type == "break" and lesson:
        next_lesson_time = lesson.get('time', 'N/A').split('-')[0].strip()
        if break_duration and break_duration >= 40:
            break_text = f"У вас большой перерыв {break_duration} минут до {next_lesson_time}. {random.choice(['Можно успеть пообедать!', 'Отличный шанс сходить в столовую.'])}"
        elif break_duration and break_duration >= 15:
            break_text = f"Перерыв {break_duration} минут до {next_lesson_time}. {random.choice(['Время выпить чаю.', 'Можно немного размяться.'])}"
        else:
            break_text = random.choice(["Успейте дойти до следующей аудитории.", "Короткая передышка."])
        text = f"✅ <b>Пара закончилась!</b>\n{break_text}\n\n☕️ <b>Следующая пара:</b>\n"
    elif reminder_type == "final":
        final_phrases = ["Пары на сегодня всё! Можно отдыхать.", "Учебный день окончен. Хорошего вечера!"]
        return f"🎉 <b>{random.choice(final_phrases)}</b>{UNSUBSCRIBE_FOOTER}"
    else:
        return None

    if lesson:
        text += f"<b>{lesson.get('subject', 'N/A')}</b> ({lesson.get('type', 'N/A')}) в <b>{lesson.get('time', 'N/A')}</b>\n"
        info_parts = [f"📍 {room}" for room in [lesson.get('room')] if room]
        if teachers := lesson.get('teachers'): info_parts.append(f"<i>с {teachers}</i>")
        if info_parts: text += " ".join(info_parts)
    
    return text + UNSUBSCRIBE_FOOTER