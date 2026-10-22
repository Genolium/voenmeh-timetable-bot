"""Coverage tests for ``core.image_service``.

The cache manager, bot and image generator are fully mocked.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.image_service import ImageService


class AsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def cache_manager():
    manager = MagicMock()
    manager.is_cached = AsyncMock(return_value=False)
    manager.invalidate_cache = AsyncMock(return_value=True)
    manager.cache_image = AsyncMock(return_value=True)
    manager.get_cache_info = AsyncMock(return_value={"file_exists": True})
    manager.cleanup_expired_cache = AsyncMock(return_value=3)
    manager.get_cache_stats = AsyncMock(return_value={"file_count": 1})
    manager.diagnose_cache = AsyncMock(return_value={"overall_status": "healthy"})
    manager.redis = MagicMock()
    manager.redis.lock = MagicMock(return_value=AsyncLock())
    return manager


@pytest.fixture
def bot():
    mock = MagicMock()
    mock.edit_message_media = AsyncMock()
    mock.send_photo = AsyncMock()
    mock.send_message = AsyncMock()
    return mock


@pytest.fixture
def service(cache_manager, bot):
    return ImageService(cache_manager, bot)


def _set_file_path(cache_manager, path: Path):
    cache_manager.get_file_path = MagicMock(return_value=path)


# ---------------------------------------------------------------------------
# get_or_generate_week_image
# ---------------------------------------------------------------------------


async def test_cached_hit_sends_to_user(service, cache_manager, tmp_path):
    image = tmp_path / "cached.png"
    image.write_bytes(b"data")
    _set_file_path(cache_manager, image)
    cache_manager.is_cached.return_value = True
    service._send_image_to_user = AsyncMock(return_value=True)

    success, path = await service.get_or_generate_week_image("grp", "odd", "Нечетная", {}, user_id=5)
    assert success is True
    assert path == str(image)
    service._send_image_to_user.assert_awaited_once()


async def test_cached_hit_without_user(service, cache_manager, tmp_path):
    image = tmp_path / "cached.png"
    image.write_bytes(b"data")
    _set_file_path(cache_manager, image)
    cache_manager.is_cached.return_value = True

    success, path = await service.get_or_generate_week_image("grp", "odd", "Нечетная", {})
    assert success is True
    assert path == str(image)


async def test_cached_hit_file_missing_regenerates(service, cache_manager, tmp_path):
    _set_file_path(cache_manager, tmp_path / "missing.png")
    cache_manager.is_cached.return_value = True
    service._generate_and_cache_image = AsyncMock(return_value=(True, "gen.png"))

    success, path = await service.get_or_generate_week_image("grp", "odd", "Нечетная", {})
    cache_manager.invalidate_cache.assert_awaited_once()
    assert success is True
    assert path == "gen.png"


async def test_cached_hit_send_failure_regenerates(service, cache_manager, tmp_path):
    image = tmp_path / "cached.png"
    image.write_bytes(b"data")
    _set_file_path(cache_manager, image)
    cache_manager.is_cached.return_value = True
    service._send_image_to_user = AsyncMock(return_value=False)
    service._generate_and_cache_image = AsyncMock(return_value=(True, "gen.png"))

    success, path = await service.get_or_generate_week_image("grp", "odd", "Нечетная", {}, user_id=5)
    cache_manager.invalidate_cache.assert_awaited_once()
    assert path == "gen.png"


async def test_miss_generates_and_sends(service, cache_manager):
    cache_manager.is_cached.return_value = False
    service._generate_and_cache_image = AsyncMock(return_value=(True, "gen.png"))
    service._send_image_to_user = AsyncMock(return_value=True)

    success, path = await service.get_or_generate_week_image("grp", "odd", "Нечетная", {}, user_id=5)
    assert success is True
    service._send_image_to_user.assert_awaited_once()


async def test_custom_theme_cache_key(service, cache_manager, tmp_path):
    image = tmp_path / "x.png"
    image.write_bytes(b"data")
    _set_file_path(cache_manager, image)
    cache_manager.is_cached.return_value = True

    await service.get_or_generate_week_image("grp", "odd", "Нечетная", {}, user_theme="dark", lang="en")
    cache_manager.is_cached.assert_awaited_with("GRP_odd_dark_en")


# ---------------------------------------------------------------------------
# _generate_and_cache_image
# ---------------------------------------------------------------------------


async def test_generate_concurrent_worker_wins(service, cache_manager, tmp_path):
    _set_file_path(cache_manager, tmp_path / "other.png")
    cache_manager.is_cached.return_value = True
    success, path = await service._generate_and_cache_image("K", {}, "odd", "G", exec_locally=True)
    assert success is True
    assert path.endswith("other.png")


async def test_generate_queued_success(service, cache_manager, tmp_path):
    _set_file_path(cache_manager, tmp_path / "q.png")
    cache_manager.is_cached.return_value = False
    with patch("bot.tasks.generate_week_image_task") as task:
        success, path = await service._generate_and_cache_image(
            "G_odd_ru", {}, "odd", "G", exec_locally=False, generated_by="mass"
        )
    task.send.assert_called_once()
    assert success is False
    assert path is None


async def test_generate_queued_exception(service, cache_manager, tmp_path):
    _set_file_path(cache_manager, tmp_path / "q.png")
    cache_manager.is_cached.return_value = False
    with patch("bot.tasks.generate_week_image_task") as task:
        task.send.side_effect = Exception("queue down")
        success, path = await service._generate_and_cache_image("G_odd_ru", {}, "odd", "G", exec_locally=False)
    assert success is False
    assert path is None


async def test_generate_local_success(service, cache_manager, tmp_path):
    output = tmp_path / "gen.png"
    _set_file_path(cache_manager, output)
    cache_manager.is_cached.return_value = False

    async def fake_generate(**kwargs):
        Path(kwargs["output_path"]).write_bytes(b"image-bytes")
        return True

    with patch("core.image_service.generate_schedule_image", side_effect=fake_generate):
        success, path = await service._generate_and_cache_image("K", {}, "odd", "G", exec_locally=True)
    assert success is True
    assert path == str(output)
    cache_manager.cache_image.assert_awaited_once()


async def test_generate_local_failure_no_file(service, cache_manager, tmp_path):
    output = tmp_path / "gen.png"
    _set_file_path(cache_manager, output)
    cache_manager.is_cached.return_value = False
    with patch("core.image_service.generate_schedule_image", AsyncMock(return_value=False)):
        success, path = await service._generate_and_cache_image("K", {}, "odd", "G", exec_locally=True)
    assert success is False
    assert path is None


async def test_generate_local_failure_file_exists(service, cache_manager, tmp_path):
    output = tmp_path / "gen.png"
    output.write_bytes(b"tiny")
    _set_file_path(cache_manager, output)
    cache_manager.is_cached.return_value = False
    with patch("core.image_service.generate_schedule_image", AsyncMock(return_value=False)):
        success, path = await service._generate_and_cache_image("K", {}, "odd", "G", exec_locally=True)
    assert success is False


async def test_generate_local_cache_error_still_succeeds(service, cache_manager, tmp_path):
    output = tmp_path / "gen.png"
    _set_file_path(cache_manager, output)
    cache_manager.is_cached.return_value = False

    async def fake_generate(**kwargs):
        Path(kwargs["output_path"]).write_bytes(b"image-bytes")
        return True

    with (
        patch("core.image_service.generate_schedule_image", side_effect=fake_generate),
        patch("builtins.open", side_effect=OSError("cannot read")),
    ):
        success, path = await service._generate_and_cache_image("K", {}, "odd", "G", exec_locally=True)
    assert success is True
    cache_manager.cache_image.assert_not_awaited()


async def test_generate_local_exception(service, cache_manager, tmp_path):
    _set_file_path(cache_manager, tmp_path / "gen.png")
    cache_manager.is_cached.return_value = False
    with patch("core.image_service.generate_schedule_image", AsyncMock(side_effect=Exception("boom"))):
        success, path = await service._generate_and_cache_image("K", {}, "odd", "G", exec_locally=True)
    assert success is False
    assert path is None


# ---------------------------------------------------------------------------
# _send_image_to_user
# ---------------------------------------------------------------------------


async def test_send_file_not_found(service):
    assert await service._send_image_to_user("/nope/x.png", 1, None, None) is False


async def test_send_empty_file(service, tmp_path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert await service._send_image_to_user(str(empty), 1, None, None) is False


async def test_send_safe_image_missing(service, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"data")
    with patch("core.image_service.get_telegram_safe_image_path", return_value=str(tmp_path / "missing.png")):
        assert await service._send_image_to_user(str(image), 1, None, None) is False


async def test_send_edits_placeholder(service, bot, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"data")
    with patch("core.image_service.get_telegram_safe_image_path", return_value=str(image)):
        result = await service._send_image_to_user(str(image), 1, 99, "caption")
    assert result is True
    bot.edit_message_media.assert_awaited_once()


async def test_send_edit_not_modified_falls_back(service, bot, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"data")
    bot.edit_message_media.side_effect = Exception("message is not modified")
    with patch("core.image_service.get_telegram_safe_image_path", return_value=str(image)):
        result = await service._send_image_to_user(str(image), 1, 99, "caption")
    assert result is True
    bot.send_photo.assert_awaited_once()


async def test_send_edit_other_error_falls_back(service, bot, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"data")
    bot.edit_message_media.side_effect = Exception("network timeout")
    with patch("core.image_service.get_telegram_safe_image_path", return_value=str(image)):
        result = await service._send_image_to_user(str(image), 1, 99, "caption")
    assert result is True
    bot.send_photo.assert_awaited_once()


async def test_send_photo_failure_uses_text_fallback(service, bot, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"data")
    bot.send_photo.side_effect = Exception("photo failed")
    with patch("core.image_service.get_telegram_safe_image_path", return_value=str(image)):
        result = await service._send_image_to_user(str(image), 1, None, "caption")
    assert result is False
    bot.send_message.assert_awaited_once()


async def test_send_photo_and_text_fallback_fail(service, bot, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"data")
    bot.send_photo.side_effect = Exception("photo failed")
    bot.send_message.side_effect = Exception("text failed")
    with patch("core.image_service.get_telegram_safe_image_path", return_value=str(image)):
        result = await service._send_image_to_user(str(image), 1, None, "caption")
    assert result is False


async def test_send_outer_exception(service, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"data")
    with patch("core.image_service.get_telegram_safe_image_path", side_effect=Exception("boom")):
        assert await service._send_image_to_user(str(image), 1, None, None) is False


# ---------------------------------------------------------------------------
# delegating helpers
# ---------------------------------------------------------------------------


async def test_delegating_helpers(service, cache_manager):
    assert await service.get_cache_info("k") == {"file_exists": True}
    assert await service.invalidate_cache("k") is True
    assert await service.cleanup_expired_cache() == 3
    assert await service.get_cache_stats() == {"file_count": 1}
    assert await service.diagnose_cache("k") == {"overall_status": "healthy"}
