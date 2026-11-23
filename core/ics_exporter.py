"""Экспорт расписания группы или преподавателя в формат iCalendar (RFC 5545 .ics)."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any

from core.config import DAY_MAP, MOSCOW_TZ
from core.semester_calendar import SEMESTER_LENGTH_WEEKS, SemesterInfo

_TIME_RANGE_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})")
_DAY_TO_INDEX: dict[str, int] = {name: idx for idx, name in enumerate(DAY_MAP) if name}

_VTIMEZONE_MOSCOW = (
    "BEGIN:VTIMEZONE\r\n"
    "TZID:Europe/Moscow\r\n"
    "BEGIN:STANDARD\r\n"
    "DTSTART:19700101T000000\r\n"
    "TZOFFSETFROM:+0300\r\n"
    "TZOFFSETTO:+0300\r\n"
    "TZNAME:MSK\r\n"
    "END:STANDARD\r\n"
    "END:VTIMEZONE"
)


def _escape_ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r\n", "\\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _fold_ics_line(line: str) -> str:
    """Сворачивает строку iCalendar по RFC 5545 (не более 75 октетов UTF-8 на строку)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    chunks: list[str] = []
    current = ""
    limit = 75
    for ch in line:
        candidate = current + ch
        if len(candidate.encode("utf-8")) > limit:
            chunks.append(current)
            current = " " + ch
            limit = 75
        else:
            current = candidate
    chunks.append(current)
    return "\r\n".join(chunks)


def _parse_time_range(lesson: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int]] | None:
    raw = str(lesson.get("time") or "")
    match = _TIME_RANGE_RE.search(raw)
    if not match:
        return None
    sh, sm, eh, em = (int(match.group(i)) for i in range(1, 5))
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
        return None
    return (sh, sm), (eh, em)


def collect_target_weeks(
    manager: Any,
    target_name: str,
    *,
    is_group_target: bool,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Собирает расписание обеих недель ('odd' и 'even') для группы или преподавателя."""
    if is_group_target:
        raw_schedules = getattr(manager, "_schedules", None) or {}
        group_data = raw_schedules.get(target_name.upper()) or {}
        return {
            "odd": dict(group_data.get("odd") or {}),
            "even": dict(group_data.get("even") or {}),
        }

    teachers_index = getattr(manager, "_teachers_index", None) or {}
    canonical = manager.resolve_canonical_teacher(target_name) if hasattr(manager, "resolve_canonical_teacher") else None
    key = canonical or target_name
    raw_lessons = teachers_index.get(key) or []
    weeks: dict[str, dict[str, list[dict[str, Any]]]] = {"odd": {}, "even": {}}
    for lesson in raw_lessons:
        day_name = lesson.get("day")
        if not day_name:
            continue
        code = str(lesson.get("week_code", "0"))
        if code in ("0", "1"):
            weeks["odd"].setdefault(day_name, []).append(lesson)
        if code in ("0", "2"):
            weeks["even"].setdefault(day_name, []).append(lesson)
    return weeks


def generate_schedule_ics(
    target_name: str,
    weeks_data: dict[str, dict[str, list[dict[str, Any]]]],
    semester_info: SemesterInfo,
    *,
    reference_date: date | None = None,
    is_teacher: bool = False,
) -> bytes | None:
    """Генерирует UTF-8 байты .ics календаря с повторениями пар по чётности недель.

    Возвращает None, если у цели нет ни одного занятия с валидным временем.
    """
    today = reference_date or datetime.now(MOSCOW_TZ).date()
    current_monday = today - timedelta(days=today.weekday())
    semester_end = semester_info.semester_start + timedelta(weeks=SEMESTER_LENGTH_WEEKS)
    until_date = semester_end if semester_end > today else today + timedelta(weeks=16)
    until_str = until_date.strftime("%Y%m%dT235959Z")
    dtstamp = datetime.now(MOSCOW_TZ).strftime("%Y%m%dT%H%M%SZ")

    # Определяем понедельники ближайшей нечётной и ближайшей чётной недель.
    if semester_info.week_key == "odd":
        week_mondays = {"odd": current_monday, "even": current_monday + timedelta(days=7)}
    else:
        week_mondays = {"even": current_monday, "odd": current_monday + timedelta(days=7)}

    events: list[str] = []
    for week_key in ("odd", "even"):
        week_label = "Нечётная неделя" if week_key == "odd" else "Чётная неделя"
        monday = week_mondays[week_key]
        day_map = weeks_data.get(week_key) or {}
        for day_name, lessons in day_map.items():
            day_idx = _DAY_TO_INDEX.get(day_name)
            if day_idx is None:
                continue
            event_date = monday + timedelta(days=day_idx)
            for idx, lesson in enumerate(lessons):
                parsed_time = _parse_time_range(lesson)
                if not parsed_time:
                    continue
                (sh, sm), (eh, em) = parsed_time
                subject = str(lesson.get("subject") or "Занятие").strip()
                lesson_type = str(lesson.get("type") or "").strip()
                summary = f"{subject} ({lesson_type})" if lesson_type else subject
                room = str(lesson.get("room") or "").strip()

                desc_parts = [week_label]
                if is_teacher:
                    group_str = str(lesson.get("group") or "").strip()
                    if group_str:
                        desc_parts.append(f"Группа: {group_str}")
                else:
                    teacher_str = str(lesson.get("teacher") or "").strip()
                    if teacher_str:
                        desc_parts.append(f"Преподаватель: {teacher_str}")
                description = "\n".join(desc_parts)

                dtstart = f"{event_date.strftime('%Y%m%d')}T{sh:02d}{sm:02d}00"
                dtend = f"{event_date.strftime('%Y%m%d')}T{eh:02d}{em:02d}00"
                uid_src = f"{target_name}|{week_key}|{day_idx}|{dtstart}|{summary}|{idx}"
                uid = hashlib.sha256(uid_src.encode("utf-8")).hexdigest()[:24] + "@voenmeh-bot"

                vevent_lines = [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{dtstamp}",
                    f"DTSTART;TZID=Europe/Moscow:{dtstart}",
                    f"DTEND;TZID=Europe/Moscow:{dtend}",
                    f"RRULE:FREQ=WEEKLY;INTERVAL=2;UNTIL={until_str}",
                    _fold_ics_line(f"SUMMARY:{_escape_ics_text(summary)}"),
                ]
                if room:
                    vevent_lines.append(_fold_ics_line(f"LOCATION:{_escape_ics_text(room)}"))
                vevent_lines.append(_fold_ics_line(f"DESCRIPTION:{_escape_ics_text(description)}"))
                vevent_lines.append("END:VEVENT")
                events.append("\r\n".join(vevent_lines))

    if not events:
        return None

    cal_name = f"Военмех — {target_name}"
    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Voenmeh Timetable Bot//Schedule Export//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        _fold_ics_line(f"X-WR-CALNAME:{_escape_ics_text(cal_name)}"),
        "X-WR-TIMEZONE:Europe/Moscow",
        _VTIMEZONE_MOSCOW,
    ]
    full_text = "\r\n".join(header + events + ["END:VCALENDAR"]) + "\r\n"
    return full_text.encode("utf-8")


def build_ics_filename(target_name: str) -> str:
    """Формирует безопасное имя .ics файла для Telegram."""
    safe = re.sub(r"[^\w\-]+", "_", target_name.strip(), flags=re.UNICODE).strip("_")
    return f"{safe or 'schedule'}_schedule.ics"
