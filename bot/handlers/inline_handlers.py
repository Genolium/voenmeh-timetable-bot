import logging
import random
import re
from datetime import date, datetime, timedelta
from uuid import uuid4

from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from rapidfuzz import process

from bot.text_formatters import format_schedule_text
from core.config import MOSCOW_TZ
from core.manager import TimetableManager
from core.text_utils import normalize_group_name
from core.i18n import i18n

# Словарь для парсинга дней недели
DAY_ALIASES = {
    "понедельник": 0, "пн": 0, "mon": 0, "monday": 0, "周一": 0, "星期一": 0,
    "вторник": 1, "вт": 1, "tue": 1, "tuesday": 1, "周二": 1, "星期二": 1,
    "среда": 2, "ср": 2, "wed": 2, "wednesday": 2, "周三": 2, "星期三": 2,
    "четверг": 3, "чт": 3, "thu": 3, "thursday": 3, "周四": 3, "星期四": 3,
    "пятница": 4, "пт": 4, "fri": 4, "friday": 4, "周五": 4, "星期五": 4,
    "суббота": 5, "сб": 5, "sat": 5, "saturday": 5, "周六": 5, "星期六": 5,
    "воскресенье": 6, "вс": 6, "sun": 6, "sunday": 6, "周日": 6, "星期日": 6,
}


def parse_day_from_query(query_parts: list[str]) -> tuple[date, list[str]]:
    """
    Пытается извлечь день недели из запроса и вернуть целевую дату.
    Возвращает дату и оставшиеся части запроса.
    """
    today = datetime.now(MOSCOW_TZ).date()
    query_lower = " ".join(query_parts).lower()

    if any(k in query_lower for k in ["сегодня", "today", "今天"]):
        remaining_parts = [p for p in query_parts if p.lower() not in ["сегодня", "today", "今天"]]
        return today, remaining_parts
    if any(k in query_lower for k in ["завтра", "tomorrow", "tmr", "明天"]):
        remaining_parts = [p for p in query_parts if p.lower() not in ["завтра", "tomorrow", "tmr", "明天"]]
        return today + timedelta(days=1), remaining_parts

    target_day = None
    remaining_parts = list(query_parts)  # Копируем, чтобы изменять
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
    Поддерживает autocomplete по группам с fuzzy-поиском.
    Пример запроса: @bot_name О735Б завтра
    """
    query_text = query.query.strip()
    
    # Пустой запрос - показать подсказку
    if not query_text:
        results = [
            InlineQueryResultArticle(
                id="help",
                title="🔍 Введите название группы",
                description="Например: О735Б или О735Б завтра",
                input_message_content=InputTextMessageContent(
                    message_text="Используйте inline-режим: @voenmeh_bot <группа> [день]"
                ),
            )
        ]
        await query.answer(results, cache_time=300, is_personal=False)
        return

    # Разделяем запрос на части
    parts = re.split(r"\s+", query_text)

    # Определяем дату и оставшиеся части запроса (предположительно, группа)
    target_date, remaining_parts = parse_day_from_query(parts)

    # Если нет оставшихся частей после извлечения дня - показать подсказки
    if not remaining_parts:
        # Показать популярные группы как подсказки
        popular_groups = list(manager._schedules.keys())[:5]
        results = [
            InlineQueryResultArticle(
                id=f"suggest:{g}",
                title=f"📅 {g}",
                description=f"Нажмите для расписания на {target_date.strftime('%d.%m')}",
                input_message_content=InputTextMessageContent(
                    message_text=f"@voenmeh_bot {g} {query_text}"
                ),
            )
            for g in popular_groups
        ]
        await query.answer(results, cache_time=60, is_personal=False)
        return

        return

    # Нормализуем группу
    raw_group_query = remaining_parts[0]
    group_query = normalize_group_name(raw_group_query)
    
    # Если нормализация вернула пустую, пробуем оригинал (вдруг это teacher)
    if not group_query:
        group_query = raw_group_query.upper()

    # Точное совпадение - показать расписание
    if group_query in manager._schedules:
        await _send_schedule_result(query, manager, group_query, target_date)
        return

    # Fuzzy-поиск - показать autocomplete подсказки
    matches = process.extract(
        group_query,
        list(manager._schedules.keys()),
        limit=5,
        score_cutoff=40,
    )

    if matches:
        results = []
        for match_name, score, _ in matches:
            results.append(
                InlineQueryResultArticle(
                    id=f"autocomplete:{match_name}",
                    title=f"📅 {match_name}",
                    description=f"Совпадение: {int(score)}% • Нажмите для расписания",
                    input_message_content=InputTextMessageContent(
                        message_text=f"@voenmeh_bot {match_name}"
                    ),
                )
            )
        await query.answer(results, cache_time=30, is_personal=True)
    else:
        # Ничего не найдено
        result = InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"❌ Группа '{group_query}' не найдена",
            description="Проверьте правильность написания группы",
            input_message_content=InputTextMessageContent(
                message_text=f"❌ Группа <b>{group_query}</b> не найдена."
            ),
        )
        await query.answer([result], cache_time=10, is_personal=True)


async def _send_schedule_result(
    query: InlineQuery,
    manager: TimetableManager,
    group_name: str,
    target_date: date,
):
    """Отправляет результат с расписанием группы."""
    # Получаем расписание
    schedule_info = await manager.get_schedule_for_day(group_name, target_date)

    # Формируем ответ
    result_title = f"Расписание для {group_name} на {target_date.strftime('%d.%m')} ({schedule_info.get('day_name', '')})"

    if schedule_info.get("lessons"):
        num_lessons = len(schedule_info["lessons"])
        first_lesson_time = schedule_info["lessons"][0]["time"].split("–")[0].strip()
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
            disable_web_page_preview=True,
        ),
        thumb_url="https://images2.imgbox.com/d2/af/ztPHjmSO_o.png",
        thumb_width=48,
        thumb_height=48,
    )

    try:
        await query.answer([result], cache_time=60, is_personal=True)
    except Exception as e:
        logging.error(f"Error answering inline query: {e}")
