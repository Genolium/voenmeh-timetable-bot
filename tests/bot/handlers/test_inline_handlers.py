import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import date, timedelta, timezone, datetime as dt
from aiogram.types import InlineQuery, User
from bot.handlers.inline_handlers import parse_day_from_query, inline_query_handler

@pytest.mark.parametrize("query, expected_day_offset, remaining_parts", [
    (["сегодня", "О735Б"], 0, ["О735Б"]),
    (["завтра", "А123"], 1, ["А123"]),
    (["пн", "Группа"], "next_monday", ["Группа"]),
])
def test_parse_day_from_query(query, expected_day_offset, remaining_parts):
    # Используем фиксированную дату (четверг), чтобы тесты были предсказуемыми
    today = date(2023, 10, 26) 
    with pytest.MonkeyPatch.context() as m:
        # Мокируем datetime.now(), чтобы она возвращала нашу фиксированную дату
        mock_now = dt.combine(today, dt.min.time(), tzinfo=timezone.utc)
        m.setattr("bot.handlers.inline_handlers.datetime", MagicMock(now=lambda tz: mock_now))
        
        if expected_day_offset == "next_monday":
            expected_date = date(2023, 10, 30)
        else:
            expected_date = today + timedelta(days=expected_day_offset)

        result_date, result_parts = parse_day_from_query(query)
        assert result_date == expected_date
        assert result_parts == remaining_parts

@pytest.mark.asyncio
class TestInlineHandler:
    
    @pytest.fixture
    def mock_query(self):
        query = AsyncMock(spec=InlineQuery)
        query.from_user = MagicMock(spec=User)
        query.answer = AsyncMock()
        return query

    @pytest.fixture
    def mock_manager(self):
        manager = MagicMock()
        manager._schedules = {"О735Б": {}}
        manager.get_schedule_for_day.return_value = {
            "date": date.today(),
            "day_name": "Тестовый день",
            "lessons": [{"time": "9:00-10:30", "subject": "Тестовый предмет"}]
        }
        return manager

    async def test_inline_query_success(self, mock_query, mock_manager, mocker):
        mocker.patch('bot.handlers.inline_handlers.format_schedule_text', return_value="Formatted Text")
        mock_query.query = "О735Б сегодня"
        await inline_query_handler(mock_query, mock_manager)
        
        mock_query.answer.assert_called_once()
        result_article = mock_query.answer.call_args[0][0][0]
        assert "Расписание для О735Б" in result_article.title

    async def test_inline_query_group_not_found(self, mock_query, mock_manager):
        mock_query.query = "XXXXX"
        await inline_query_handler(mock_query, mock_manager)
        
        result_article = mock_query.answer.call_args[0][0][0]
        assert "Группа 'XXXXX' не найдена" in result_article.title

    async def test_inline_query_no_lessons(self, mock_query, mock_manager, mocker):
        mocker.patch('bot.handlers.inline_handlers.format_schedule_text', return_value="Formatted Text No Lessons")
        mock_query.query = "О735Б завтра"
        mock_manager.get_schedule_for_day.return_value = {"date": date.today(), "day_name": "Завтра", "lessons": []}
        
        await inline_query_handler(mock_query, mock_manager)
        
        result_article = mock_query.answer.call_args[0][0][0]
        assert "Занятий нет, можно отдыхать!" in result_article.description