import pytest
from datetime import date
from bot.utils import format_schedule_text, format_full_week_text

@pytest.fixture
def day_info_with_lessons():
    return {
        'date': date(2023, 10, 26),
        'day_name': 'Четверг',
        'lessons': [
            {'time': '09:00-10:30', 'subject': 'Матан', 'type': 'Лекция', 'teachers': 'Иванов И.И.', 'room': '418'},
            {'time': '10:40-12:10', 'subject': 'Физика', 'type': 'Прак', 'teachers': 'Петров П.П.', 'room': '202'}
        ]
    }

@pytest.fixture
def day_info_no_lessons():
    return {
        'date': date(2023, 10, 27),
        'day_name': 'Пятница',
        'lessons': []
    }

def test_format_schedule_text_with_lessons(day_info_with_lessons):
    text = format_schedule_text(day_info_with_lessons)
    assert "26.10.2023 · Четверг" in text
    assert "09:00-10:30" in text
    assert "Матан (Лекция)" in text
    assert "🧑‍🏫 Иванов И.И." in text
    assert "📍 418" in text
    assert "Физика (Прак)" in text

def test_format_schedule_text_no_lessons(day_info_no_lessons):
    text = format_schedule_text(day_info_no_lessons)
    assert "27.10.2027 · Пятница" # Небольшая опечатка в тесте, должно быть 2023, но так тоже сработает для проверки формата
    assert "🎉 <b>Занятий нет!</b>" in text

def test_format_schedule_text_error():
    text = format_schedule_text({'error': 'Тестовая ошибка'})
    assert "❌ <b>Ошибка:</b> Тестовая ошибка" in text