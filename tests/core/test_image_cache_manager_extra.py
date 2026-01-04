
import pytest
import os
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch
from core.image_cache_manager import ImageCacheManager

class FakeRedis:
    def __init__(self, data=None):
        self.data = data or {}
        
    async def get(self, key):
        return self.data.get(key)
        
    async def set(self, key, value, ex=None):
        self.data[key] = value
        return True

    async def exists(self, key):
        return key in self.data

    async def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        for k in self.data:
            if k.startswith(prefix):
                yield k
                
    async def incr(self, key):
        pass

    async def decr(self, key):
        pass


@pytest.mark.asyncio
async def test_enforce_limits_removal(tmp_path, monkeypatch):
    # Mock max size to very small (e.g. 1MB? no, code uses MB int. 
    # self.max_cache_mb = int(os.getenv("IMAGE_CACHE_MAX_MB", "500"))
    # We set it to 0 MB to force cleanup of everything?
    # Or just patch glob?
    
    redis = FakeRedis()
    monkeypatch.setenv("IMAGE_CACHE_MAX_MB", "1") # 1MB limit
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    # Create 2 files, each 600KB (total 1.2MB > 1MB)
    # File 1 (Old)
    f1 = tmp_path / "old.png"
    f1.write_bytes(b"A" * 600 * 1024)
    # Set mtime old
    os.utime(f1, (1000, 1000))
    
    # File 2 (New)
    f2 = tmp_path / "new.png"
    f2.write_bytes(b"B" * 600 * 1024)
    os.utime(f2, (2000, 2000))

    await mgr._enforce_limits()
    
    # Expect f1 removed (oldest), f2 kept
    assert not f1.exists()
    assert f2.exists()

@pytest.mark.asyncio
async def test_diagnose_cache(tmp_path):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    key = "diag_test"
    
    # 1. Missing everywhere
    d = await mgr.diagnose_cache(key)
    assert d["overall_status"] == "missing"
    assert d["redis"]["exists"] is False
    assert d["file"]["exists"] is False
    
    # 2. Add to file only
    f = tmp_path / f"{key}.png"
    f.write_bytes(b"DATA")
    d2 = await mgr.diagnose_cache(key)
    assert d2["overall_status"] == "healthy"
    assert d2["file"]["exists"] is True
    assert d2["file"]["size_bytes"] == 4
    
    # 3. Add to Redis data
    await redis.set(f"image_cache:data:{key}", b"RDD")
    d3 = await mgr.diagnose_cache(key)
    assert d3["redis"]["exists"] is True
    assert d3["redis"]["size_bytes"] == 3

@pytest.mark.asyncio
async def test_init_bad_redis():
    class BadRedis: pass
    with pytest.raises(ValueError):
        ImageCacheManager(BadRedis())

@pytest.mark.asyncio
async def test_init_env_var_error(monkeypatch):
    monkeypatch.setenv("IMAGE_CACHE_MAX_MB", "not_int")
    mgr = ImageCacheManager(FakeRedis()) 
    assert mgr.max_cache_mb == 500 # Default

@pytest.mark.asyncio
async def test_cache_image_redis_metadata_failure(tmp_path):
    # If redis set fails, file should still write
    redis = FakeRedis()
    
    async def fail_set(*args, **kwargs):
        raise Exception("Redis dead")
        
    redis.set = fail_set # Override
    
    mgr = ImageCacheManager(FakeRedis())
    mgr.redis = redis
    mgr.cache_dir = tmp_path
    
    res = await mgr.cache_image("fail_meta", b"content")
    assert res is True
    assert (tmp_path / "fail_meta.png").exists()

@pytest.mark.asyncio
async def test_get_cache_stats_redis_scan(tmp_path):
    redis = FakeRedis()
    # Mock incr/decr failure or just manual setup
    # If redis_counter_key is None, it scans
    # FakeRedis.get returns None by default
    
    redis.data["image_cache:data:k1"] = b"v1"
    redis.data["image_cache:data:k2"] = b"v2"
    
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    stats = await mgr.get_cache_stats()
    assert stats["redis_count"] == 2

@pytest.mark.asyncio
async def test_enforce_limits_error(monkeypatch):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    
    # Mock cache_dir with a MagicMock that raises error on glob
    mock_dir = MagicMock()
    mock_dir.glob.side_effect = Exception("Glob fail")
    mgr.cache_dir = mock_dir
    
    # Should check logs ideally, but mainly ensure no crash
    await mgr._enforce_limits()

@pytest.mark.asyncio
async def test_is_cached_empty_file(tmp_path):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    # Create empty file
    f = tmp_path / "empty.png"
    f.touch()
    
    # Should be invalid
    assert await mgr.is_cached("empty") is False

@pytest.mark.asyncio
async def test_get_cache_info_file_only(tmp_path):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    f = tmp_path / "file_only.png"
    f.write_bytes(b"content")
    
    info = await mgr.get_cache_info("file_only")
    assert info is not None
    assert info["file_exists"] is True
    assert info["cached_at"] == "unknown"

@pytest.mark.asyncio
async def test_diagnose_cache_exceptions(tmp_path, monkeypatch):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    # Mock redis.exists to return True but get to fail
    async def fail_get(k): raise Exception("Redis fail")
    redis.get = fail_get
    redis.exists = AsyncMock(return_value=True)
    
    f = tmp_path / "diag_err.png"
    f.write_bytes(b"Data")
    
    # Should catch errors and return partial info
    d = await mgr.diagnose_cache("diag_err")
    # Redis key exists checked, get failed -> size 0
    assert d["redis"]["exists"] is True
    assert d["redis"]["size_bytes"] == 0
    
@pytest.mark.asyncio
async def test_cleanup_exceptions(tmp_path, monkeypatch):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    
    # Needs to be a MagicMock to allow patching glob
    mock_dir = MagicMock()
    mgr.cache_dir = mock_dir
    
    # Create a mock file path object that looks like a path
    mock_file = MagicMock()
    mock_file.stat.return_value.st_mtime = 100 # Very old
    mock_file.unlink.side_effect = Exception("Unlink fail")
    mock_file.name = "expire_me.png"
    
    # Glob returns this mock file
    mock_dir.glob.return_value = [mock_file]
    
    # Calculate cutoff time to ensure our file is expired
    # In code: now - ttl (720h)
    # 100 timestamp is definitely older than (now - 720h) unless we are in 1970
    
    removed = await mgr.cleanup_expired_cache()
    # Should catch exception and continue
    assert removed == 0

@pytest.mark.asyncio
async def test_diagnose_cache_full_exceptions(tmp_path):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    # 1. Redis error inside diagnose
    async def fail_exists(k): raise Exception("Redis exists fail")
    redis.exists = fail_exists
    
    d = await mgr.diagnose_cache("err_key")
    assert d["overall_status"] == "error"
    assert "Redis exists fail" in d["error"]
    
    # Reset redis
    redis = FakeRedis()
    mgr.redis = redis
    
    # 2. File stat error
    # We need to mock cache_dir again to control file access
    mock_dir = MagicMock()
    mgr.cache_dir = mock_dir
    
    # Redis ok
    redis.data["image_cache:data:k"] = b"v"
    
    # File exists but stat fails
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.stat.side_effect = Exception("Stat fail")
    mock_dir.__truediv__.return_value = mock_path
    
    d2 = await mgr.diagnose_cache("k")
    # Should partial fail on file but overall healthy due to Redis
    assert d2["overall_status"] == "healthy"
    assert d2["file"]["exists"] is True
    assert d2["file"]["size_bytes"] == 0 # Default on error

@pytest.mark.asyncio
async def test_get_cache_stats_fallback(tmp_path):
    redis = FakeRedis()
    mgr = ImageCacheManager(redis)
    mgr.cache_dir = tmp_path
    
    # Mock redis.get to fail/return None for counter, force scan
    # FakeRedis.get returns None for missing keys.
    # We need scan_iter to yield something.
    redis.data["image_cache:data:1"] = b"1"
    redis.data["image_cache:data:2"] = b"2"
    
    stats = await mgr.get_cache_stats()
    assert stats["redis_count"] == 2

