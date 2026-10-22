import pytest

from bot.text_formatters import format_teacher_schedule_text


def test_format_teacher_schedule_text_success():
    """Тестирует успешное форматирование расписания преподавателя"""
    from datetime import date

    day_info = {
        "teacher": "Землянская Е.Р.",
        "date": date(2025, 8, 28),
        "day_name": "Четверг",
        "lessons": [
            {
                "time": "18:30-20:00",
                "subject": "ИНФОРМАЦИОННЫЕ ТЕХНОЛОГИИ",
                "groups": ["О742Б", "О743Б"],
                "room": "218*",
            },
            {
                "time": "20:10-21:40",
                "subject": "ПРОГРАММИРОВАНИЕ",
                "groups": ["О741Б"],
                "room": "315",
            },
        ],
    }

    result = format_teacher_schedule_text(day_info)

    assert "Четверг" in result
    assert "О742Б" in result
    assert "О743Б" in result
    assert "О741Б" in result
    assert "ИНФОРМАЦИОННЫЕ ТЕХНОЛОГИИ" in result
    assert "ПРОГРАММИРОВАНИЕ" in result
    assert "18:30-20:00" in result
    assert "20:10-21:40" in result


def test_format_teacher_schedule_text_no_lessons():
    """Тестирует форматирование пустого расписания преподавателя"""
    from datetime import date

    day_info = {
        "teacher": "Землянская Е.Р.",
        "date": date(2025, 8, 25),
        "day_name": "Понедельник",
        "lessons": [],
    }

    result = format_teacher_schedule_text(day_info)

    assert "Понедельник" in result
    assert "занятий нет" in result.lower()


def test_format_teacher_schedule_text_single_lesson():
    """Тестирует форматирование расписания с одним занятием"""
    from datetime import date

    day_info = {
        "teacher": "Землянская Е.Р.",
        "date": date(2025, 8, 26),
        "day_name": "Вторник",
        "lessons": [
            {
                "time": "14:00-15:30",
                "subject": "МАТЕМАТИКА",
                "groups": ["М101"],
                "room": "101",
            }
        ],
    }

    result = format_teacher_schedule_text(day_info)

    assert "Вторник" in result
    assert "М101" in result
    assert "МАТЕМАТИКА" in result
    assert "14:00-15:30" in result
    assert "101" in result
