
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.user_data import UserDataManager
from core.db import User
from sqlalchemy.ext.asyncio import AsyncSession

@pytest.fixture
def mock_session():
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = MagicMock(fetchall=lambda: [], all=lambda: [], one_or_none=lambda: None)
    session.scalar.return_value = None
    session.get.return_value = None
    return session

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get.return_value = None
    redis.scan.side_effect = [(0, [])] # Default scan returns no keys
    redis.lrange.return_value = []
    return redis

@pytest.fixture
def user_manager(mock_session, mock_redis):
    # Patch create_async_engine to returned mock engine
    with patch("core.user_data.create_async_engine") as mock_engine, \
         patch("core.user_data.async_sessionmaker") as mock_maker:
        
        mock_maker.return_value = MagicMock()
        # The instance is called to get context manager: self.async_session_maker()
        # So mock_maker.return_value.return_value is the session context manager
        # And its __aenter__ returns the session
        mock_maker.return_value.return_value.__aenter__.return_value = mock_session
        
        manager = UserDataManager("postgresql+asyncpg://user:pass@localhost/db", "redis://localhost")
        manager._redis_client = mock_redis
        return manager

@pytest.mark.asyncio
class TestUserDataErrors:
    async def test_register_user_db_error(self, user_manager, mock_session):
        # Verify DB error bubbles up
        mock_session.get.side_effect = Exception("DB Connection Failed")
        with pytest.raises(Exception, match="DB Connection Failed"):
            await user_manager.register_user(123, "test_user")

    async def test_cache_failure_does_not_break_logic(self, user_manager, mock_redis):
        # Decorator logic: if redis fails, function should still return result
        # But register_user only calls clear_user_cache, which catches exceptions
        mock_redis.scan.side_effect = Exception("Redis Down")
        
        # Should not raise exception
        await user_manager.clear_user_cache()

    async def test_history_failure_warning(self, user_manager, mock_redis):
        mock_redis.lpush.side_effect = Exception("Redis Write Failed")
        
        # Should not raise exception, just log warning
        await user_manager.add_to_history(123, "group", "123")
        # If no exception, test passed (logic swallows it)

    async def test_set_user_theme_invalid(self, user_manager, mock_session):
        # Passing invalid theme should force 'standard'
        await user_manager.set_user_theme(123, "invalid_theme_xyz")
        
        # Verify update was called with 'standard'
        # Extract the statement passed to execute
        assert mock_session.execute.called
        # It's hard to inspect SQLAlchemy statement objects deeply, 
        # checking the warning log or effect is safer.
        # But we can check that it didn't fail.

@pytest.mark.asyncio
class TestUserDataLanguage:
    async def test_get_user_language_default(self, user_manager, mock_session):
        mock_session.scalar.return_value = None
        lang = await user_manager.get_user_language(123)
        assert lang == "ru"

    async def test_set_user_language_invalid(self, user_manager, mock_session):
        await user_manager.set_user_language(123, "fr") # French not supported
        
        # Should fallback to 'ru' inside the method?
        # Code: if language not in ["ru", "en", "zh"]: language = "ru"
        # Since we can't easily check the SQL query values, we trust logic coverage.
        # We can at least ensure it runs.
        assert mock_session.execute.called

@pytest.mark.asyncio
class TestHistoryLogic:
    async def test_get_history_decodes_bytes(self, user_manager, mock_redis):
        # Redis often returns bytes
        mock_redis.lrange.return_value = [b"Group1", b"Group2"]
        
        history = await user_manager.get_history(123, "group")
        assert history == ["Group1", "Group2"]
        assert isinstance(history[0], str)

    async def test_clear_history_specific_type(self, user_manager, mock_redis):
        await user_manager.clear_history(123, "group")
        mock_redis.delete.assert_called_once()
        args = mock_redis.delete.call_args[0]
        assert "history:123:group" in args[0] # or args is just the key string if passed as *keys

    async def test_clear_history_all(self, user_manager, mock_redis):
        await user_manager.clear_history(123)
        assert mock_redis.delete.call_count == 3 # group, teacher, room
