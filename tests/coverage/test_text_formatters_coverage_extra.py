"""Extra coverage for bot/text_formatters.py.

Focuses on the remaining error/edge branches: unknown room markers, week names,
dynamic-header fallback, promo footer, non-list locale values and reminder
variants. No network or DB access is involved.
"""

from datetime import date, datetime, time
from unittest.mock import MagicMock

import pytest

import bot.text_formatters as tf
from bot.text_formatters import (
    format_full_week_text,
    format_schedule_text,
    format_teacher_schedule_text,
    generate_dynamic_header,
    generate_evening_intro,
    generate_morning_intro,
    generate_reminder_text,
    get_footer_with_promo,
)
from core.config import MOSCOW_TZ


# --------------------------------------------------------------------------- #
# Room "not specified" handling (99, 153, 258)
# --------------------------------------------------------------------------- #
def test_format_schedule_text_room_not_specified():
    info = {
        "date": date(2025, 1, 1),
        "week_name": "Четная",
        "lessons": [
            {"time": "09:00-10:30", "subject": "Матан", "room": "Кабинет не указан"},
        ],
    }
    text = format_schedule_text(info)
    assert "Кабинет не указан" in text
    assert "📍" in text


def test_format_teacher_schedule_text_room_not_specified():
    info = {
        "teacher": "Иванов",
        "date": date(2025, 1, 1),
        "lessons": [
            {"time": "09:00-10:30", "subject": "Матан", "room": "кабинет не указан"},
            {"time": "10:40-12:10", "subject": "Физика", "room": "101"},
        ],
    }
    text = format_teacher_schedule_text(info)
    assert "📍" in text
    assert "101" in text


def test_format_full_week_text_room_not_specified_and_bad_time():
    week = {
        "Понедельник": [
            # Bad time forces the (ValueError, IndexError) fallback sorting path.
            {"time": "n/a", "subject": "Матан", "room": "Кабинет не указан"},
        ],
        "Суббота": [
            {"time": "10:40-12:10", "subject": "Физика", "room": "202"},
        ],
    }
    text = format_full_week_text(week, "четная")
    assert "Кабинет не указан" in text
    assert "202" in text


# --------------------------------------------------------------------------- #
# Unknown week name in day formatter
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("week_name", ["Четная", "Нечетная", "Something else"])
def test_format_schedule_text_week_names(week_name):
    info = {
        "date": date(2025, 1, 1),
        "week_name": week_name,
        "lessons": [{"time": "09:00-10:30", "subject": "Матан"}],
    }
    text = format_schedule_text(info, week_number=5)
    assert "Неделя" in text


# --------------------------------------------------------------------------- #
# Dynamic header fallback (345)
# --------------------------------------------------------------------------- #
def test_generate_dynamic_header_falls_through(monkeypatch):
    """Force a non-covering, non-break lesson window.

    ``datetime.strptime`` is fed a call-order sequence so that the lesson's
    parsed window never contains "now" while the header still runs the full
    scan. This is a defensive fallback path that is unreachable with a single
    consistent parse, hence the deliberate sequence.
    """
    today = datetime.now(MOSCOW_TZ).date()
    now_dt = datetime.combine(today, time(10, 30), tzinfo=MOSCOW_TZ)

    class FakeDatetime:
        times = iter([time(9), time(10), time(9), time(11), time(13), time(14)])

        @classmethod
        def now(cls, tz=None):
            return now_dt

        @classmethod
        def strptime(cls, value, fmt):
            return datetime.combine(today, next(cls.times))

        @classmethod
        def combine(cls, d, t):
            return datetime.combine(d, t)

    monkeypatch.setattr(tf, "datetime", FakeDatetime)

    lessons = [
        {
            "time": "09:00-10:00",
            "start_time_raw": "09:00",
            "end_time_raw": "10:00",
            "subject": "Матан",
        }
    ]
    header, bar = generate_dynamic_header(lessons, today)
    assert header == ""
    assert "Прогресс" in bar


# --------------------------------------------------------------------------- #
# Promo footer (364-365)
# --------------------------------------------------------------------------- #
def test_footer_with_promo_when_random_low(monkeypatch):
    monkeypatch.setattr(tf.random, "random", lambda: 0.1)
    footer = get_footer_with_promo("ru")
    assert "t.me/voenmeh404" in footer


def test_footer_without_promo_when_random_high(monkeypatch):
    monkeypatch.setattr(tf.random, "random", lambda: 0.9)
    footer = get_footer_with_promo("ru")
    assert "t.me/voenmeh404" not in footer


# --------------------------------------------------------------------------- #
# Evening / morning intro fallbacks (461, 523) and weather branches
# --------------------------------------------------------------------------- #
def _fake_get(overrides):
    original = tf.i18n.get

    def fake_get(key, lang="ru", **kwargs):
        if key in overrides:
            return overrides[key]
        return original(key, lang, **kwargs)

    return fake_get


def test_evening_intro_non_list_greetings(monkeypatch):
    monkeypatch.setattr(tf.i18n, "get", _fake_get({"evening_greetings": "not-a-list"}))
    text = generate_evening_intro(None, datetime.now(MOSCOW_TZ))
    assert isinstance(text, str)


@pytest.mark.parametrize("temp", [0, -10, 5, 15, 25])
def test_evening_intro_clothing_advice_temperatures(temp):
    text = generate_evening_intro(
        {"temperature": temp, "description": "ясно", "emoji": "☀️"},
        datetime.now(MOSCOW_TZ),
        user_type="student",
    )
    assert text


def test_evening_intro_missing_weather_fields():
    text = generate_evening_intro({"temperature": 7}, datetime.now(MOSCOW_TZ))
    assert text


def test_evening_intro_teacher_weather():
    text = generate_evening_intro(
        {"temperature": 3, "description": "снег", "emoji": "🌨"},
        datetime.now(MOSCOW_TZ),
        user_type="teacher",
    )
    assert "3" in text


def test_morning_intro_non_list_greetings(monkeypatch):
    monkeypatch.setattr(tf.i18n, "get", _fake_get({"morning_greetings": "oops"}))
    text = generate_morning_intro(None)
    assert isinstance(text, str)


def test_morning_intro_with_weather_and_teacher():
    student_text = generate_morning_intro({"temperature": 12, "description": "дождь"})
    teacher_text = generate_morning_intro({"temperature": 12, "description": "дождь"}, user_type="teacher")
    assert "12" in student_text
    assert "12" in teacher_text


# --------------------------------------------------------------------------- #
# Reminder texts (554, 602) and final/else branches
# --------------------------------------------------------------------------- #
def test_reminder_first_non_list_locale(monkeypatch):
    monkeypatch.setattr(
        tf.i18n,
        "get",
        _fake_get({"reminder_first": "First lesson in {minutes} minutes!"}),
    )
    lesson = {"subject": "Матан", "type": "Лекция", "time": "09:00-10:30", "room": "101"}
    text = generate_reminder_text(lesson, "first", None, reminder_time_minutes=15)
    # A non-list locale value falls back to the built-in default greeting.
    assert "First lesson soon!" in text


def test_reminder_first_without_placeholder(monkeypatch):
    monkeypatch.setattr(tf.i18n, "get", _fake_get({"reminder_first": "Hurry up, lesson soon!"}))
    lesson = {"subject": "Матан", "time": "09:00-10:30"}
    text = generate_reminder_text(lesson, "first", None)
    assert "Матан" in text


def test_reminder_final_non_list_locale(monkeypatch):
    monkeypatch.setattr(tf.i18n, "get", _fake_get({"reminder_final": "All done"}))
    text = generate_reminder_text(None, "final", None)
    assert text is not None
    assert "Final!" in text  # fallback used for non-list locale values


def test_reminder_final_with_lesson():
    lesson = {"subject": "Физика", "time": "10:40-12:10"}
    text = generate_reminder_text(lesson, "final", None)
    assert text is not None
    # "final" ignores lesson details and only sends the closing phrase.
    assert "🎉" in text
    assert "Физика" not in text


def test_reminder_unknown_type_returns_none():
    assert generate_reminder_text({"subject": "X"}, "unknown", None) is None


@pytest.mark.parametrize("duration", [None, 5, 20, 45])
def test_reminder_break_durations(duration):
    lesson = {"subject": "Матан", "type": "Лекция", "time": "10:40-12:10", "room": "101"}
    text = generate_reminder_text(lesson, "break", duration)
    assert text is not None
    assert "Матан" in text
