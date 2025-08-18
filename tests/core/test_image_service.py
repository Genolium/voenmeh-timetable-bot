import pytest
from unittest.mock import AsyncMock, MagicMock
from core.image_service import ImageService
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
from pathlib import Path

@pytest.fixture
def mock_cache_manager():
    mock = MagicMock(spec=ImageCacheManager)
    mock.is_cached = AsyncMock(return_value=False)
    mock.get_file_path.return_value = Path("test.png")
    mock.cache_image = AsyncMock(return_value=True)
    return mock

@pytest.fixture
def mock_bot():
    return MagicMock()

@pytest.fixture
def image_service(mock_cache_manager, mock_bot):
    return ImageService(mock_cache_manager, mock_bot)

@pytest.mark.asyncio
async def test_get_schedule_image_cached(image_service, mock_cache_manager):
    mock_cache_manager.is_cached.return_value = True
    mock_cache_manager.get_file_path.return_value = Path("cached.png")
    success, file_path = await image_service.get_or_generate_week_image(
        "TEST_GROUP", "odd", "Нечетная", {}
    )
    assert success
    assert file_path == "cached.png"
    mock_cache_manager.is_cached.assert_called_with("TEST_GROUP_odd")

@pytest.mark.asyncio
async def test_get_schedule_image_generate(image_service, mock_cache_manager, mocker):
    mock_cache_manager.is_cached.return_value = False
    mocker.patch.object(image_service, '_generate_and_cache_image', AsyncMock(return_value=(True, "generated.png")))
    success, file_path = await image_service.get_or_generate_week_image(
        "TEST_GROUP", "odd", "Нечетная", {}
    )
    assert success
    assert file_path == "generated.png"
    image_service._generate_and_cache_image.assert_called_once()

@pytest.mark.asyncio
async def test_get_schedule_image_generation_fail(image_service, mock_cache_manager, mocker):
    mock_cache_manager.is_cached.return_value = False
    mocker.patch.object(image_service, '_generate_and_cache_image', AsyncMock(return_value=(False, None)))
    success, file_path = await image_service.get_or_generate_week_image(
        "TEST_GROUP", "odd", "Нечетная", {}
    )
    assert not success
    assert file_path is None

@pytest.mark.asyncio
async def test_generate_and_cache_image(image_service, mocker, tmp_path):
    mocker.patch('core.image_service.generate_schedule_image', AsyncMock(return_value=True))
    mocker.patch('pathlib.Path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open())
    success, file_path = await image_service._generate_and_cache_image("test_key", {}, "Test Week", "TEST")
    assert success
    assert file_path.endswith(".png")

@pytest.mark.asyncio
async def test_send_image_to_user_success(image_service, mocker, tmp_path):
    image_file = tmp_path / "test.png"
    image_file.write_bytes(b"dummy")
    mocker.patch('pathlib.Path.exists', return_value=True)
    mocker.patch('bot.utils.image_compression.get_telegram_safe_image_path', return_value=str(image_file))
    mocker.patch.object(image_service.bot, 'edit_message_media', AsyncMock(side_effect=Exception("test exception to trigger send")))
    mocker.patch.object(image_service.bot, 'send_photo', AsyncMock())
    success = await image_service._send_image_to_user(str(image_file), 123, 456, "caption")
    assert success

@pytest.mark.asyncio
async def test_send_image_to_user_file_not_exists(image_service):
    success = await image_service._send_image_to_user("nonexistent.png", 123, None, None)
    assert not success

# Add more tests for other methods in ImageService
