def format_schedule_text(day_info: dict) -> str:
    """Форматирует расписание на день для отправки в Telegram."""
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
        room = f"📍 {lesson['room']}" if lesson['room'] else ""
        
        lesson_str = (
            f"<b>{lesson['time']}</b> - {lesson['subject']} ({lesson['type']})\n"
            f"<pre>   {room:<15} {teachers}</pre>"
        )
        body.append(lesson_str)
    
    return f"{header}\n\n" + "\n".join(body)

def format_full_week_text(week_schedule, week_name_header):
    """Форматирует расписание на всю неделю."""
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
    return text