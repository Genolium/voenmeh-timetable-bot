from datetime import date

import pytest

from core.semester_calendar import get_semester_info


class TestSemesterCalendar:
    def test_fall_semester_week_number(self):
        info = get_semester_info(date(2024, 9, 15), date(2024, 9, 1), date(2024, 2, 9))
        assert info.semester == "fall"
        assert info.week_number == 3
        assert info.week_key == "odd"

    def test_spring_semester_week_number(self):
        info = get_semester_info(date(2024, 2, 19), date(2024, 9, 1), date(2024, 2, 9))
        assert info.semester == "spring"
        assert info.week_number == 3
        assert info.week_key == "odd"

    def test_january_belongs_to_fall_semester_of_previous_academic_year(self):
        # Настройки за 2024 год не должны ломать январь 2026.
        info = get_semester_info(date(2026, 1, 15), date(2024, 9, 1), date(2024, 2, 9))
        assert info.semester == "fall"
        assert info.semester_start == date(2025, 9, 1)
        assert info.week_number > 0
        assert info.week_key in {"odd", "even"}

    def test_next_academic_year_fall(self):
        info = get_semester_info(date(2026, 9, 15), date(2024, 9, 1), date(2024, 2, 9))
        assert info.semester == "fall"
        assert info.semester_start == date(2026, 9, 1)

    def test_summer_has_no_week_number(self):
        info = get_semester_info(date(2026, 7, 15), date(2024, 9, 1), date(2024, 2, 9))
        assert info.week_number == 0

    def test_defaults_when_settings_missing(self):
        info = get_semester_info(date(2026, 9, 15))
        assert info.semester == "fall"
        assert info.semester_start == date(2026, 9, 1)
