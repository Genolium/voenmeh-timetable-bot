import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.image_cache_manager import ImageCacheManager


class FakeRedis:
    def __init__(self, data: dict):
        self.data = data
        self.set_calls = []

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, value))
        self.data[key] = value
        return True

    async def delete(self, key):
        self.data.pop(key, None)
        return 1

    async def keys(self, pattern):
        prefix = pattern.split(':*')[0]
        return [k for k in self.data if k.startswith(prefix)]


@pytest.mark.asyncio
async def test_expired_entries_are_removed(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Prepare expired metadata and real file
    key = "EXPIRED"
    img_path = tmp_path / f"{key}.png"
    img_path.write_bytes(b"X")
    expired_meta = {
        "created_at": (datetime.now() - timedelta(hours=2)).isoformat(),
        "expires_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        "size_bytes": 1,
        "file_path": str(img_path),
    }
    await mgr._set_cache_metadata(key, expired_meta)

    # is_cached should clear it and return False
    assert await mgr.is_cached(key) is False
    assert not img_path.exists()

    # get_cached_image should also return None and not crash
    assert await mgr.get_cached_image(key) is None


@pytest.mark.asyncio
async def test_is_cached_missing_file(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    await mgr._set_cache_metadata("K", {"expires_at": "2999-01-01T00:00:00"})
    assert await mgr.is_cached("K") is False


@pytest.mark.asyncio
async def test_get_cache_info(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    await mgr.cache_image("A", b"1")
    info = await mgr.get_cache_info()
    assert "total_files" in info and info["total_files"] >= 1


@pytest.mark.asyncio
async def test_image_cache_manager_store_and_hit(tmp_path, monkeypatch):
    fake_redis = FakeRedis({})
    mgr = ImageCacheManager(fake_redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    key = "G_odd"
    data = b"PNGDATA"

    ok = await mgr.cache_image(key, data, metadata={"group": "G", "week_key": "odd"})
    assert ok is True

    assert await mgr.is_cached(key) is True
    hit = await mgr.get_cached_image(key)
    assert hit == data


@pytest.mark.asyncio
async def test_image_cache_manager_cleanup(tmp_path):
    fake_redis = FakeRedis({})
    mgr = ImageCacheManager(fake_redis, cache_ttl_hours=0)
    mgr.cache_dir = tmp_path

    key = "G_even"
    await mgr.cache_image(key, b"X")
    # истекает сразу из-за cache_ttl_hours=0
    info_before = await mgr.get_cache_info()
    assert info_before["total_files"] >= 1

    res = await mgr.cleanup_expired_cache()
    assert "cleaned_files" in res


@pytest.mark.asyncio
async def test_cache_image_with_metadata(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=2)
    mgr.cache_dir = tmp_path

    metadata = {"group": "TEST", "week": "odd"}
    success = await mgr.cache_image("test_key", b"test_data", metadata)
    
    assert success is True
    assert await mgr.is_cached("test_key") is True
    
    # Проверяем, что метаданные сохранились
    cached_data = await mgr.get_cached_image("test_key")
    assert cached_data == b"test_data"


@pytest.mark.asyncio
async def test_cache_image_failure_handling(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем ошибку записи файла
    with patch('builtins.open', side_effect=OSError("Permission denied")):
        success = await mgr.cache_image("fail_key", b"data")
        assert success is False


@pytest.mark.asyncio
async def test_get_cached_image_redis_error(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем ошибку Redis
    with patch.object(redis, 'get', side_effect=Exception("Redis error")):
        result = await mgr.get_cached_image("error_key")
        assert result is None


@pytest.mark.asyncio
async def test_get_cached_image_file_read_error(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Создаем файл и метаданные
    key = "read_error"
    img_path = tmp_path / f"{key}.png"
    img_path.write_bytes(b"data")
    
    metadata = {
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
        "size_bytes": 4,
        "file_path": str(img_path),
    }
    await mgr._set_cache_metadata(key, metadata)

    # Мокаем ошибку чтения файла
    with patch('builtins.open', side_effect=OSError("File read error")):
        result = await mgr.get_cached_image(key)
        assert result is None


@pytest.mark.asyncio
async def test_is_cached_redis_error(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем ошибку Redis
    with patch.object(redis, 'get', side_effect=Exception("Redis error")):
        result = await mgr.is_cached("error_key")
        assert result is False


@pytest.mark.asyncio
async def test_get_cache_info_redis_error(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем ошибку Redis
    with patch.object(redis, 'get', side_effect=Exception("Redis error")):
        result = await mgr.get_cache_info()
        assert "error" in result


@pytest.mark.asyncio
async def test_cleanup_expired_cache_with_errors(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Создаем несколько записей
    await mgr.cache_image("key1", b"data1")
    await mgr.cache_image("key2", b"data2")

    # Мокаем ошибку при получении метаданных для одной записи
    with patch.object(redis, 'get', side_effect=[b'{"expires_at": "1999-01-01T00:00:00"}', Exception("Redis error")]):
        result = await mgr.cleanup_expired_cache()
        assert "cleaned_files" in result


@pytest.mark.asyncio
async def test_remove_cache_entry_success(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Создаем файл и метаданные
    key = "to_remove"
    img_path = tmp_path / f"{key}.png"
    img_path.write_bytes(b"data")
    
    metadata = {
        "created_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
        "size_bytes": 4,
        "file_path": str(img_path),
    }
    await mgr._set_cache_metadata(key, metadata)

    # Удаляем запись
    success = await mgr._remove_cache_entry(key)
    assert success is True
    assert not img_path.exists()


@pytest.mark.asyncio
async def test_remove_cache_entry_file_not_exists(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Удаляем несуществующую запись
    success = await mgr._remove_cache_entry("nonexistent")
    assert success is True


@pytest.mark.asyncio
async def test_remove_cache_entry_redis_error(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем ошибку Redis
    with patch.object(redis, 'delete', side_effect=Exception("Redis error")):
        success = await mgr._remove_cache_entry("error_key")
        assert success is False


@pytest.mark.asyncio
async def test_set_cache_metadata_success():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=2)

    metadata = {"test": "data"}
    success = await mgr._set_cache_metadata("test_key", metadata)
    assert success is True


@pytest.mark.asyncio
async def test_set_cache_metadata_redis_error(monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    # Мокаем ошибку Redis
    with patch.object(redis, 'set', side_effect=Exception("Redis error")):
        success = await mgr._set_cache_metadata("error_key", {})
        assert success is False


@pytest.mark.asyncio
async def test_get_cache_metadata_success():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    metadata = {"test": "data"}
    await mgr._set_cache_metadata("test_key", metadata)
    
    result = await mgr._get_cache_metadata("test_key")
    assert result == metadata


@pytest.mark.asyncio
async def test_get_cache_metadata_redis_error(monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    # Мокаем ошибку Redis
    with patch.object(redis, 'get', side_effect=Exception("Redis error")):
        result = await mgr._get_cache_metadata("error_key")
        assert result is None


@pytest.mark.asyncio
async def test_get_cache_metadata_invalid_json():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    # Устанавливаем невалидный JSON
    await redis.set("image_cache:metadata:invalid", "invalid json")
    
    result = await mgr._get_cache_metadata("invalid")
    assert result is None


@pytest.mark.asyncio
async def test_is_cache_expired_valid_future():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    metadata = {
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat()
    }
    
    assert mgr._is_cache_expired(metadata) is False


@pytest.mark.asyncio
async def test_is_cache_expired_valid_past():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    metadata = {
        "expires_at": (datetime.now() - timedelta(hours=1)).isoformat()
    }
    
    assert mgr._is_cache_expired(metadata) is True


@pytest.mark.asyncio
async def test_is_cache_expired_missing_expires_at():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    metadata = {}
    assert mgr._is_cache_expired(metadata) is True


@pytest.mark.asyncio
async def test_is_cache_expired_invalid_date_format():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    metadata = {"expires_at": "invalid-date"}
    assert mgr._is_cache_expired(metadata) is True


@pytest.mark.asyncio
async def test_update_cache_stats_success():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    # Обновляем статистику
    await mgr._update_cache_stats("hits", 5)
    await mgr._update_cache_stats("size", 1024)
    
    # Проверяем, что статистика обновилась
    stats_data = await redis.get("image_cache:stats")
    assert stats_data is not None


@pytest.mark.asyncio
async def test_update_cache_stats_redis_error(monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    # Мокаем ошибку Redis
    with patch.object(redis, 'get', side_effect=Exception("Redis error")):
        # Не должно падать
        await mgr._update_cache_stats("hits")


@pytest.mark.asyncio
async def test_update_cache_stats_invalid_json(monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)

    # Устанавливаем невалидный JSON
    await redis.set("image_cache:stats", "invalid json")
    
    # Не должно падать
    await mgr._update_cache_stats("hits")


@pytest.mark.asyncio
async def test_cache_initialization():
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=48)
    
    assert mgr.cache_ttl_hours == 48
    assert mgr.redis == redis
    assert "generated" in str(mgr.cache_dir)


@pytest.mark.asyncio
async def test_get_cache_info_detailed(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Создаем несколько файлов разного размера
    await mgr.cache_image("small", b"1" * 100)
    await mgr.cache_image("large", b"2" * 1000)

    info = await mgr.get_cache_info()
    
    assert info["total_files"] == 2
    assert info["total_size_bytes"] == 1100
    assert info["total_size_mb"] == 0.0  # меньше 1MB
    assert info["ttl_hours"] == 1
    assert "cache_dir" in info


@pytest.mark.asyncio
async def test_cleanup_expired_cache_no_expired_files(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=24)
    mgr.cache_dir = tmp_path

    # Создаем файлы с будущим TTL
    await mgr.cache_image("future1", b"data1")
    await mgr.cache_image("future2", b"data2")

    result = await mgr.cleanup_expired_cache()
    
    assert result["cleaned_files"] == 0
    assert result["cleaned_size_bytes"] == 0
    assert result["cleaned_size_mb"] == 0.0


@pytest.mark.asyncio
async def test_cache_image_without_metadata(tmp_path):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    success = await mgr.cache_image("no_meta", b"data")
    assert success is True
    
    # Проверяем, что базовые метаданные создались
    metadata = await mgr._get_cache_metadata("no_meta")
    assert metadata is not None
    assert "created_at" in metadata
    assert "expires_at" in metadata
    assert metadata["size_bytes"] == 4


@pytest.mark.asyncio
async def test_get_cached_image_metrics_increment(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Создаем валидный кэш
    await mgr.cache_image("metrics_test", b"data")
    
    # Мокаем метрики
    mock_hits = MagicMock()
    monkeypatch.setattr('core.image_cache_manager.IMAGE_CACHE_HITS', mock_hits)
    
    await mgr.get_cached_image("metrics_test")
    
    # Проверяем, что метрика увеличилась
    mock_hits.labels.assert_called_with(cache_type="file")


@pytest.mark.asyncio
async def test_cache_miss_metrics_increment(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем метрики
    mock_misses = MagicMock()
    monkeypatch.setattr('core.image_cache_manager.IMAGE_CACHE_MISSES', mock_misses)
    
    await mgr.get_cached_image("nonexistent")
    
    # Проверяем, что метрика увеличилась
    mock_misses.labels.assert_called_with(cache_type="redis")


@pytest.mark.asyncio
async def test_cache_operations_metrics_increment(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем метрики
    mock_operations = MagicMock()
    monkeypatch.setattr('core.image_cache_manager.IMAGE_CACHE_OPERATIONS', mock_operations)
    
    await mgr.cache_image("metrics_test", b"data")
    
    # Проверяем, что метрика увеличилась
    mock_operations.labels.assert_called_with(operation="store")


@pytest.mark.asyncio
async def test_cache_size_metrics_update(tmp_path, monkeypatch):
    redis = FakeRedis({})
    mgr = ImageCacheManager(redis, cache_ttl_hours=1)
    mgr.cache_dir = tmp_path

    # Мокаем метрики
    mock_size = MagicMock()
    monkeypatch.setattr('core.image_cache_manager.IMAGE_CACHE_SIZE', mock_size)
    
    await mgr.cache_image("size_test", b"test_data")
    
    # Проверяем, что кэш работает корректно
    assert await mgr.is_cached("size_test") is True
