import logging
import random
import re
from datetime import date, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from rapidfuzz import process

from bot.text_formatters import esc, format_schedule_text, format_teacher_schedule_text
from core.bot_identity import bot_mention, get_bot_username
from core.config import MOSCOW_TZ
from core.i18n import i18n
from core.manager import TimetableManager
from core.text_utils import normalize_group_name

# Словарь для парсинга дней недели
DAY_ALIASES = {
    "понедельник": 0,
    "пн": 0,
    "mon": 0,
    "monday": 0,
    "周一": 0,
    "星期一": 0,
    "вторник": 1,
    "вт": 1,
    "tue": 1,
    "tuesday": 1,
    "周二": 1,
    "星期二": 1,
    "среда": 2,
    "ср": 2,
    "wed": 2,
    "wednesday": 2,
    "周三": 2,
    "星期三": 2,
    "четверг": 3,
    "чт": 3,
    "thu": 3,
    "thursday": 3,
    "周四": 3,
    "星期四": 3,
    "пятница": 4,
    "пт": 4,
    "fri": 4,
    "friday": 4,
    "周五": 4,
    "星期五": 4,
    "суббота": 5,
    "сб": 5,
    "sat": 5,
    "saturday": 5,
    "周六": 5,
    "星期六": 5,
    "воскресенье": 6,
    "вс": 6,
    "sun": 6,
    "sunday": 6,
    "周日": 6,
    "星期日": 6,
}


def parse_day_from_query(query_parts: list[str]) -> tuple[date, list[str]]:
    """
    Пытается извлечь день недели из запроса и вернуть целевую дату.
    Возвращает дату и оставшиеся части запроса.
    """
    today = datetime.now(MOSCOW_TZ).date()
    today_tokens = {"сегодня", "today", "今天"}
    tomorrow_tokens = {"завтра", "tomorrow", "tmr", "明天"}

    if any(p.lower().strip(",.!?") in today_tokens for p in query_parts):
        remaining_parts = [p for p in query_parts if p.lower().strip(",.!?") not in today_tokens]
        return today, remaining_parts
    if any(p.lower().strip(",.!?") in tomorrow_tokens for p in query_parts):
        remaining_parts = [p for p in query_parts if p.lower().strip(",.!?") not in tomorrow_tokens]
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


def _inline_lang(query: InlineQuery) -> str:
    try:
        code = str(query.from_user.language_code or "").lower()
        if "zh" in code:
            return "zh"
        if "en" in code:
            return "en"
    except Exception:
        pass
    return "ru"


def _tr(lang: str, key: str, **kwargs: Any) -> str:
    return cast(str, i18n.get(key, lang=lang, **kwargs))


def _available_groups(manager: TimetableManager) -> list[str]:
    """Возвращает только реальные группы, без служебных ключей вида __...__."""
    return [group for group in manager._schedules if not group.startswith("__")]


async def _safe_answer(query: InlineQuery, results: list[Any], **kwargs: Any) -> None:
    try:
        await query.answer(results, **kwargs)
    except Exception as e:
        logging.debug(f"Failed to answer inline query: {e}")


async def inline_query_handler(query: InlineQuery, manager: TimetableManager) -> None:
    """
    Обработчик inline-запросов для быстрого получения расписания.
    Поддерживает autocomplete по группам с fuzzy-поиском.
    Пример запроса: @bot_name О735Б завтра
    """
    query_text = query.query.strip()
    lang = _inline_lang(query)

    try:
        from core.analytics import track_feature_click

        await track_feature_click(getattr(manager, "redis", None), "inline_group")
    except Exception:
        pass

    # Пустой запрос - показать подсказку
    if not query_text:
        results = [
            InlineQueryResultArticle(
                id="help",
                title=_tr(lang, "inline_help_title"),
                description=_tr(lang, "inline_help_description"),
                input_message_content=InputTextMessageContent(
                    message_text=_tr(lang, "inline_help_message", bot_username=bot_mention())
                ),
            )
        ]
        await _safe_answer(query, results, cache_time=300, is_personal=True)
        return

    # Разделяем запрос на части
    parts = re.split(r"\s+", query_text)

    # Определяем дату и оставшиеся части запроса (предположительно, группа)
    target_date, remaining_parts = parse_day_from_query(parts)

    # Если нет оставшихся частей после извлечения дня - показать подсказки
    if not remaining_parts:
        # Показать популярные группы как подсказки
        popular_groups = _available_groups(manager)[:5]
        results = [
            InlineQueryResultArticle(
                id=f"suggest:{g}",
                title=f"📅 {g}",
                description=_tr(lang, "inline_suggest_description", date=target_date.strftime("%d.%m")),
                input_message_content=InputTextMessageContent(message_text=f"{bot_mention()} {g} {query_text}"),
            )
            for g in popular_groups
        ]
        await _safe_answer(query, results, cache_time=60, is_personal=True)
        return

    # Нормализуем группу
    raw_group_query = remaining_parts[0]
    group_query = normalize_group_name(raw_group_query)

    # Если нормализация вернула пустую, пробуем оригинал (вдруг это teacher)
    if not group_query:
        group_query = raw_group_query.upper()

    # Точное совпадение - показать расписание
    available_groups = _available_groups(manager)
    if group_query in available_groups:
        await _send_schedule_result(query, manager, group_query, target_date)
        return

    # Fuzzy-поиск групп - показать autocomplete подсказки
    matches = process.extract(
        group_query,
        available_groups,
        limit=5,
        score_cutoff=40,
    )

    if matches:
        results = []
        for match_name, score, _idx in matches:
            results.append(
                InlineQueryResultArticle(
                    id=f"autocomplete:{match_name}",
                    title=f"📅 {match_name}",
                    description=_tr(lang, "inline_match_description", score=int(score)),
                    input_message_content=InputTextMessageContent(message_text=f"{bot_mention()} {match_name}"),
                )
            )
        await _safe_answer(query, results, cache_time=30, is_personal=True)
        return

    # Поиск преподавателя (если запрос похож на фамилию)
    if hasattr(manager, "find_teachers_fuzzy"):
        teacher_matches = manager.find_teachers_fuzzy(query_text, limit=3, score_cutoff=65)
        if isinstance(teacher_matches, (list, tuple)) and teacher_matches:
            await _send_teacher_result(query, manager, teacher_matches[0], target_date)
            return

    # Ничего не найдено
    result = InlineQueryResultArticle(
        id=str(uuid4()),
        title=_tr(lang, "inline_not_found_title", group=group_query),
        description=_tr(lang, "inline_not_found_description"),
        input_message_content=InputTextMessageContent(
            message_text=_tr(lang, "inline_not_found_message", group=esc(group_query)),
            parse_mode="HTML",
        ),
    )
    await _safe_answer(query, [result], cache_time=10, is_personal=True)


async def _send_schedule_result(
    query: InlineQuery,
    manager: TimetableManager,
    group_name: str,
    target_date: date,
) -> None:
    """Отправляет результат с расписанием группы."""
    # Получаем расписание
    schedule_info = await manager.get_schedule_for_day(group_name, target_date)
    assert schedule_info is not None

    # Формируем ответ
    lang = _inline_lang(query)
    result_title = _tr(
        lang,
        "inline_result_title",
        group=group_name,
        date=target_date.strftime("%d.%m"),
        day=schedule_info.get("day_name", ""),
    )

    if schedule_info.get("lessons"):
        num_lessons = len(schedule_info["lessons"])
        first_lesson_time = schedule_info["lessons"][0]["time"].replace("–", "-").split("-")[0].strip()
        result_description = _tr(lang, "inline_lessons_count", count=num_lessons, time=first_lesson_time)
    else:
        result_description = _tr(lang, "inline_no_lessons")
    formatted_text = format_schedule_text(schedule_info, lang=lang)

    # Добавляем рекламу канала в 20% случаев
    if random.random() < 0.2:
        formatted_text += _tr(lang, "channel_promo")

    uname = get_bot_username()
    open_btn_label = {"ru": "📅 Открыть в боте", "en": "📅 Open in bot", "zh": "📅 在机器人中打开"}.get(
        lang, "📅 Открыть в боте"
    )
    reply_markup = (
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=open_btn_label, url=f"https://t.me/{uname}?start=group_{group_name}")]]
        )
        if uname
        else None
    )

    result = InlineQueryResultArticle(
        id=f"{group_name}:{target_date.isoformat()}",
        title=result_title,
        description=result_description,
        input_message_content=InputTextMessageContent(
            message_text=formatted_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        ),
        reply_markup=reply_markup,
        thumb_url="https://images2.imgbox.com/d2/af/ztPHjmSO_o.png",
        thumb_width=48,
        thumb_height=48,
    )

    try:
        await query.answer([result], cache_time=60, is_personal=True)
    except Exception as e:
        logging.error(f"Error answering inline query: {e}")


async def _send_teacher_result(
    query: InlineQuery,
    manager: TimetableManager,
    teacher_name: str,
    target_date: date,
) -> None:
    """Отправляет inline-результат с расписанием преподавателя."""
    schedule_info = await manager.get_teacher_schedule(teacher_name, target_date=target_date)
    lang = _inline_lang(query)
    if not schedule_info or schedule_info.get("error"):
        return

    lessons = schedule_info.get("lessons") or []
    result_description = (
        _tr(
            lang,
            "inline_lessons_count",
            count=len(lessons),
            time=lessons[0]["time"].replace("–", "-").split("-")[0].strip(),
        )
        if lessons
        else _tr(lang, "inline_no_lessons")
    )
    formatted_text = format_teacher_schedule_text(schedule_info, lang=lang)
    uname = get_bot_username()
    open_btn_label = {"ru": "📅 Открыть в боте", "en": "📅 Open in bot", "zh": "📅 在机器人中打开"}.get(
        lang, "📅 Открыть в боте"
    )
    reply_markup = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=open_btn_label, url=f"https://t.me/{uname}")]])
        if uname
        else None
    )
    result = InlineQueryResultArticle(
        id=f"teacher:{teacher_name}:{target_date.isoformat()}",
        title=f"🧑‍🏫 {teacher_name} ({target_date.strftime('%d.%m')})",
        description=result_description,
        input_message_content=InputTextMessageContent(
            message_text=formatted_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        ),
        reply_markup=reply_markup,
    )
    await _safe_answer(query, [result], cache_time=60, is_personal=True)
