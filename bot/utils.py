from core.config import MAP_URL 

def format_schedule_text(day_info: dict) -> str:
    """Форматирует расписание на день для группы."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_str = day_info['date'].strftime('%d.%m.%Y')
    day_name = day_info['day_name']
    week_name = day_info['week_name']
    lessons = day_info['lessons']

    header = f"🗓 <b>{date_str}</b> - {day_name} <i>({week_name} неделя)</i>"
    
    if not lessons:
        return f"{header}\n\n🎉 Занятий нет, можно отдыхать!"

    body = []
    for lesson in lessons:
        teachers = f"<i>{lesson['teachers']}</i>" if lesson['teachers'] else ""
        
        room_text = ""
        room_data = lesson.get('room')
        if room_data and room_data.strip() not in ['N/A', 'кабинет не указан', '']:
            room_text = f"📍 {room_data}" 
        else:
            room_text = "🤷‍♀️ кабинет не указан"

        lesson_str = (
            f"<b>{lesson['time']}</b> - {lesson['subject']} ({lesson['type']})\n"
            f"<pre>   {room_text:<25} {teachers}</pre>"
        )
        body.append(lesson_str)
    
    map_link_text = f"\n\nПосмотреть расположение кабинетов: <a href='{MAP_URL}'>Карта корпусов</a>"

    return f"{header}\n\n" + "\n".join(body) + map_link_text


def format_full_week_text(week_schedule, week_name_header):
    """Форматирует расписание на всю неделю для группы."""
    text = f"🗓 <b>{week_name_header}</b>\n\n"
    days_ordered = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
    has_any_lesson = False

    for day in days_ordered:
        lessons = week_schedule.get(day)
        if lessons:
            has_any_lesson = True
            text += f"<b>--- {day.upper()} ---</b>\n"
            for lesson in lessons:
                text += (
                    f"<b>{lesson['time']}</b> - {lesson['subject']} ({lesson['type']})\n"
                    f"<i>   {lesson['room']}, {lesson['teachers']}</i>\n"
                )
            text += "\n"
    
    if not has_any_lesson:
        return text + "🎉 Занятий на этой неделе нет!"
    return text + f"\n\nПосмотреть расположение кабинетов: <a href='{MAP_URL}'>Карта корпусов</a>"


def format_teacher_schedule_text(day_info: dict) -> str:
    """Форматирует расписание преподавателя на день, группируя группы."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_str = day_info['date'].strftime('%d.%m.%Y')
    day_name = day_info['day_name']
    week_name = day_info['week_name']
    lessons = day_info['lessons']
    teacher_name = day_info['teacher']

    header = f"🧑‍🏫 Расписание для <b>{teacher_name}</b>\n"
    header += f"🗓 <b>{date_str}</b> - {day_name} <i>({week_name} неделя)</i>"
    
    if not lessons:
        return f"{header}\n\n🎉 У преподавателя нет пар в этот день."

    body = []
    for lesson in lessons:
        groups_list = lesson.get('groups', [])
        groups = f"<i>гр. {', '.join(sorted(groups_list))}</i>" if groups_list else ""
        
        room_text = ""
        room_data = lesson.get('room')
        if room_data and room_data.strip() not in ['N/A', 'кабинет не указан', '']:
            room_text = f"📍 {room_data}" 
        else:
            room_text = "🤷‍♀️ кабинет не указан"
        
        lesson_str = (
            f"<b>{lesson['time']}</b> - {lesson['subject']} ({lesson['type']})\n"
            f"<pre>   {room_text:<25} {groups}</pre>"
        )
        body.append(lesson_str)
    
    return f"{header}\n\n" + "\n".join(body) + f"\n\nПосмотреть расположение кабинетов: <a href='{MAP_URL}'>Карта корпусов</a>"


def format_classroom_schedule_text(day_info: dict) -> str:
    """Форматирует расписание аудитории на день, группируя группы."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_str = day_info['date'].strftime('%d.%m.%Y')
    day_name = day_info['day_name']
    week_name = day_info['week_name']
    lessons = day_info['lessons']
    classroom_number = day_info['classroom']

    header = f"🚪 Расписание для аудитории <b>{classroom_number}</b>\n"
    header += f"🗓 <b>{date_str}</b> - {day_name} <i>({week_name} неделя)</i>"
    
    if not lessons:
        return f"{header}\n\n🎉 Аудитория свободна в этот день."

    body = []
    for lesson in lessons:
        groups_list = lesson.get('groups', [])
        groups = f"<i>гр. {', '.join(sorted(groups_list))}</i>" if groups_list else ""
        teachers = f"<i>{lesson.get('teachers', '')}</i>" if lesson.get('teachers') else ""
        
        room_text = ""
        room_data = lesson.get('room')
        if room_data and room_data.strip() not in ['N/A', 'кабинет не указан', '']:
            room_text = f"📍 {room_data}" 
        else:
            room_text = "🤷‍♀️ кабинет не указан"
        
        lesson_str = (
            f"<b>{lesson['time']}</b> - {lesson['subject']} ({lesson['type']})\n"
            f"<pre>   {groups:<15} {teachers}</pre>"
        )
        body.append(lesson_str)
    
    return f"{header}\n\n" + "\n".join(body) + f"\n\nПосмотреть расположение кабинетов: <a href='{MAP_URL}'>Карта корпусов</a>"