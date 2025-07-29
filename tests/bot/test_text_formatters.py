import pytest
from datetime import date, datetime, time
from unittest.mock import MagicMock

from bot.text_formatters import (
    generate_dynamic_header, format_schedule_text, generate_evening_intro,
    generate_morning_intro, generate_reminder_text, UNSUBSCRIBE_FOOTER
)
from core.config import MOSCOW_TZ

@pytest.fixture
def lessons_sample():
    return [
        {'time': '09:00-10:30', 'subject': 'Матан', 'start_time_raw': '09:00', 'end_time_raw': '10:30'},
        {'time': '10:40-12:10', 'subject': 'Физика', 'start_time_raw': '10:40', 'end_time_raw': '12:10'}
    ]

@pytest.mark.parametrize("mock_time_str, expected_header", [
    ("08:00", "☀️ <b>Доброе утро!</b> Первая пара в 09:00."),
    ("09:30", "⏳ <b>Идет пара:</b> Матан.\nЗакончится в 10:30."),
    ("10:35", "☕️ <b>Перерыв до 10:40.</b>\nСледующая пара: Физика."),
    ("11:00", "⏳ <b>Идет пара:</b> Физика.\nЗакончится в 12:10."),
    ("13:00", "✅ <b>Пары на сегодня закончились.</b> Отдыхайте!"),
    ("04:00", "🌙 <b>Поздняя ночь.</b> Скоро утро!"),
])
def test_generate_dynamic_header_for_today(mocker, lessons_sample, mock_time_str, expected_header):
    today = datetime.now(MOSCOW_TZ).date()
    mock_time = time.fromisoformat(mock_time_str)
    mocked_now_dt = datetime.combine(today, mock_time, tzinfo=MOSCOW_TZ)
    
    mocker.patch('bot.text_formatters.datetime', MagicMock(now=lambda tz: mocked_now_dt, strptime=datetime.strptime))

    header, progress_bar = generate_dynamic_header(lessons_sample, today)
    
    assert header == expected_header
    assert "Прогресс дня" in progress_bar

def test_generate_dynamic_header_for_future_day(lessons_sample):
    future_date = datetime.now(MOSCOW_TZ).date() + pytest.importorskip("datetime").timedelta(days=1)
    header, progress_bar = generate_dynamic_header(lessons_sample, future_date)
    assert header == ""
    assert progress_bar == ""

def test_generate_dynamic_header_no_lessons(mocker):
    today = datetime.now(MOSCOW_TZ).date()
    mocked_now_dt = datetime.combine(today, time(12, 0), tzinfo=MOSCOW_TZ)
    mocker.patch('bot.text_formatters.datetime', MagicMock(now=lambda tz: mocked_now_dt))
    
    header, progress_bar = generate_dynamic_header([], today)
    assert "Сегодня занятий нет" in header
    assert progress_bar == ""

def test_format_schedule_text_with_error():
    text = format_schedule_text({'error': 'Группа не найдена'})
    assert "❌ <b>Ошибка:</b> Группа не найдена" in text

class TestNotificationFormatters:
    def test_generate_evening_intro(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        mocker.patch('bot.text_formatters.random.shuffle', lambda x: None)
        
        weather_forecast = {'temperature': -5, 'description': 'снег', 'emoji': '❄️'}
        target_date = datetime(2025, 7, 28)
        
        text = generate_evening_intro(weather_forecast, target_date)
        
        assert "Добрый вечер! 👋" in text
        assert "Завтра понедельник — начинаем неделю с чистого листа!" in text
        assert "Прогноз на завтра: Снег, -5°C" in text
        # ИСПРАВЛЕНИЕ: Проверяем фразу, которая гарантированно будет выбрана моком
        assert "не забудьте шапку и перчатки" in text
        assert "💡 Совет: Соберите рюкзак с вечера" in text

    def test_generate_morning_intro(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        weather_forecast = {'temperature': 22, 'description': 'ясно', 'emoji': '☀️'}
        text = generate_morning_intro(weather_forecast)
        
        assert "Доброе утро! ☀️" in text
        assert "За окном сейчас Ясно, 22°C." in text

    def test_generate_reminder_text(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        lesson = {
            'subject': 'Теория автоматов', 'type': 'Лекция',
            'time': '12:30-14:00', 'room': '505', 'teachers': 'Петров П.П.'
        }
        
        first_text = generate_reminder_text(lesson, "first", None)
        assert "Первая пара через 20 минут!" in first_text

        break_text = generate_reminder_text(lesson, "break", 20)
        assert "Пара закончилась!" in break_text

        final_text = generate_reminder_text(None, "final", None)
        assert "Пары на сегодня всё!" in final_text
        
        invalid_text = generate_reminder_text(lesson, "invalid_type", None)
        assert invalid_text is None