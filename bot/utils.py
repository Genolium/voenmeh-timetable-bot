from datetime import datetime

def format_schedule_text(day_info: dict) -> str:
    """Форматирует расписание на день для группы."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_obj = day_info.get('date')
    if not date_obj:
        return "❌ <b>Ошибка:</b> Дата не найдена в данных расписания."

    date_str = date_obj.strftime('%d.%m.%Y')
    day_name = day_info.get('day_name', '')
    week_type = f"({day_info.get('week_type_name', '')})" if day_info.get('week_type_name') else ""

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

    teacher_name = schedule_info.get('teacher_name', 'Преподаватель')
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
        group_str = f" ({lesson.get('group', 'группа не указана')})"
        
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

    classroom_number = schedule_info.get('classroom_number', 'Аудитория')
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
        group_str = f"({lesson.get('group', 'группа не указана')})"
        
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