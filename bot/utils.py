from core.config import MAP_URL 

def format_schedule_text(day_info: dict) -> str:
    """Форматирует расписание на день для группы (с новым дизайном)."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_str = day_info['date'].strftime('%d.%m.%Y')
    day_name = day_info['day_name']
    week_name = day_info['week_name']
    lessons = day_info['lessons']

    header = f"🗓 <b>{date_str}</b> · {day_name} <i>({week_name} неделя)</i>"
    
    if not lessons:
        # Это сообщение будет показано для дней без пар (кроме сегодня)
        return f"{header}\n\n🎉 Занятий нет, можно отдыхать!"

    body = []
    for lesson in lessons:
        time = lesson['time']
        subject = lesson['subject']
        lesson_type = lesson['type']
        
        # --- Улучшенный блок преподавателей ---
        teachers = lesson.get('teachers')
        teachers_str = f"🧑‍🏫 <i>{teachers}</i>" if teachers else "🧑‍🏫 Преподаватель не указан"
        
        # --- Улучшенный блок аудитории ---
        room = lesson.get('room')
        room_str = ""
        if room and room.strip() not in ['N/A', 'кабинет не указан', '']:
            # Используем <code> для возможности копирования по клику
            room_number = room.split(" ")[0] # Предполагаем, что номер - первое слово
            room_str = f"📍 Аудитория: <code>{room_number}</code>"
        else:
            room_str = "🤷‍♀️ Кабинет не указан"

        lesson_block = (
            f"<b>{time}</b>\n"
            f"<b>{subject}</b> ({lesson_type})\n"
            f"{teachers_str}\n"
            f"{room_str}"
        )
        body.append(lesson_block)
    
    map_link_text = f"\n\n🗺️ <a href='{MAP_URL}'>Карта корпусов</a>"

    return f"{header}\n\n" + "\n\n".join(body) + map_link_text

def format_full_week_text(week_schedule, week_name_header):
    """Форматирует расписание на всю неделю для группы (улучшенный дизайн)."""
    text = f"🗓 <b>{week_name_header}</b>\n"
    days_ordered = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
    has_any_lesson = False

    day_texts = []
    for day in days_ordered:
        lessons = week_schedule.get(day)
        if lessons:
            has_any_lesson = True
            day_header = f"<b>--- {day.upper()} ---</b>"
            lesson_texts = []
            # Сортируем пары на всякий случай
            sorted_lessons = sorted(lessons, key=lambda x: x['time'])
            for lesson in sorted_lessons:
                time = lesson.get('time', 'Время не указано')
                subject = lesson.get('subject', 'Предмет не указан')
                lesson_type = lesson.get('type', '')
                
                teachers_str = f"🧑‍🏫 <i>{lesson['teachers']}</i>" if lesson.get('teachers') else ""
                
                room_str = ""
                if lesson.get('room') and lesson['room'].strip() not in ['N/A', 'кабинет не указан', '']:
                    room_str = f"📍 <code>{lesson['room']}</code>"

                lesson_texts.append(
                    f"<b>{time}</b> - {subject} ({lesson_type})\n{teachers_str} {room_str}".strip()
                )
            day_texts.append(day_header + "\n" + "\n".join(lesson_texts))

    if not has_any_lesson:
        return text + "\n🎉 Занятий на этой неделе нет!"
        
    return text + "\n\n".join(day_texts) + f"\n\n\n🗺️ <a href='{MAP_URL}'>Карта корпусов</a>"

def format_teacher_schedule_text(day_info: dict) -> str:
    """Форматирует расписание преподавателя на день (улучшенный дизайн)."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_str = day_info['date'].strftime('%d.%m.%Y')
    day_name = day_info['day_name']
    week_name = day_info['week_name']
    lessons = day_info['lessons']
    teacher_name = day_info['teacher']

    header = f"🧑‍🏫 Расписание для <b>{teacher_name}</b>\n"
    header += f"🗓 <b>{date_str}</b> · {day_name} <i>({week_name} неделя)</i>"
    
    if not lessons:
        return f"{header}\n\n🎉 У преподавателя нет пар в этот день."

    body = []
    for lesson in lessons:
        groups_list = lesson.get('groups', [])
        groups_str = f"👥 <i>гр. {', '.join(sorted(groups_list))}</i>" if groups_list else ""
        
        room_str = ""
        if lesson.get('room') and lesson['room'].strip() not in ['N/A', 'кабинет не указан', '']:
            room_str = f"📍 Аудитория: <code>{lesson['room']}</code>"
        
        lesson_block = (
            f"<b>{lesson['time']}</b>\n"
            f"<b>{lesson['subject']}</b> ({lesson['type']})\n"
            f"{groups_str}\n"
            f"{room_str}"
        )
        body.append(lesson_block)
    
    return f"{header}\n\n" + "\n\n".join(body)


def format_classroom_schedule_text(day_info: dict) -> str:
    """Форматирует расписание аудитории на день (улучшенный дизайн)."""
    if not day_info or 'error' in day_info:
        return f"❌ <b>Ошибка:</b> {day_info.get('error', 'Неизвестная ошибка')}"

    date_str = day_info['date'].strftime('%d.%m.%Y')
    day_name = day_info['day_name']
    week_name = day_info['week_name']
    lessons = day_info['lessons']
    classroom_number = day_info['classroom']

    header = f"🚪 Расписание для аудитории <b>{classroom_number}</b>\n"
    header += f"🗓 <b>{date_str}</b> · {day_name} <i>({week_name} неделя)</i>"
    
    if not lessons:
        return f"{header}\n\n🎉 Аудитория свободна в этот день."

    body = []
    for lesson in lessons:
        # Информация о группах
        groups_list = lesson.get('groups', [])
        groups_str = f"👥 <i>гр. {', '.join(sorted(groups_list))}</i>" if groups_list else ""
        
        # Информация о преподавателях
        teachers = lesson.get('teachers', '')
        teachers_str = f"🧑‍🏫 <i>{teachers}</i>" if teachers else ""
        
        lesson_block = (
            f"<b>{lesson['time']}</b>\n"
            f"<b>{lesson['subject']}</b> ({lesson['type']})\n"
            f"{groups_str}\n"
            f"{teachers_str}"
        )
        # Убираем лишние пустые строки, если какой-то информации нет
        body.append("\n".join(line for line in lesson_block.splitlines() if line.strip()))
    
    return f"{header}\n\n" + "\n\n".join(body)