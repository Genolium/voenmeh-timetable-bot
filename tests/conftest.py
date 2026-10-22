import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Добавляем корневую директорию проекта в пути поиска модулей
# Это гарантирует, что pytest найдет папки 'core' и 'bot'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set up test environment variables
os.environ.setdefault("BOT_TOKEN", "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test_db")

# Fix asyncio transport warnings on Windows by using selector event loop policy
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass


@pytest.fixture
def mock_dialog_manager():
    """Фикстура для мока DialogManager"""
    mock_manager = MagicMock()

    # Мокаем current_context
    mock_context = MagicMock()
    mock_context.dialog_data = {}
    mock_manager.current_context.return_value = mock_context

    # Мокаем middleware_data
    mock_manager.middleware_data = {}

    # Мокаем методы
    mock_manager.switch_to = AsyncMock()
    mock_manager.start = AsyncMock()

    return mock_manager
