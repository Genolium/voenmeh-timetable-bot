import pytest
from datetime import datetime, date

from bot.text_formatters import (
    format_schedule_text,
    format_teacher_schedule_text,
    format_classroom_schedule_text,
    format_full_week_text,
    generate_dynamic_header,
)
from core.config import MOSCOW_TZ


class TestFormatScheduleText:
    def test_returns_error_when_no_data(self):
        assert "Ошибка" in format_schedule_text({})

    def test_returns_error_when_no_date(self):
        text = format_schedule_text({'lessons': []})
        assert "Дата не найдена" in text

    def test_no_lessons_branch(self):
        text = format_schedule_text({'date': date(2025, 1, 1), 'day_name': 'Среда', 'week_name': 'Четная', 'lessons': []})
        assert "Занятий нет" in text

    def test_lessons_with_details(self):
        day_info = {
            'date': date(2025, 1, 1),
            'day_name': 'Среда',
            'week_name': 'Четная',
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Матан', 'type': 'Лекция', 'teachers': 'Иванов', 'room': '101'},
                {'time': '10:40-12:10', 'subject': 'Физика'},
            ],
        }
        text = format_schedule_text(day_info)
        assert "Матан" in text and "Иванов" in text and "101" in text
        assert "Физика" in text


class TestTeacherFormatter:
    def test_teacher_error_branch(self):
        assert "Ошибка" in format_teacher_schedule_text({'error': 'Нет данных'})

    def test_teacher_no_lessons(self):
        info = {'teacher': 'Петров', 'date': date(2025, 1, 2), 'day_name': 'Четверг', 'lessons': []}
        text = format_teacher_schedule_text(info)
        assert "Занятий нет" in text

    def test_teacher_with_lessons(self):
        info = {
            'teacher': 'Сидоров',
            'date': date(2025, 1, 2),
            'day_name': 'Четверг',
            'lessons': [
                {'time': '12:00-13:30', 'subject': 'ТАУ', 'groups': ['О735Б']},
            ],
        }
        text = format_teacher_schedule_text(info)
        assert "ТАУ" in text and "О735Б" in text


class TestClassroomFormatter:
    def test_classroom_error_branch(self):
        assert "Ошибка" in format_classroom_schedule_text({'error': 'Нет данных'})

    def test_classroom_no_lessons(self):
        info = {'classroom': '505', 'date': date(2025, 1, 3), 'day_name': 'Пятница', 'lessons': []}
        text = format_classroom_schedule_text(info)
        assert "Аудитория свободна" in text

    def test_classroom_with_lessons(self):
        info = {
            'classroom': '505',
            'date': date(2025, 1, 3),
            'day_name': 'Пятница',
            'lessons': [
                {'time': '08:30-10:00', 'subject': 'Информатика', 'teachers': 'Смирнов'},
            ],
        }
        text = format_classroom_schedule_text(info)
        assert "Информатика" in text and "Смирнов" in text


class TestFullWeekFormatter:
    def test_week_with_no_lessons(self):
        text = format_full_week_text({}, 'нечетная')
        assert "занятий нет" in text

    def test_week_orders_days_and_lessons(self):
        week = {
            'Понедельник': [
                {'time': '10:40-12:10', 'subject': 'Физика'},
                {'time': '09:00-10:30', 'subject': 'Матан'},
            ],
            'Пятница': [
                {'time': '11:00-12:30', 'subject': 'История', 'type': 'семинар', 'room': '101', 'teachers': 'Иванов'},
            ],
        }
        text = format_full_week_text(week, 'нечетная')
        # Проверяем, что более ранняя пара идёт первой
        assert text.index('09:00-10:30') < text.index('10:40-12:10')
        assert 'История' in text and 'Иванов' in text and '📍 101' in text


def test_generate_dynamic_header_handles_exceptions(mocker):
    # Подготовим данные, которые вызовут KeyError внутри функции
    lessons = [{'time': '09:00-10:30'}]  # отсутствуют start_time_raw/end_time_raw
    today = datetime.now(MOSCOW_TZ).date()
    mocker.patch('bot.text_formatters.logging')
    header, progress = generate_dynamic_header(lessons, today)
    assert header == ""
    assert progress == ""


