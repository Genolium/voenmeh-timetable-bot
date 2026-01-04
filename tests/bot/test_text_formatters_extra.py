from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pytest
from bot.text_formatters import (
    format_schedule_text,
    format_teacher_schedule_text,
    format_full_week_text,
    generate_dynamic_header,
)
from core.config import MOSCOW_TZ

class TestLocalizationSupport:
    def test_format_schedule_text_en(self):
        # Mock i18n to return English-like strings or verify key usage
        # Ideally we use real i18n if loaded. 
        # But to be safe and unit-testy, we can mock i18n.get
        with patch("bot.text_formatters.i18n.get") as mock_get:
            mock_get.side_effect = lambda key, lang="ru": f"[{lang}:{key}]"
            
            day_info = {
                "date": date(2025, 1, 1),
                "day_name": "Wednesday",
                "lessons": []
            }
            text = format_schedule_text(day_info, lang="en")
            
            assert "[en:fmt_no_lessons]" in text
            assert "[en:fmt_date_missing]" not in text

    def test_format_teacher_schedule_text_en(self):
        with patch("bot.text_formatters.i18n.get") as mock_get:
            mock_get.side_effect = lambda key, lang="ru": f"[{lang}:{key}]"
            
            info = {
                "teacher": "Smith",
                "date": date(2025, 1, 1),
                "day_name": "Wednesday",
                "lessons": []
            }
            text = format_teacher_schedule_text(info, lang="en")
            
            assert "[en:fmt_no_lessons]" in text

    def test_format_full_week_text_en(self):
        with patch("bot.text_formatters.i18n.get") as mock_get:
            mock_get.side_effect = lambda key, lang="ru": f"[{lang}:{key}]"
            
            week = {}
            text = format_full_week_text(week, "odd", lang="en")
            
            # The key for no lessons in full week might be different ('fmt_week_no_lessons')
            assert "[en:fmt_week_no_lessons]" in text


class TestDynamicHeaderLogic:
    def test_progress_bar_calculation(self, mocker):
        # We need to mock datetime.now but preserve datetime.strptime
        # Since datetime is a class, we can't easily patch just one method.
        # We replace the whole class with a mock that delegates everything but 'now' to real datetime.
        real_datetime = datetime

        class MockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls._mock_now
            
        MockDatetime.strptime = real_datetime.strptime
        
        # Test specific percentage points
        lessons = [
            {"time": "10:00-11:30", "start_time_raw": "10:00", "end_time_raw": "11:30", "subject": "Math"}
        ]
        today = real_datetime.now(MOSCOW_TZ).date()
        
        # 1. Before start (10 mins before)
        MockDatetime._mock_now = real_datetime.combine(today, time(9, 50), tzinfo=MOSCOW_TZ)
        with patch("bot.text_formatters.datetime", MockDatetime):
            header, bar = generate_dynamic_header(lessons, today)
            assert "100%" not in bar
        
        # 2. 50% through (10:45)
        MockDatetime._mock_now = real_datetime.combine(today, time(10, 45), tzinfo=MOSCOW_TZ)
        with patch("bot.text_formatters.datetime", MockDatetime):
            header, bar = generate_dynamic_header(lessons, today)
            # Lesson is ongoing, so header says "It's running"
            assert "Идет пара" in header or "Math" in header
            # Progress bar counts ONLY fully completed lessons, so 0/1
            assert "0/1" in bar
        
        # 3. Finished
        MockDatetime._mock_now = real_datetime.combine(today, time(12, 00), tzinfo=MOSCOW_TZ)
        with patch("bot.text_formatters.datetime", MockDatetime):
            header, bar = generate_dynamic_header(lessons, today)
            assert "Пары на сегодня закончились" in header
