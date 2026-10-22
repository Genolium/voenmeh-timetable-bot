"""Coverage tests for ``core.image_cache_manager``.

Redis is replaced with an in-memory fake, the file system uses ``tmp_path``.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import image_cache_manager as icm_module
from core.image_cache_manager import ImageCacheManager, invalidate_all_generated_images, invalidate_generated_images_for_groups


class _AwareDatetime(datetime):
    """datetime whose fromtimestamp() returns an aware value.

    Needed to exercise ``cleanup_expired_cache`` which otherwise crashes on
    naive/aware comparison (see test comment).
    """

    @classmethod
    def fromtimestamp(cls, ts, tz=None):
        return datetime.fromtimestamp(ts, tz=timezone.utc)


class FakeRedis:
    """Minimal async Redis double supporting the operations used by the manager."""

    def __init__(self):
        self.store: dict = {}
        self.get = AsyncMock(side_effect=self._get)
        self.set = AsyncMock(side_effect=self._set)
        self.delete = AsyncMock(side_effect=self._delete)
        self.exists = AsyncMock(side_effect=self._exists)
        self.incr = AsyncMock(return_value=1)
        self.decr = AsyncMock(return_value=0)

    async def _get(self, key):
        return self.store.get(key)

    async def _set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def _delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed

    async def _exists(self, key):
        return 1 if key in self.store else 0

    async def scan_iter(self, pattern="*"):
        prefix = pattern.rstrip("*")
        for key in list(self.store):
            if key.startswith(prefix):
                yield key


def make_manager(tmp_path, redis=None, ttl=720, max_mb=500):
    manager = ImageCacheManager(redis or FakeRedis(), cache_ttl_hours=ttl)
    manager.cache_dir = tmp_path
    manager.max_cache_mb = max_mb
    return manager


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_invalid_max_mb_env(monkeypatch):
    monkeypatch.setenv("IMAGE_CACHE_MAX_MB", "not-a-number")
    manager = ImageCacheManager(FakeRedis())
    assert manager.max_cache_mb == 500


def test_init_valid_max_mb_env(monkeypatch):
    monkeypatch.setenv("IMAGE_CACHE_MAX_MB", "123")
    manager = ImageCacheManager(FakeRedis())
    assert manager.max_cache_mb == 123


def test_init_rejects_bad_redis_client():
    with pytest.raises(ValueError):
        ImageCacheManager(object())


# ---------------------------------------------------------------------------
# is_cached
# ---------------------------------------------------------------------------


async def test_is_cached_file_hit(tmp_path):
    manager = make_manager(tmp_path)
    (tmp_path / "KEY.png").write_bytes(b"data")
    assert await manager.is_cached("KEY") is True


async def test_is_cached_expired_file_removed(tmp_path):
    manager = make_manager(tmp_path, ttl=1)
    path = tmp_path / "OLD.png"
    path.write_bytes(b"data")
    old = (datetime.now() - timedelta(hours=5)).timestamp()
    import os

    os.utime(path, (old, old))
    assert await manager.is_cached("OLD") is False
    assert not path.exists()


async def test_is_cached_expired_file_unlink_error(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, ttl=1)
    path = tmp_path / "OLD2.png"
    path.write_bytes(b"data")
    old = (datetime.now() - timedelta(hours=5)).timestamp()
    import os

    os.utime(path, (old, old))

    def boom(self, *args, **kwargs):
        raise OSError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", boom)
    assert await manager.is_cached("OLD2") is False


async def test_is_cached_empty_file(tmp_path):
    manager = make_manager(tmp_path)
    (tmp_path / "EMPTY.png").write_bytes(b"")
    assert await manager.is_cached("EMPTY") is False


async def test_is_cached_file_stat_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.cache_dir = MagicMock()
    file_mock = MagicMock()
    file_mock.exists.return_value = True
    file_mock.stat.side_effect = Exception("stat boom")
    manager.cache_dir.__truediv__.return_value = file_mock
    manager.redis.exists = AsyncMock(return_value=0)
    assert await manager.is_cached("X") is False


async def test_is_cached_redis_hit(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.exists = AsyncMock(return_value=1)
    assert await manager.is_cached("REDIS") is True


async def test_is_cached_redis_miss(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.exists = AsyncMock(return_value=0)
    assert await manager.is_cached("MISS") is False


async def test_is_cached_outer_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.exists = AsyncMock(side_effect=Exception("redis down"))
    assert await manager.is_cached("ERR") is False


# ---------------------------------------------------------------------------
# get_cached_image
# ---------------------------------------------------------------------------


async def test_get_cached_image_from_redis(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.store[f"{manager.cache_data_prefix}K"] = b"redis-bytes"
    assert await manager.get_cached_image("K") == b"redis-bytes"


async def test_get_cached_image_from_file(tmp_path):
    manager = make_manager(tmp_path)
    (tmp_path / "F.png").write_bytes(b"file-bytes")
    assert await manager.get_cached_image("F") == b"file-bytes"


async def test_get_cached_image_missing(tmp_path):
    manager = make_manager(tmp_path)
    assert await manager.get_cached_image("NOPE") is None


async def test_get_cached_image_redis_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.get = AsyncMock(side_effect=Exception("boom"))
    assert await manager.get_cached_image("E") is None


async def test_get_cached_image_file_read_error(tmp_path):
    manager = make_manager(tmp_path)
    (tmp_path / "R.png").write_bytes(b"data")
    with patch("builtins.open", side_effect=OSError("read boom")):
        assert await manager.get_cached_image("R") is None


# ---------------------------------------------------------------------------
# cache_image
# ---------------------------------------------------------------------------


async def test_cache_image_success(tmp_path):
    manager = make_manager(tmp_path)
    assert await manager.cache_image("C", b"bytes", {"group": "G"}) is True
    assert (tmp_path / "C.png").exists()


async def test_cache_image_redis_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.set = AsyncMock(side_effect=Exception("redis boom"))
    assert await manager.cache_image("C2", b"bytes") is True


async def test_cache_image_file_error(tmp_path):
    manager = make_manager(tmp_path)
    with patch("builtins.open", side_effect=OSError("file boom")):
        assert await manager.cache_image("C3", b"bytes") is True


async def test_cache_image_counter_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager._increment_counter = AsyncMock(side_effect=Exception("counter boom"))
    assert await manager.cache_image("C4", b"bytes") is True


async def test_cache_image_outer_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.incr = AsyncMock(return_value=25)
    manager._update_cache_size_metrics = AsyncMock(side_effect=Exception("metrics boom"))
    assert await manager.cache_image("C5", b"bytes") is False


# ---------------------------------------------------------------------------
# get_cache_info
# ---------------------------------------------------------------------------


async def test_get_cache_info_with_meta_and_file(tmp_path):
    manager = make_manager(tmp_path)
    (tmp_path / "I.png").write_bytes(b"data")
    manager.redis.store[f"{manager.cache_metadata_prefix}I"] = b'{"group": "G"}'
    info = await manager.get_cache_info("I")
    assert info["file_exists"] is True
    assert info["group"] == "G"


async def test_get_cache_info_with_meta_no_file(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.store[f"{manager.cache_metadata_prefix}J"] = b'{"group": "G"}'
    info = await manager.get_cache_info("J")
    assert info["file_exists"] is False


async def test_get_cache_info_file_only(tmp_path):
    manager = make_manager(tmp_path)
    (tmp_path / "K.png").write_bytes(b"data")
    info = await manager.get_cache_info("K")
    assert info["file_exists"] is True
    assert info["cached_at"] == "unknown"


async def test_get_cache_info_missing(tmp_path):
    manager = make_manager(tmp_path)
    assert await manager.get_cache_info("none") is None


async def test_get_cache_info_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.get = AsyncMock(side_effect=Exception("boom"))
    assert await manager.get_cache_info("err") is None


# ---------------------------------------------------------------------------
# invalidate_cache
# ---------------------------------------------------------------------------


async def test_invalidate_cache_removes_redis_and_file(tmp_path):
    manager = make_manager(tmp_path)
    path = tmp_path / "DEL.png"
    path.write_bytes(b"data")
    assert await manager.invalidate_cache("DEL") is True
    assert not path.exists()


async def test_invalidate_cache_redis_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.delete = AsyncMock(side_effect=Exception("redis boom"))
    (tmp_path / "D2.png").write_bytes(b"data")
    assert await manager.invalidate_cache("D2") is True


async def test_invalidate_cache_file_unlink_error(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    (tmp_path / "D3.png").write_bytes(b"data")

    def boom(self, *args, **kwargs):
        raise OSError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", boom)
    assert await manager.invalidate_cache("D3") is True


async def test_invalidate_cache_nothing_to_remove(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.delete = AsyncMock(side_effect=Exception("redis boom"))
    assert await manager.invalidate_cache("nothing") is False


async def test_invalidate_cache_outer_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager._update_cache_size_metrics = AsyncMock(side_effect=Exception("metrics boom"))
    assert await manager.invalidate_cache("x") is False


async def test_invalidate_cache_decrement_counter_error(tmp_path):
    manager = make_manager(tmp_path)
    manager._decrement_counter = AsyncMock(side_effect=Exception("decr boom"))
    assert await manager.invalidate_cache("x") is True


# ---------------------------------------------------------------------------
# cleanup_expired_cache
# ---------------------------------------------------------------------------


async def test_cleanup_expired_cache_removes_files(tmp_path, monkeypatch):
    # NOTE: production code compares a naive datetime.fromtimestamp() with an
    # aware datetime.now(MOSCOW_TZ), which always raises TypeError. We patch
    # ``datetime`` so the (currently unreachable) removal branch can be exercised.
    monkeypatch.setattr(icm_module, "datetime", _AwareDatetime)
    manager = make_manager(tmp_path, ttl=1)
    path = tmp_path / "OLD.png"
    path.write_bytes(b"data")
    old = (datetime.now() - timedelta(hours=5)).timestamp()
    import os

    os.utime(path, (old, old))
    removed = await manager.cleanup_expired_cache()
    assert removed == 1
    assert not path.exists()
    manager.redis.delete.assert_called()


async def test_cleanup_expired_cache_keeps_fresh_files(tmp_path):
    manager = make_manager(tmp_path, ttl=24)
    (tmp_path / "NEW.png").write_bytes(b"data")
    assert await manager.cleanup_expired_cache() == 0


async def test_cleanup_expired_cache_redis_error(tmp_path, monkeypatch):
    monkeypatch.setattr(icm_module, "datetime", _AwareDatetime)
    manager = make_manager(tmp_path, ttl=1)
    path = tmp_path / "OLD.png"
    path.write_bytes(b"data")
    old = (datetime.now() - timedelta(hours=5)).timestamp()
    import os

    os.utime(path, (old, old))
    manager.redis.delete = AsyncMock(side_effect=Exception("redis boom"))
    assert await manager.cleanup_expired_cache() == 1


async def test_cleanup_expired_cache_file_error(tmp_path):
    manager = make_manager(tmp_path, ttl=1)
    manager.cache_dir = MagicMock()
    file_mock = MagicMock()
    file_mock.stat.side_effect = Exception("stat boom")
    manager.cache_dir.glob.return_value = [file_mock]
    assert await manager.cleanup_expired_cache() == 0


async def test_cleanup_expired_cache_outer_exception(tmp_path):
    manager = make_manager(tmp_path, ttl=1)
    manager.cache_dir = MagicMock()
    manager.cache_dir.glob.side_effect = Exception("glob boom")
    assert await manager.cleanup_expired_cache() == 0


# ---------------------------------------------------------------------------
# get_cache_stats
# ---------------------------------------------------------------------------


async def test_get_cache_stats_with_counter_bytes(tmp_path):
    manager = make_manager(tmp_path)
    (tmp_path / "S.png").write_bytes(b"x" * 10)
    manager.redis.store[manager.redis_counter_key] = b"5"
    stats = await manager.get_cache_stats()
    assert stats["file_count"] == 1
    assert stats["redis_count"] == 5


async def test_get_cache_stats_with_counter_str(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.store[manager.redis_counter_key] = "3"
    stats = await manager.get_cache_stats()
    assert stats["redis_count"] == 3


async def test_get_cache_stats_bad_counter(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.store[manager.redis_counter_key] = b"abc"
    stats = await manager.get_cache_stats()
    assert stats["redis_count"] == 0


async def test_get_cache_stats_scan_fallback(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.store[f"{manager.cache_data_prefix}A"] = b"1"
    manager.redis.store[f"{manager.cache_data_prefix}B"] = b"2"
    stats = await manager.get_cache_stats()
    assert stats["redis_count"] == 2


async def test_get_cache_stats_redis_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.get = AsyncMock(side_effect=Exception("redis boom"))
    stats = await manager.get_cache_stats()
    assert stats["redis_count"] == 0


async def test_get_cache_stats_outer_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager.cache_dir = MagicMock()
    manager.cache_dir.glob.side_effect = Exception("glob boom")
    assert await manager.get_cache_stats() == {}


# ---------------------------------------------------------------------------
# metrics helpers / counters / limits
# ---------------------------------------------------------------------------


async def test_update_cache_size_metrics_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.get_cache_stats = AsyncMock(side_effect=Exception("boom"))
    await manager._update_cache_size_metrics()  # must not raise


async def test_enforce_limits_under_limit(tmp_path):
    manager = make_manager(tmp_path, max_mb=100)
    (tmp_path / "small.png").write_bytes(b"x")
    await manager._enforce_limits()
    assert (tmp_path / "small.png").exists()


async def test_enforce_limits_removes_oldest(tmp_path):
    manager = make_manager(tmp_path, max_mb=0)
    (tmp_path / "a.png").write_bytes(b"x" * 10)
    (tmp_path / "b.png").write_bytes(b"x" * 10)
    await manager._enforce_limits()
    assert not list(tmp_path.glob("*.png"))


async def test_enforce_limits_unlink_error(tmp_path, monkeypatch):
    manager = make_manager(tmp_path, max_mb=0)
    (tmp_path / "a.png").write_bytes(b"x" * 10)

    def boom(self, *args, **kwargs):
        raise OSError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", boom)
    await manager._enforce_limits()  # must not raise


async def test_enforce_limits_outer_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager.cache_dir = MagicMock()
    manager.cache_dir.glob.side_effect = Exception("glob boom")
    await manager._enforce_limits()  # must not raise


async def test_increment_and_decrement_counter(tmp_path):
    manager = make_manager(tmp_path)
    await manager._increment_counter()
    await manager._decrement_counter()
    manager.redis.incr = AsyncMock(side_effect=Exception("boom"))
    manager.redis.decr = AsyncMock(side_effect=Exception("boom"))
    await manager._increment_counter()
    await manager._decrement_counter()


def test_get_file_path(tmp_path):
    manager = make_manager(tmp_path)
    assert manager.get_file_path("K") == tmp_path / "K.png"


# ---------------------------------------------------------------------------
# diagnose_cache
# ---------------------------------------------------------------------------


async def test_diagnose_cache_healthy(tmp_path):
    manager = make_manager(tmp_path)
    redis_key = f"{manager.cache_data_prefix}K"
    manager.redis.store[redis_key] = b"12345"
    manager.redis.store[f"{manager.cache_metadata_prefix}K"] = b'{"a": 1}'
    (tmp_path / "K.png").write_bytes(b"data")
    result = await manager.diagnose_cache("K")
    assert result["overall_status"] == "healthy"
    assert result["redis"]["size_bytes"] == 5
    assert result["file"]["exists"] is True
    assert result["metadata"]["data"] == {"a": 1}


async def test_diagnose_cache_redis_data_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.exists = AsyncMock(return_value=1)
    manager.redis.get = AsyncMock(side_effect=Exception("boom"))
    result = await manager.diagnose_cache("K")
    assert result["redis"]["size_bytes"] == 0


async def test_diagnose_cache_file_stat_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.cache_dir = MagicMock()
    file_mock = MagicMock()
    file_mock.exists.return_value = True
    file_mock.stat.side_effect = Exception("stat boom")
    manager.cache_dir.__truediv__.return_value = file_mock
    manager.redis.exists = AsyncMock(return_value=0)
    result = await manager.diagnose_cache("K")
    assert result["file"]["size_bytes"] == 0
    assert result["overall_status"] == "healthy"


async def test_diagnose_cache_metadata_error(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.exists = AsyncMock(return_value=1)
    manager.redis.get = AsyncMock(side_effect=Exception("boom"))
    result = await manager.diagnose_cache("K")
    assert result["metadata"]["data"] is None


async def test_diagnose_cache_missing(tmp_path):
    manager = make_manager(tmp_path)
    result = await manager.diagnose_cache("absent")
    assert result["overall_status"] == "missing"


async def test_diagnose_cache_outer_exception(tmp_path):
    manager = make_manager(tmp_path)
    manager.redis.exists = AsyncMock(side_effect=Exception("boom"))
    result = await manager.diagnose_cache("K")
    assert result["overall_status"] == "error"


# ---------------------------------------------------------------------------
# module level invalidate_all_generated_images
# ---------------------------------------------------------------------------


async def test_invalidate_all_generated_images(tmp_path, monkeypatch):
    cache_dir = tmp_path / "generated"
    cache_dir.mkdir()
    (cache_dir / "a.png").write_bytes(b"1")
    (cache_dir / "b.jpg").write_bytes(b"2")
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)

    redis = FakeRedis()
    redis.store["image_cache:data:x"] = b"1"
    removed = await invalidate_all_generated_images(redis)
    assert removed == 2
    assert not list(cache_dir.iterdir())


async def test_invalidate_all_generated_images_unlink_error(tmp_path, monkeypatch):
    cache_dir = tmp_path / "generated"
    cache_dir.mkdir()
    (cache_dir / "a.png").write_bytes(b"1")
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)

    def boom(self, *args, **kwargs):
        raise OSError("cannot unlink")

    monkeypatch.setattr(Path, "unlink", boom)
    assert await invalidate_all_generated_images() == 0


async def test_invalidate_all_generated_images_redis_errors(tmp_path, monkeypatch):
    cache_dir = tmp_path / "generated"
    cache_dir.mkdir()
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)

    class BrokenRedis:
        async def scan_iter(self, pattern):
            yield "image_cache:data:x"

        async def delete(self, key):
            raise Exception("delete boom")

    assert await invalidate_all_generated_images(BrokenRedis()) == 0

    class ScanBrokenRedis:
        def scan_iter(self, pattern):
            raise Exception("scan boom")

    assert await invalidate_all_generated_images(ScanBrokenRedis()) == 0


@pytest.mark.asyncio
async def test_invalidate_generated_images_for_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "G1_odd_ru.png").write_bytes(b"x")
    (generated / "G2_odd_ru.png").write_bytes(b"x")

    class ScanRedis:
        deleted: list[str] = []

        async def scan_iter(self, pattern):
            yield "image_cache:data:G1_odd_ru"

        async def delete(self, key):
            self.deleted.append(key)

    redis = ScanRedis()
    removed = await invalidate_generated_images_for_groups(redis, ["G1"])

    assert removed == 1
    assert not (generated / "G1_odd_ru.png").exists()
    assert (generated / "G2_odd_ru.png").exists()


@pytest.mark.asyncio
async def test_invalidate_generated_images_for_groups_errors_are_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(icm_module, "MEDIA_PATH", tmp_path)
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "G1_odd_ru.png").write_bytes(b"x")
    redis = FakeRedis()
    redis.scan_iter = MagicMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(Path, "unlink", MagicMock(side_effect=OSError("file down")))

    assert await invalidate_generated_images_for_groups(redis, ["G1"]) == 0
