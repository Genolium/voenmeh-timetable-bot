"""Full statement coverage tests for bot/handlers/inline_handlers.py."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers import inline_handlers as ih
from bot.handlers.inline_handlers import (
    DAY_ALIASES,
    _send_schedule_result,
    inline_query_handler,
    parse_day_from_query,
)
from core.config import MOSCOW_TZ


def _today():
    return datetime.now(MOSCOW_TZ).date()


class FakeManager:
    def __init__(self, schedules, schedule_info=None):
        self._schedules = schedules
        self.redis = None
        self._schedule_info = schedule_info or {
            "date": _today(),
            "day_name": "День",
            "lessons": [{"time": "9:00-10:30", "subject": "Математика"}],
        }

    async def get_schedule_for_day(self, group_name, target_date):
        return self._schedule_info


def _query(text, lang="ru"):
    q = MagicMock()
    q.query = text
    q.answer = AsyncMock()
    q.from_user = MagicMock()
    q.from_user.language_code = lang
    return q


class TestParseDay:
    def test_today_aliases(self):
        today = _today()
        for alias in ["сегодня", "today", "今天"]:
            d, parts = parse_day_from_query([alias, "Группа"])
            assert d == today
            assert parts == ["Группа"]

    def test_tomorrow_aliases(self):
        today = _today()
        for alias in ["завтра", "tomorrow", "tmr", "明天"]:
            d, parts = parse_day_from_query([alias, "Группа"])
            assert d == today + timedelta(days=1)
            assert parts == ["Группа"]

    def test_weekday_alias_in_future(self):
        today = _today()
        # pick a weekday strictly after today to hit the days_diff >= 0 branch
        target = (today.weekday() + 2) % 7
        alias = next(name for name, num in DAY_ALIASES.items() if num == target and name.isascii())
        d, parts = parse_day_from_query([alias, "Группа"])
        expected_diff = target - today.weekday()
        if expected_diff < 0:
            expected_diff += 7
        assert d == today + timedelta(days=expected_diff)
        assert parts == ["Группа"]

    def test_weekday_alias_already_passed(self):
        today = _today()
        # pick a weekday strictly before today to hit the days_diff < 0 branch
        target = (today.weekday() - 1) % 7
        alias = next(name for name, num in DAY_ALIASES.items() if num == target and name.isascii())
        d, parts = parse_day_from_query([alias, "Группа"])
        expected_diff = target - today.weekday()
        if expected_diff < 0:
            expected_diff += 7
        assert d == today + timedelta(days=expected_diff)

    def test_no_day_defaults_to_today(self):
        d, parts = parse_day_from_query(["Группа"])
        assert d == _today()
        assert parts == ["Группа"]


class TestInlineQueryHandler:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        q = _query("   ")
        await inline_query_handler(q, FakeManager({}))
        results = q.answer.call_args[0][0]
        assert results[0].id == "help"

    @pytest.mark.asyncio
    async def test_day_only_suggests_popular(self):
        q = _query("завтра")
        await inline_query_handler(q, FakeManager({"О735Б": {}, "О736Б": {}}))
        results = q.answer.call_args[0][0]
        assert all(r.id.startswith("suggest:") for r in results)

    @pytest.mark.asyncio
    async def test_exact_group_match(self):
        q = _query("О735Б")
        await inline_query_handler(q, FakeManager({"О735Б": {}}))
        q.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fuzzy_matches(self, monkeypatch):
        q = _query("735")
        monkeypatch.setattr(ih.process, "extract", lambda *a, **k: [("О735Б", 90, 0)])
        await inline_query_handler(q, FakeManager({"О735Б": {}}))
        results = q.answer.call_args[0][0]
        assert results[0].id.startswith("autocomplete:")

    @pytest.mark.asyncio
    async def test_no_matches(self, monkeypatch):
        q = _query("zzzzz")
        monkeypatch.setattr(ih.process, "extract", lambda *a, **k: [])
        await inline_query_handler(q, FakeManager({"О735Б": {}}))
        results = q.answer.call_args[0][0]
        assert "не найдена" in results[0].title

    @pytest.mark.asyncio
    async def test_normalization_empty_falls_back_to_raw(self, monkeypatch):
        q = _query("!!!")
        monkeypatch.setattr(ih, "normalize_group_name", lambda value: "")
        monkeypatch.setattr(ih.process, "extract", lambda *a, **k: [])
        await inline_query_handler(q, FakeManager({"О735Б": {}}))
        results = q.answer.call_args[0][0]
        assert "!!!" in results[0].title

    @pytest.mark.asyncio
    async def test_analytics_exception_is_swallowed(self, monkeypatch):
        q = _query("О735Б")
        monkeypatch.setattr(
            "core.analytics.track_feature_click",
            AsyncMock(side_effect=Exception("analytics down")),
        )
        await inline_query_handler(q, FakeManager({"О735Б": {}}))
        q.answer.assert_awaited_once()


class TestSendScheduleResult:
    @pytest.mark.asyncio
    async def test_with_lessons(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.9)
        q = _query("x")
        manager = FakeManager({})
        await _send_schedule_result(q, manager, "О735Б", date(2025, 1, 6))
        result = q.answer.call_args[0][0][0]
        assert "Пар: 1" in result.description
        assert "Начало в 9:00." in result.description

    @pytest.mark.asyncio
    async def test_without_lessons(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.9)
        q = _query("x")
        manager = FakeManager({}, {"date": date(2025, 1, 6), "day_name": "День", "lessons": []})
        await _send_schedule_result(q, manager, "О735Б", date(2025, 1, 6))
        result = q.answer.call_args[0][0][0]
        assert "Занятий нет" in result.description

    @pytest.mark.asyncio
    async def test_promo_added(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.1)
        q = _query("x")
        await _send_schedule_result(q, FakeManager({}), "О735Б", date(2025, 1, 6))
        result = q.answer.call_args[0][0][0]
        assert "Новости разработки" in result.input_message_content.message_text

    @pytest.mark.asyncio
    async def test_language_zh(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.9)
        q = _query("x", lang="zh-Hans")
        await _send_schedule_result(q, FakeManager({}), "О735Б", date(2025, 1, 6))
        q.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_language_en(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.9)
        q = _query("x", lang="en-US")
        await _send_schedule_result(q, FakeManager({}), "О735Б", date(2025, 1, 6))
        q.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_language_none_falls_back(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.9)
        q = _query("x", lang=None)
        await _send_schedule_result(q, FakeManager({}), "О735Б", date(2025, 1, 6))
        q.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_language_exception_falls_back(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.9)

        class _NoUserQuery:
            def __init__(self):
                self.answer = AsyncMock()

            @property
            def from_user(self):
                raise RuntimeError("no from_user")

        q = _NoUserQuery()
        await _send_schedule_result(q, FakeManager({}), "О735Б", date(2025, 1, 6))
        q.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_answer_error_is_logged(self, monkeypatch):
        monkeypatch.setattr(ih.random, "random", lambda: 0.9)
        q = _query("x")
        q.answer = AsyncMock(side_effect=Exception("telegram down"))
        await _send_schedule_result(q, FakeManager({}), "О735Б", date(2025, 1, 6))
        q.answer.assert_awaited_once()
