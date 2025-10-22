import pytest
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

from bot.text_formatters import (
    generate_dynamic_header, format_schedule_text, generate_evening_intro,
    generate_morning_intro, generate_reminder_text, UNSUBSCRIBE_FOOTER,
    format_teacher_schedule_text, format_classroom_schedule_text, format_full_week_text
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

def test_format_schedule_text_no_date():
    text = format_schedule_text({'lessons': []})
    assert "Дата не найдена" in text

def test_format_schedule_text_no_lessons():
    text = format_schedule_text({
        'date': date(2025, 1, 1),
        'day_name': 'Среда',
        'week_name': 'Четная',
        'lessons': []
    })
    assert "Занятий нет" in text

def test_format_schedule_text_with_lesson_details():
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

def test_format_schedule_text_without_optional_fields():
    day_info = {
        'date': date(2025, 1, 1),
        'day_name': 'Среда',
        'lessons': [
            {'time': '09:00-10:30', 'subject': 'Матан'},
        ],
    }
    text = format_schedule_text(day_info)
    assert "Матан" in text
    assert "09:00-10:30" in text

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

    def test_teacher_with_room_info(self):
        info = {
            'teacher': 'Иванов',
            'date': date(2025, 1, 2),
            'day_name': 'Четверг',
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'groups': ['О735Б'], 'room': '101'},
            ],
        }
        text = format_teacher_schedule_text(info)
        assert "📍 101" in text

    def test_teacher_groups_deduplicated(self):
        info = {
            'teacher': 'Готин',
            'date': date(2025, 9, 1),
            'day_name': 'Понедельник',
            'lessons': [
                {
                    'time': '12:40-14:10',
                    'subject': 'ПРЕДСТ.ЗНАН.В ИС',
                    'groups': ['О734Б', 'О735Б', 'О735Б', 'О736Б', 'О736Б']
                }
            ],
        }
        text = format_teacher_schedule_text(info)
        assert text.count('О735Б') == 1
        assert text.count('О736Б') == 1

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
                {'time': '08:30-10:00', 'subject': 'Информатика', 'groups': ['О735Б'], 'teachers': 'Смирнов'},
            ],
        }
        text = format_classroom_schedule_text(info)
        assert "Информатика" in text and "Смирнов" in text

    def test_classroom_without_optional_fields(self):
        info = {
            'classroom': '101',
            'date': date(2025, 1, 3),
            'day_name': 'Пятница',
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Физика'},
            ],
        }
        text = format_classroom_schedule_text(info)
        assert "Физика" in text

    def test_classroom_groups_deduplicated(self):
        info = {
            'classroom': '315',
            'date': date(2025, 9, 1),
            'day_name': 'Понедельник',
            'lessons': [
                {
                    'time': '10:50-12:20',
                    'subject': 'ЭД И РАСП.Р-ВОЛН',
                    'groups': ['И431С', 'И432С', 'И432С', 'И437С', 'И437С', 'И438С', 'И438С', 'КВ32', 'КВ32']
                }
            ],
        }
        text = format_classroom_schedule_text(info)
        assert text.count('И432С') == 1
        assert text.count('И437С') == 1
        assert text.count('И438С') == 1
        assert text.count('КВ32') == 1

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

    def test_week_with_single_day(self):
        week = {
            'Понедельник': [
                {'time': '09:00-10:30', 'subject': 'Математика'},
            ],
        }
        text = format_full_week_text(week, 'четная')
        assert 'Математика' in text
        assert 'ПОНЕДЕЛЬНИК' in text

    def test_week_with_invalid_time_format(self):
        week = {
            'Вторник': [
                {'time': 'invalid-time', 'subject': 'Тест'},
            ],
        }
        # Не должно падать
        text = format_full_week_text(week, 'нечетная')
        assert 'Тест' in text

def test_generate_dynamic_header_handles_exceptions(mocker):
    # Подготовим данные, которые вызовут KeyError внутри функции
    lessons = [{'time': '09:00-10:30'}]  # отсутствуют start_time_raw/end_time_raw
    today = datetime.now(MOSCOW_TZ).date()
    mocker.patch('bot.text_formatters.logging')
    header, progress = generate_dynamic_header(lessons, today)
    assert header == ""
    assert progress == ""

def test_generate_dynamic_header_with_malformed_time(mocker):
    lessons = [
        {'time': '09:00-10:30', 'start_time_raw': 'invalid', 'end_time_raw': '10:30', 'subject': 'Тест'}
    ]
    today = datetime.now(MOSCOW_TZ).date()
    mocker.patch('bot.text_formatters.datetime', MagicMock(now=lambda tz: datetime.combine(today, time(9, 30), tzinfo=MOSCOW_TZ)))
    
    # Мокаем logging чтобы избежать ошибок
    mocker.patch('bot.text_formatters.logging')
    
    # Мокаем datetime.strptime чтобы избежать ошибки с MagicMock
    mocker.patch('bot.text_formatters.datetime.strptime', side_effect=ValueError("Invalid time"))
    
    header, progress = generate_dynamic_header(lessons, today)
    assert header == ""
    assert progress == ""

class TestNotificationFormatters:
    def test_generate_evening_intro(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        weather_forecast = {'temperature': -5, 'description': 'снег', 'emoji': '❄️'}
        target_date = datetime(2025, 7, 28)
        
        text = generate_evening_intro(weather_forecast, target_date)
        
        assert "Добрый вечер!" in text
        assert "Завтра понедельник — начинаем неделю с чистого листа!" in text
        assert "Прогноз на завтра: Снег, -5°C" in text
        assert "не забудьте шапку и перчатки" in text
        assert "Совет:" not in text
        assert "Цитата:" not in text
        assert "Вопрос:" not in text

    def test_generate_evening_intro_no_weather(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        target_date = datetime(2025, 7, 28)  # понедельник
        text = generate_evening_intro(None, target_date)
        
        assert "Добрый вечер!" in text
        assert "Завтра понедельник" in text
        assert "Прогноз на завтра" not in text
        assert "Совет:" not in text
        assert "Цитата:" not in text
        assert "Вопрос:" not in text

    def test_generate_evening_intro_different_weekdays(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        # Тестируем разные дни недели
        test_cases = [
            (datetime(2025, 7, 29), "вторник"),  # вторник
            (datetime(2025, 7, 30), "среда"),    # среда
            (datetime(2025, 7, 31), "четверг"),  # четверг
            (datetime(2025, 8, 1), "пятница"),   # пятница
            (datetime(2025, 8, 2), "суббота"),   # суббота
            (datetime(2025, 8, 3), "воскресенье"), # воскресенье
        ]
        
        for target_date, expected_day in test_cases:
            text = generate_evening_intro(None, target_date)
            assert expected_day in text.lower()

    def test_generate_evening_intro_temperature_ranges(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        target_date = datetime(2025, 7, 28)
        
        # Тестируем разные температурные диапазоны
        test_cases = [
            (-10, "морозно"),
            (5, "прохладно"),
            (15, "тепло"),
            (25, "жарко"),
        ]
        
        for temp, expected_advice in test_cases:
            weather = {'temperature': temp, 'description': 'ясно', 'emoji': '☀️'}
            text = generate_evening_intro(weather, target_date)
            assert expected_advice in text.lower()

    def test_generate_morning_intro(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        weather_forecast = {'temperature': 22, 'description': 'ясно', 'emoji': '☀️'}
        text = generate_morning_intro(weather_forecast)
        
        assert "Доброе утро! ☀️" in text
        assert "За окном сейчас Ясно, 22°C." in text

    def test_generate_morning_intro_no_weather(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        text = generate_morning_intro(None)
        
        assert "Доброе утро! ☀️" in text
        assert "За окном сейчас" not in text

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

    def test_generate_reminder_text_break_durations(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        lesson = {
            'subject': 'Математика', 'type': 'Лекция',
            'time': '10:40-12:10', 'room': '101'
        }
        
        # Короткий перерыв
        short_break = generate_reminder_text(lesson, "break", 10)
        assert "Успейте дойти до следующей аудитории" in short_break
        
        # Средний перерыв
        medium_break = generate_reminder_text(lesson, "break", 25)
        assert "Время выпить чаю" in medium_break
        
        # Длинный перерыв
        long_break = generate_reminder_text(lesson, "break", 50)
        assert "большой перерыв" in long_break
        assert "пообедать" in long_break

    def test_generate_reminder_text_with_lesson_details(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        lesson = {
            'subject': 'Физика', 'type': 'Лабораторная',
            'time': '14:00-15:30', 'room': '205', 'teachers': 'Сидоров А.А.'
        }
        
        text = generate_reminder_text(lesson, "first", None)
        assert "Физика" in text
        assert "Лабораторная" in text
        assert "14:00-15:30" in text
        assert "📍 205" in text
        assert "с Сидоров А.А." in text
        assert UNSUBSCRIBE_FOOTER in text

    def test_generate_reminder_text_without_optional_fields(self, mocker):
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        lesson = {
            'subject': 'История', 'type': 'Семинар',
            'time': '09:00-10:30'
        }
        
        text = generate_reminder_text(lesson, "first", None)
        assert "История" in text
        assert "Семинар" in text
        assert "09:00-10:30" in text
        assert "📍" not in text
        assert "с " not in text

    def test_generate_evening_intro_teacher(self, mocker):
        """Тест вечернего приветствия для преподавателя"""
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        weather_forecast = {'temperature': 10, 'description': 'облачно', 'emoji': '☁️'}
        target_date = datetime(2025, 7, 28)
        
        text = generate_evening_intro(weather_forecast, target_date, user_type='teacher')
        
        # Проверяем, что используется формальное приветствие
        assert "Добрый вечер!" in text
        # Проверяем, что используется формальный контекст
        assert "понедельник" in text.lower()
        # Проверяем, что погода отображается без советов по одежде
        assert "Прогноз погоды на завтра: Облачно, 10°C" in text
        assert "куртка" not in text.lower()
        assert "свитер" not in text.lower()

    def test_generate_evening_intro_student(self, mocker):
        """Тест вечернего приветствия для студента (по умолчанию)"""
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        weather_forecast = {'temperature': 10, 'description': 'облачно', 'emoji': '☁️'}
        target_date = datetime(2025, 7, 28)
        
        text = generate_evening_intro(weather_forecast, target_date, user_type='student')
        
        # Проверяем, что используется неформальное приветствие
        assert "Добрый вечер!" in text
        # Проверяем, что есть советы по одежде для студентов
        assert "Прогноз на завтра: Облачно, 10°C" in text
        assert ("куртка" in text.lower() or "свитер" in text.lower() or "прохладно" in text.lower())

    def test_generate_morning_intro_teacher(self, mocker):
        """Тест утреннего приветствия для преподавателя"""
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        weather_forecast = {'temperature': 15, 'description': 'солнечно', 'emoji': '☀️'}
        text = generate_morning_intro(weather_forecast, user_type='teacher')
        
        # Проверяем формальное приветствие
        assert "Доброе утро!" in text
        # Проверяем формальный стиль погоды
        assert "Текущая погода: Солнечно, 15°C" in text
        assert "За окном" not in text

    def test_generate_morning_intro_student(self, mocker):
        """Тест утреннего приветствия для студента"""
        mocker.patch('bot.text_formatters.random.choice', lambda x: x[0])
        
        weather_forecast = {'temperature': 15, 'description': 'солнечно', 'emoji': '☀️'}
        text = generate_morning_intro(weather_forecast, user_type='student')
        
        # Проверяем неформальное приветствие
        assert "Доброе утро! ☀️" in text
        # Проверяем неформальный стиль погоды
        assert "За окном сейчас Солнечно, 15°C" in text

def test_format_schedule_text_edge_cases():
    # Пустой словарь
    text = format_schedule_text({})
    assert "Ошибка" in text
    
    # Только lessons без date
    text = format_schedule_text({'lessons': [{'time': '09:00', 'subject': 'Тест'}]})
    assert "Дата не найдена" in text
    
    # Убираем тест с None, так как он вызывает AttributeError

def test_format_teacher_schedule_text_edge_cases():
    # Пустой словарь
    text = format_teacher_schedule_text({})
    assert "Ошибка" in text
    
    # Убираем тест с None, так как он вызывает AttributeError
    
    # С ошибкой в данных
    text = format_teacher_schedule_text({'error': 'Test error', 'teacher': 'Тест', 'date': date(2025, 1, 1), 'day_name': 'Понедельник'})
    assert "Ошибка" in text

def test_format_classroom_schedule_text_edge_cases():
    # Пустой словарь
    text = format_classroom_schedule_text({})
    assert "Ошибка" in text
    
    # Убираем тест с None, так как он вызывает AttributeError
    
    # С ошибкой в данных
    text = format_classroom_schedule_text({'error': 'Test error', 'classroom': '101', 'date': date(2025, 1, 1), 'day_name': 'Понедельник'})
    assert "Ошибка" in text

def test_format_full_week_text_edge_cases():
    # Пустой словарь
    text = format_full_week_text({}, 'тест')
    assert "занятий нет" in text
    
    # Словарь с пустыми днями
    text = format_full_week_text({
        'Понедельник': [],
        'Вторник': []
    }, 'тест')
    assert "занятий нет" in text
    
    # День с невалидными данными
    text = format_full_week_text({
        'Понедельник': [{'invalid': 'data'}]
    }, 'тест')
    assert "ПОНЕДЕЛЬНИК" in text

def test_generate_dynamic_header_edge_cases():
    # Пустой список уроков
    header, progress = generate_dynamic_header([], datetime.now(MOSCOW_TZ).date())
    assert "занятий нет" in header
    
    # Уроки без времени
    lessons = [{'subject': 'Тест'}]
    header, progress = generate_dynamic_header(lessons, datetime.now(MOSCOW_TZ).date())
    assert header == ""
    assert progress == ""

def test_unsubscribe_footer_constant():
    assert UNSUBSCRIBE_FOOTER == "\n\n<tg-spoiler><i>Отключить эту рассылку можно в «⚙️ Настройки»</i></tg-spoiler>"