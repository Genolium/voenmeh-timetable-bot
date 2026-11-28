from datetime import date

import pytest

from core.summary_advisor import (
    analyze_lessons_window,
    build_schedule_briefing,
    build_weather_summary_block,
)


def test_analyze_lessons_window_empty_and_malformed():
    empty = analyze_lessons_window([])
    assert empty["count"] == 0
    assert empty["start_hour"] == 10
    assert empty["end_hour"] == 18

    bad = analyze_lessons_window([{"subject": "X", "time": "bad"}])
    assert bad["count"] == 1
    assert bad["first_start"] == ""

    fallback_time = analyze_lessons_window(
        [
            {"subject": "A", "time": "12:40 – 14:10"},
            {"subject": "B", "time": "16:45 - 18:15"},
        ]
    )
    assert fallback_time["count"] == 2
    assert fallback_time["first_start"] == "12:40"
    assert fallback_time["last_end"] == "18:15"
    assert fallback_time["max_gap_minutes"] == 155
    assert fallback_time["end_hour"] == 18


def test_build_schedule_briefing_variants():
    assert build_schedule_briefing([]) == ""
    assert build_schedule_briefing([{"subject": "Bad", "time": "bad"}]) == ""

    dense_lessons = [
        {"start_time_raw": "09:00", "end_time_raw": "10:30"},
        {"start_time_raw": "10:50", "end_time_raw": "12:20"},
        {"start_time_raw": "12:40", "end_time_raw": "14:10"},
        {"start_time_raw": "14:30", "end_time_raw": "16:00"},
    ]
    ru_dense = build_schedule_briefing(dense_lessons, lang="ru")
    assert "4 пары" in ru_dense
    assert "ранний подъём" in ru_dense
    assert "без окон" in ru_dense

    late_lessons = [
        {"start_time_raw": "12:40", "end_time_raw": "14:10"},
        {"start_time_raw": "16:00", "end_time_raw": "17:30"},
        {"start_time_raw": "18:30", "end_time_raw": "20:00"},
    ]
    ru_late = build_schedule_briefing(late_lessons, lang="ru")
    assert "3 пары" in ru_late
    assert "можно выспаться" in ru_late

    single_late_end = [{"start_time_raw": "18:30", "end_time_raw": "20:00"}]
    assert "допоздна" in build_schedule_briefing(single_late_end, lang="ru")
    assert "1 class" in build_schedule_briefing(single_late_end, lang="en")
    assert "1节课" in build_schedule_briefing(single_late_end, lang="zh")

    # Teacher skips student "sleep in" note
    teacher_brief = build_schedule_briefing([{"start_time_raw": "12:40", "end_time_raw": "14:10"}], user_type="teacher")
    assert "можно выспаться" not in teacher_brief
    assert "1 пара" in teacher_brief

    gap_60 = [
        {"start_time_raw": "09:00", "end_time_raw": "10:00"},
        {"start_time_raw": "11:00", "end_time_raw": "12:30"},
    ]
    assert "окно 1 ч" in build_schedule_briefing(gap_60, lang="ru")
    assert "окно 45 мин" in build_schedule_briefing(
        [
            {"start_time_raw": "09:00", "end_time_raw": "10:00"},
            {"start_time_raw": "10:45", "end_time_raw": "12:15"},
        ],
        lang="ru",
    )


def test_build_weather_summary_block_seasonal_and_langs():
    assert build_weather_summary_block(None, target_date=date(2026, 11, 25)) == ""

    # Nov 25, +1°C snow -> NEVER says "Осенняя прохлада"
    snow_nov = {
        "start": {"temperature": 1, "feels_like": -4, "description": "небольшой снег", "emoji": "🌨️", "pop": 80},
        "end": {"temperature": -2, "feels_like": -6, "description": "снег", "emoji": "🌨️", "pop": 60},
        "wind_speed": 9,
        "max_pop": 80,
        "precip_kind": "snow",
    }
    txt = build_weather_summary_block(snow_nov, target_date=date(2026, 11, 25), user_type="student", lang="ru")
    assert "Осенняя прохлада" not in txt
    assert "1°C → -2°C" in txt
    assert "ощущается как -4°C" in txt
    assert "ветер до 9 м/с" in txt
    assert "осадки 80%" in txt
    assert "снег и скользкие тротуары" in txt.lower()

    # Teacher variant + storm + rain + cold/warm/hot tiers across ru/en/zh
    for lang in ("ru", "en", "zh"):
        for kind, temp in (
            ("storm", 18),
            ("snow", -1),
            ("rain", 10),
            (None, -12),
            (None, 0),
            (None, 4),
            (None, 12),
            (None, 20),
            (None, 28),
        ):
            w = {"temperature": temp, "feels_like": temp, "description": "test", "emoji": "☀️", "precip_kind": kind}
            out = build_weather_summary_block(
                w, target_date=date(2026, 11, 25), user_type="student", lang=lang, is_morning=True
            )
            assert out
            out_t = build_weather_summary_block(w, target_date=date(2026, 5, 10), user_type="teacher", lang=lang)
            assert out_t

    # High pop infers snow/rain when precip_kind is None
    inferred_snow = build_weather_summary_block(
        {"temperature": 0, "feels_like": 0, "description": "пасмурно", "emoji": "☁️", "max_pop": 70},
        target_date=date(2026, 12, 1),
    )
    assert "снег" in inferred_snow.lower()

    # Extra branch coverage: 5 lessons ("пар"), en/zh durations, 10:00 start, <2°C delta, neutral temp
    from core.summary_advisor import _format_duration

    assert _format_duration(75, "en") == "1h 15m"
    assert _format_duration(30, "en") == "30m"
    assert _format_duration(75, "zh") == "1小时15分"
    assert _format_duration(30, "zh") == "30分钟"

    five_lessons = [{"time": f"{9 + i}:00-{9 + i}:30"} for i in range(5)]
    assert "5 пар" in build_schedule_briefing(five_lessons, lang="ru")
    mid_day = [{"time": "10:00-11:30"}]
    assert "10:00–11:30" in build_schedule_briefing(mid_day, user_type="student", lang="ru")

    small_delta = build_weather_summary_block(
        {
            "start": {"temperature": 16, "feels_like": 16, "description": "ясно", "emoji": "☀️"},
            "end": {"temperature": 17},
        },
        target_date=date(2026, 5, 10),
        user_type="student",
        lang="ru",
    )
    assert "16°C" in small_delta and "→" not in small_delta
