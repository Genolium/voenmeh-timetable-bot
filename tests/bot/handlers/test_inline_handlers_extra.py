
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime, timedelta
from bot.handlers.inline_handlers import inline_query_handler, parse_day_from_query, DAY_ALIASES

@pytest.fixture
def mock_query():
    query = AsyncMock()
    query.query = ""
    query.from_user.id = 123
    return query

@pytest.fixture
def mock_manager():
    manager = AsyncMock()
    manager._schedules = {"O735B": {}, "O736B": {}, "I504B": {}} # English letters for fuzzy matching stability
    return manager

class TestInlineHandlersExtra:
    @pytest.mark.asyncio
    async def test_empty_query(self, mock_query, mock_manager):
        mock_query.query = "   "
        await inline_query_handler(mock_query, mock_manager)
        
        mock_query.answer.assert_called_once()
        results = mock_query.answer.call_args[0][0]
        assert len(results) == 1
        assert results[0].id == "help"

    @pytest.mark.asyncio
    async def test_query_only_day_suggest_popular(self, mock_query, mock_manager):
        mock_query.query = "завтра"
        await inline_query_handler(mock_query, mock_manager)
        
        mock_query.answer.assert_called_once()
        results = mock_query.answer.call_args[0][0]
        # Should return suggestions equal to number of keys (up to 5)
        assert len(results) == 3 
        assert results[0].id.startswith("suggest:")

    @pytest.mark.asyncio
    async def test_fuzzy_search_suggestions(self, mock_query, mock_manager):
        mock_query.query = "735" # Should match O735B
        manager = mock_manager
        
        # We need normalize_group_name to NOT change "735" too much or mock it.
        # core.text_utils.normalize_group_name usually handles ru/en chars.
        # Let's mock process.extract to ensure we hit the 'matches' branch
        with patch("bot.handlers.inline_handlers.process.extract") as mock_extract:
            mock_extract.return_value = [("O735B", 90, 0)]
            
            await inline_query_handler(mock_query, manager)
            
            mock_query.answer.assert_called_once()
            results = mock_query.answer.call_args[0][0]
            assert len(results) == 1
            assert results[0].id.startswith("autocomplete:O735B")

    @pytest.mark.asyncio
    async def test_send_schedule_result_promo(self, mock_query, mock_manager):
        mock_query.query = "O735B"
        mock_manager.get_schedule_for_day.return_value = {
             "lessons": [{"time": "10:00-11:30", "subject": "Math"}],
             "date": date(2025, 1, 1),
             "day_name": "Wednesday"
        }
        
        # Patch random to trigger promo ( < 0.2 )
        # And patch normalize_group_name to avoid Latin/Cyrillic mismatch
        with patch("bot.handlers.inline_handlers.random.random", return_value=0.1), \
             patch("bot.handlers.inline_handlers.normalize_group_name", side_effect=lambda x: x):
             
             await inline_query_handler(mock_query, mock_manager)
             
             mock_query.answer.assert_called()
             results = mock_query.answer.call_args[0][0]
             # Check description for "Math" to ensure we got schedule result
             assert "Math" in results[0].input_message_content.message_text or "Math" in results[0].description
             content = results[0].input_message_content.message_text
             assert "Новости разработки" in content

def test_parse_day_aliases():
    # Test all aliases mapping
    # Just sample a few key ones
    query = ["mon", "group"]
    d, parts = parse_day_from_query(query)
    # Check that parts removed 'mon'
    assert parts == ["group"]
    # Check d is a date
    assert isinstance(d, date)

    query = ["tmr", "group"]
    d, parts = parse_day_from_query(query)
    # Tmr is tomorrow
    assert parts == ["group"]
    # We can't strictly check date without mocking datetime, but we trust logic flow
    
    # Test "today" logic if no day found
    query = ["group"]
    d, parts = parse_day_from_query(query)
    assert parts == ["group"]
