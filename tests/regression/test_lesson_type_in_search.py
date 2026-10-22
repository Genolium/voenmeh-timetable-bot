from datetime import date

from bot.text_formatters import format_classroom_schedule_text, format_schedule_text, format_teacher_schedule_text


def _lesson(**overrides):
    lesson = {
        "time": "16:45-18:15",
        "subject": "МЕТОДЫ И СР-ВА АВТ. ППиС",
        "type": "лек",
        "groups": ["Е5М12"],
        "room": "257*",
    }
    lesson.update(overrides)
    return lesson


def test_teacher_schedule_shows_lesson_type():
    text = format_teacher_schedule_text(
        {
            "teacher": "Добросельский М.А.",
            "date": date(2026, 10, 15),
            "day_name": "Четверг",
            "week_name": "Четная",
            "week_number": 7,
            "lessons": [_lesson()],
        }
    )
    assert "МЕТОДЫ И СР-ВА АВТ. ППиС" in text
    assert "(лек)" in text
    assert "Чётная" in text
    assert "Неделя 7" in text


def test_classroom_schedule_shows_lesson_type():
    text = format_classroom_schedule_text(
        {
            "classroom": "257*",
            "date": date(2026, 10, 15),
            "day_name": "Четверг",
            "week_name": "Четная",
            "week_number": 7,
            "lessons": [_lesson(teachers="Добросельский М.А.")],
        }
    )
    assert "(лек)" in text
    assert "Чётная" in text
    assert "Неделя 7" in text


def test_group_schedule_shows_group_and_lesson_type():
    text = format_schedule_text(
        {
            "group": "Е5М12",
            "date": date(2026, 10, 15),
            "day_name": "Четверг",
            "week_name": "Четная",
            "week_number": 7,
            "lessons": [_lesson(teachers="Добросельский М.А.")],
        }
    )
    assert "🎓 <b>Е5М12</b>" in text
    assert "(лек)" in text
    assert "Неделя 7" in text
