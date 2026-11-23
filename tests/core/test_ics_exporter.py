"""Тесты для экспорта расписания в формат iCalendar (.ics)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from core.ics_exporter import (
    _fold_ics_line,
    _parse_time_range,
    build_ics_filename,
    collect_target_weeks,
    generate_schedule_ics,
)
from core.semester_calendar import SemesterInfo


def test_fold_ics_line_and_parse_time_range():
    short = "SUMMARY:Математика"
    assert _fold_ics_line(short) == short

    long_line = "SUMMARY:" + "ДлинноеНазваниеПредмета_" * 6
    folded = _fold_ics_line(long_line)
    assert "\r\n " in folded
    for part in folded.split("\r\n"):
        assert len(part.encode("utf-8")) <= 75

    assert _parse_time_range({"time": "09:00-10:30"}) == ((9, 0), (10, 30))
    assert _parse_time_range({"time": "25:00-26:00"}) is None
    assert _parse_time_range({"time": "без времени"}) is None


def test_collect_target_weeks_group_and_teacher():
    tm = MagicMock()
    tm._schedules = {
        "О735Б": {
            "odd": {"Понедельник": [{"subject": "НИР", "time": "14:55-16:25", "room": "219*а", "teacher": "Снижко"}]},
            "even": {"Вторник": [{"subject": "БД", "time": "10:50-12:20"}]},
        }
    }
    tm._teachers_index = {
        "Скобеев Ю.С.": [
            {"day": "Понедельник", "week_code": "0", "subject": "Сети", "time": "18:30-20:00", "group": "О734Б"},
            {"day": "Вторник", "week_code": "1", "subject": "Сети ОДД", "time": "18:30-20:00", "group": "О735Б"},
            {"day": "Среда", "week_code": "2", "subject": "Сети ИВЕН", "time": "20:05-21:35"},
            {"day": "", "week_code": "0", "subject": "Пустой день", "time": "10:00-11:00"},
        ]
    }
    tm.resolve_canonical_teacher.return_value = "Скобеев Ю.С."

    group_weeks = collect_target_weeks(tm, "о735б", is_group_target=True)
    assert len(group_weeks["odd"]["Понедельник"]) == 1
    assert len(group_weeks["even"]["Вторник"]) == 1

    teacher_weeks = collect_target_weeks(tm, "Скобеев", is_group_target=False)
    assert len(teacher_weeks["odd"]["Понедельник"]) == 1
    assert len(teacher_weeks["even"]["Понедельник"]) == 1
    assert len(teacher_weeks["odd"]["Вторник"]) == 1
    assert len(teacher_weeks["even"]["Среда"]) == 1


def test_generate_schedule_ics_student_odd_and_even_weeks():
    info_odd = SemesterInfo(semester="fall", semester_start=date(2026, 9, 1), week_number=13, week_key="odd")
    weeks = {
        "odd": {
            "Понедельник": [
                {
                    "subject": "УПР.ПРОЕКТАМИ",
                    "type": "лек",
                    "time": "16:45-18:15",
                    "room": "429*",
                    "teacher": "Ефимов А.М.",
                },
                {"subject": "Сломанное время", "time": "invalid"},
            ],
            "НеизвестныйДень": [{"subject": "Skip", "time": "09:00-10:30"}],
        },
        "even": {
            "Среда": [
                {"subject": "НИР", "time": "14:55-16:25", "room": "", "teacher": ""},
            ]
        },
    }

    ics = generate_schedule_ics("О735Б", weeks, info_odd, reference_date=date(2026, 11, 23))
    assert ics is not None
    text = ics.decode("utf-8")
    unfolded = text.replace("\r\n ", "")
    assert "BEGIN:VCALENDAR\r\n" in text
    assert "TZID:Europe/Moscow" in text
    assert "DTSTART;TZID=Europe/Moscow:20261123T164500" in text
    assert "SUMMARY:УПР.ПРОЕКТАМИ (лек)" in unfolded
    assert "LOCATION:429*" in unfolded
    assert "Преподаватель: Ефимов А.М." in unfolded
    assert "DTSTART;TZID=Europe/Moscow:20261202T145500" in text
    assert "RRULE:FREQ=WEEKLY;INTERVAL=2;UNTIL=" in text

    # Чётная стартовая неделя + преподаватель + дата после конца семестра
    info_even = SemesterInfo(semester="spring", semester_start=date(2026, 2, 9), week_number=0, week_key="even")
    teacher_weeks = {
        "odd": {},
        "even": {
            "Понедельник": [
                {"subject": "СЕТИ", "time": "18:30-20:00", "room": "216*", "group": "О734Б"},
                {"subject": "СЕТИ 2", "time": "20:05-21:35"},
            ]
        },
    }
    ics_t = generate_schedule_ics(
        "Скобеев Ю.С.",
        teacher_weeks,
        info_even,
        reference_date=date(2026, 7, 1),
        is_teacher=True,
    )
    assert ics_t is not None
    t_text = ics_t.decode("utf-8")
    assert "Группа: О734Б" in t_text
    assert "DTSTART;TZID=Europe/Moscow:20260629T183000" in t_text


def test_generate_schedule_ics_empty_and_filename():
    info = SemesterInfo(semester="fall", semester_start=date(2026, 9, 1), week_number=1, week_key="odd")
    assert generate_schedule_ics("О735Б", {"odd": {}, "even": {}}, info) is None
    assert build_ics_filename("О735Б") == "О735Б_schedule.ics"
    assert build_ics_filename("Скобеев Ю.С.") == "Скобеев_Ю_С_schedule.ics"
    assert build_ics_filename("!!!") == "schedule_schedule.ics"
