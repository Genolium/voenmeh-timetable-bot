"""
Тесты для менеджера настроек семестров.
"""

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.db.models import SemesterSettings
from core.semester_settings import SemesterSettingsManager


@pytest.fixture
def mock_session_factory():
    """Мокает session_factory для тестов."""
    return MagicMock()


@pytest.fixture
def settings_manager(mock_session_factory):
    """Создает менеджер настроек с моком session_factory."""
    return SemesterSettingsManager(mock_session_factory)


@pytest.mark.asyncio
async def test_get_semester_settings_no_settings(settings_manager, mock_session_factory):
    """Тест получения настроек, когда их нет в базе."""
    # Мокаем пустой результат
    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    result = await settings_manager.get_semester_settings()

    assert result is None
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_semester_settings_with_settings(settings_manager, mock_session_factory):
    """Тест получения настроек, когда они есть в базе."""
    # Мокаем существующие настройки
    mock_settings = MagicMock()
    mock_settings.fall_semester_start = date(2024, 9, 1)
    mock_settings.spring_semester_start = date(2025, 2, 9)

    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_settings
    mock_session.execute.return_value = mock_result

    result = await settings_manager.get_semester_settings()

    assert result == (date(2024, 9, 1), date(2025, 2, 9))
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_update_semester_settings_new(settings_manager, mock_session_factory):
    """Тест создания новых настроек семестров."""
    # Мокаем отсутствие существующих настроек
    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    fall_start = date(2024, 9, 1)
    spring_start = date(2025, 2, 9)
    updated_by = 123456

    result = await settings_manager.update_semester_settings(fall_start, spring_start, updated_by)

    assert result is True
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_update_semester_settings_existing(settings_manager, mock_session_factory):
    """Тест обновления существующих настроек семестров."""
    # Мокаем существующие настройки
    mock_existing_settings = MagicMock()
    mock_existing_settings.id = 1

    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_existing_settings
    mock_session.execute.return_value = mock_result

    fall_start = date(2024, 9, 15)  # Измененная дата
    spring_start = date(2025, 2, 15)  # Измененная дата
    updated_by = 123456

    result = await settings_manager.update_semester_settings(fall_start, spring_start, updated_by)

    assert result is True
    mock_session.execute.assert_called()  # Должен быть вызов update
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_formatted_settings_with_settings(settings_manager, mock_session_factory):
    """Тест получения отформатированных настроек."""
    # Мокаем существующие настройки
    mock_settings = MagicMock()
    mock_settings.fall_semester_start = date(2024, 9, 1)
    mock_settings.spring_semester_start = date(2025, 2, 9)

    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_settings
    mock_session.execute.return_value = mock_result

    result = await settings_manager.get_formatted_settings()

    assert "🍂" in result
    assert "🌸" in result
    assert "01.09.2024" in result
    assert "09.02.2025" in result
    assert "Текущие настройки семестров" in result


@pytest.mark.asyncio
async def test_get_formatted_settings_no_settings(settings_manager, mock_session_factory):
    """Тест получения отформатированных настроек, когда их нет."""
    # Мокаем отсутствие настроек
    mock_session = AsyncMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    result = await settings_manager.get_formatted_settings()

    assert "Настройки семестров не установлены" in result
    assert "1 сентября" in result
    assert "9 февраля" in result
