import logging
import re
import random
from datetime import date, timedelta, datetime
from uuid import uuid4

from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from core.manager import TimetableManager
from bot.text_formatters import format_schedule_text
from core.config import MOSCOW_TZ

# Словарь для парсинга дней недели
DAY_ALIASES = {
    'понедельник': 0, 'пн': 0,
    'вторник': 1, 'вт': 1,
    'среда': 2, 'ср': 2,
    'четверг': 3, 'чт': 3,
    'пятница': 4, 'пт': 4,
    'суббота': 5, 'сб': 5,
    'воскресенье': 6, 'вс': 6,
}

def parse_day_from_query(query_parts: list[str]) -> tuple[date, list[str]]:
    """
    Пытается извлечь день недели из запроса и вернуть целевую дату.
    Возвращает дату и оставшиеся части запроса.
    """
    today = datetime.now(MOSCOW_TZ).date()
    query_lower = " ".join(query_parts).lower()

    if "сегодня" in query_lower:
        remaining_parts = [p for p in query_parts if p.lower() != "сегодня"]
        return today, remaining_parts
    if "завтра" in query_lower:
        remaining_parts = [p for p in query_parts if p.lower() != "завтра"]
        return today + timedelta(days=1), remaining_parts

    target_day = None
    remaining_parts = list(query_parts) # Копируем, чтобы изменять
    for i, part in enumerate(query_parts):
        day_num = DAY_ALIASES.get(part.lower())
        if day_num is not None:
            target_day = day_num
            # Удаляем день недели из оставшихся частей
            remaining_parts.pop(i)
            break
            
    if target_day is not None:
        days_diff = target_day - today.weekday()
        # Если запрашиваемый день уже прошел на этой неделе, берем следующую
        if days_diff < 0:
            days_diff += 7
        return today + timedelta(days=days_diff), remaining_parts

    # Если день не указан, по умолчанию используется сегодня
    return today, remaining_parts


async def inline_query_handler(query: InlineQuery, manager: TimetableManager):
    """
    Обработчик inline-запросов для быстрого получения расписания.
    Пример запроса: @bot_name О735Б завтра
    """
    query_text = query.query.strip()
    if not query_text:
        return

    # Разделяем запрос на части
    parts = re.split(r'\s+', query_text)
    
    # Определяем дату и оставшиеся части запроса (предположительно, группа)
    target_date, remaining_parts = parse_day_from_query(parts)
    
    if not remaining_parts:
        return # Не удалось определить группу

    group_name = remaining_parts[0].upper()
    
    # Проверяем, существует ли такая группа
    if group_name not in manager._schedules:
        result = InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"Группа '{group_name}' не найдена",
            description="Проверьте правильность написания группы.",
            input_message_content=InputTextMessageContent(
                message_text=f"❌ Группа <b>{group_name}</b> не найдена."
            )
        )
        await query.answer([result], cache_time=10, is_personal=True)
        return

    # Получаем расписание
    schedule_info = manager.get_schedule_for_day(group_name, target_date)
    
    # Формируем ответ
    result_title = f"Расписание для {group_name} на {target_date.strftime('%d.%m')} ({schedule_info.get('day_name', '')})"
    
    if schedule_info.get("lessons"):
        num_lessons = len(schedule_info["lessons"])
        first_lesson_time = schedule_info["lessons"][0]['time'].split('–')[0].strip()
        result_description = f"Пар: {num_lessons}. Начало в {first_lesson_time}."
    else:
        result_description = "🎉 Занятий нет, можно отдыхать!"
    
    formatted_text = format_schedule_text(schedule_info)
    
    # Добавляем рекламу канала в 20% случаев
    if random.random() < 0.2:
        formatted_text += "\n\n📢 <i>Новости разработки: <a href='https://t.me/voenmeh404'>Аудитория 404 | Военмех</a></i>"

    result = InlineQueryResultArticle(
        id=f"{group_name}:{target_date.isoformat()}",
        title=result_title,
        description=result_description,
        input_message_content=InputTextMessageContent(
            message_text=formatted_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        ),
        thumb_url="https://images2.imgbox.com/d2/af/ztPHjmSO_o.png", 
        thumb_width=48,
        thumb_height=48
    )

    try:
        await query.answer([result], cache_time=60, is_personal=True)
    except Exception as e:
        logging.error(f"Error answering inline query: {e}")