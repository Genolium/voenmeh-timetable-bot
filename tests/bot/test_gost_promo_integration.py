"""Tests for quiet GOST report menu button and contextual evening lab footer."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs.schedule_view import get_schedule_data, schedul_dialog_windows
from bot.dialogs.states import Schedule
from bot.scheduler import (
    GOST_LAB_PROMO_COOLDOWN_SECONDS,
    GOST_LAB_PROMO_PREFIX,
    _should_show_gost_lab_promo,
    evening_broadcast,
)
from bot.text_formatters import get_footer_with_promo, has_reportable_lessons
from core.config import GOST_REPORT_BOT_LAB_URL, GOST_REPORT_BOT_MENU_URL, MOSCOW_TZ
from core.i18n import i18n


def test_gost_urls_and_locales():
    assert GOST_REPORT_BOT_MENU_URL == "https://t.me/auditorium_404_bot?start=tt_menu"
    assert GOST_REPORT_BOT_LAB_URL == "https://t.me/auditorium_404_bot?start=tt_lab"
    assert i18n.get("btn_gost_report", lang="ru") == "📄 Оформить отчёт по ГОСТ"
    for lang in ("ru", "en", "zh"):
        assert i18n.get("btn_gost_report", lang=lang)
        promo = i18n.get("gost_lab_promo", lang=lang)
        assert "https://t.me/auditorium_404_bot?start=tt_lab" in promo
        assert "@auditorium_404_bot" in promo


def test_main_schedule_menu_contains_gost_url_button_in_separate_row():
    view_window = next(w for w in schedul_dialog_windows if w.state == Schedule.view)
    more_window = next(w for w in schedul_dialog_windows if w.state == Schedule.more)

    def _collect(widget) -> list[str]:
        ids: list[str] = []
        wid = getattr(widget, "widget_id", None)
        if wid:
            ids.append(wid)
        for attr in ("buttons", "widgets"):
            for child in getattr(widget, attr, []) or []:
                ids.extend(_collect(child))
        return ids

    assert "gost_report_url" in _collect(view_window.keyboard)
    assert "gost_report_url" not in _collect(more_window.keyboard)
    assert GOST_LAB_PROMO_COOLDOWN_SECONDS == 4 * 86400


@pytest.mark.asyncio
async def test_get_schedule_data_exposes_gost_button_fields():
    ctx = SimpleNamespace(dialog_data={"group": "О735Б", "current_date_iso": "2026-11-28"})
    dm = MagicMock()
    dm.event = SimpleNamespace(from_user=SimpleNamespace(id=101))
    dm.current_context.return_value = ctx
    dm.start_data = {}
    udm = MagicMock()
    udm.get_user_group = AsyncMock(return_value="О735Б")
    udm.get_user_type = AsyncMock(return_value="student")
    udm.get_user_language = AsyncMock(return_value="ru")
    tm = MagicMock()
    tm._schedules = {"О735Б": {"odd": {}, "even": {}}}
    tm.get_schedule_for_day = AsyncMock(return_value={"lessons": [], "week_name": "Нечётная"})
    tm.get_semester_info_for = AsyncMock(return_value=SimpleNamespace(week_number=1))
    dm.middleware_data = {"manager": tm, "user_data_manager": udm}

    data = await get_schedule_data(dm)
    assert data["gost_report_url"] == "https://t.me/auditorium_404_bot?start=tt_menu"
    assert data["btn_gost_report"] == "📄 Оформить отчёт по ГОСТ"


@pytest.mark.parametrize(
    "lessons,expected",
    [
        (None, False),
        ([], False),
        ([{"type": "лек", "subject": "Программирование"}], False),
        ([{"type": "Лекция", "subject": "Прикладная математика"}], False),
        ([{"type": "лаб", "subject": "Физика"}], True),
        ([{"type": "Лаб.", "subject": "Электротехника"}], True),
        ([{"type": "пр", "subject": "Математика"}], True),
        ([{"type": "Практ.", "subject": "Информатика"}], True),
        ([{"type": "Курс.", "subject": "САПР"}], True),
        ([{"type": "", "subject": "Курсовой проект по деталям машин"}], True),
        ([{"type": "лек", "subject": "Лабораторный практикум"}], True),
    ],
)
def test_has_reportable_lessons(lessons, expected):
    assert has_reportable_lessons(lessons) is expected


def test_get_footer_with_promo_prefers_gost_lab_promo_without_stacking(monkeypatch):
    monkeypatch.setattr("bot.text_formatters.random.random", lambda: 0.0)
    footer = get_footer_with_promo("ru", show_gost_lab_promo=True)
    assert "auditorium_404_bot?start=tt_lab" in footer
    assert "Завтра лаба?" in footer
    assert "voenmeh404" not in footer


@pytest.mark.asyncio
async def test_should_show_gost_lab_promo_guards_and_cooldown():
    lab_lessons = [{"type": "лаб", "subject": "Физика"}]
    lec_lessons = [{"type": "лек", "subject": "Физика"}]

    redis_ok = AsyncMock()
    redis_ok.set = AsyncMock(return_value=True)
    tm = SimpleNamespace(redis=redis_ok)

    # Group chats, teachers, non-reportable days, missing redis
    assert await _should_show_gost_lab_promo(tm, -1001, "student", lab_lessons) is False
    assert await _should_show_gost_lab_promo(tm, 42, "teacher", lab_lessons) is False
    assert await _should_show_gost_lab_promo(tm, 42, "student", lec_lessons) is False
    assert await _should_show_gost_lab_promo(SimpleNamespace(redis=None), 42, "student", lab_lessons) is False

    # First claim within 8-day window succeeds
    assert await _should_show_gost_lab_promo(tm, 42, "student", lab_lessons) is True
    redis_ok.set.assert_awaited_once_with(
        f"{GOST_LAB_PROMO_PREFIX}42",
        "1",
        nx=True,
        ex=GOST_LAB_PROMO_COOLDOWN_SECONDS,
    )

    # Subsequent claim within cooldown returns False
    redis_ok.set = AsyncMock(return_value=None)
    assert await _should_show_gost_lab_promo(tm, 42, "student", lab_lessons) is False

    # Redis error fails closed
    redis_err = AsyncMock()
    redis_err.set = AsyncMock(side_effect=RuntimeError("redis down"))
    assert await _should_show_gost_lab_promo(SimpleNamespace(redis=redis_err), 42, "student", lab_lessons) is False


@pytest.mark.asyncio
async def test_evening_broadcast_includes_gost_footer_once_per_cooldown():
    udm = AsyncMock()
    udm.get_users_for_evening_notify.return_value = [(777, "О735Б", "ru", "student")]
    udm.async_session_maker = None

    tm = MagicMock()
    tm.redis = AsyncMock()
    tm.redis.set = AsyncMock(side_effect=[True, None])
    tm.get_schedule_for_day = AsyncMock(
        return_value={
            "date": datetime.now(MOSCOW_TZ).date(),
            "day_name": "Понедельник",
            "lessons": [
                {
                    "time": "09:00-10:30",
                    "type": "лаб",
                    "subject": "Физика",
                    "start_time_raw": "09:00",
                    "end_time_raw": "10:30",
                    "room": "101",
                    "teachers": "Иванов И.И.",
                }
            ],
        }
    )

    sent_texts: list[str] = []
    with (
        patch("bot.scheduler.WeatherAPI") as mock_w,
        patch("bot.scheduler.send_message_task.send", side_effect=lambda _uid, txt: sent_texts.append(txt)),
    ):
        mock_w.return_value.get_forecast_for_time = AsyncMock(return_value=None)
        mock_w.return_value.get_day_weather_window = AsyncMock(return_value=None)
        await evening_broadcast(udm, tm)
        await evening_broadcast(udm, tm)

    assert len(sent_texts) == 2
    assert "auditorium_404_bot?start=tt_lab" in sent_texts[0]
    assert "auditorium_404_bot?start=tt_lab" not in sent_texts[1]
