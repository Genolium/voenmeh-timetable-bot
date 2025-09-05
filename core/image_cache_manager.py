import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from redis.asyncio.client import Redis
from PIL import Image
import io

from core.config import MEDIA_PATH, MOSCOW_TZ
from core.metrics import IMAGE_CACHE_HITS, IMAGE_CACHE_MISSES, IMAGE_CACHE_SIZE, IMAGE_CACHE_OPERATIONS

logger = logging.getLogger(__name__)

class ImageCacheManager:
    """
    Унифицированный менеджер кэширования изображений с поддержкой Redis и файловой системы.
    Поддерживает как файловый кэш, так и Redis для метаданных.
    """
    
    def __init__(self, redis_client: Redis, cache_ttl_hours: int = 720):
        self.redis = redis_client
        self.cache_ttl_hours = cache_ttl_hours
        self.cache_dir = MEDIA_PATH / "generated"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Порог ограничения размера файлового кэша (МБ)
        try:
            self.max_cache_mb = int(os.getenv('IMAGE_CACHE_MAX_MB', '500'))
        except Exception:
            self.max_cache_mb = 500
        
        # Проверяем, что Redis-клиент правильный
        if not hasattr(self.redis, 'get') or not hasattr(self.redis, 'set'):
            raise ValueError("Redis-клиент должен иметь методы 'get' и 'set'. Передан неправильный тип клиента.")
        
        # Ключи Redis для метаданных кэша
        self.cache_metadata_key = "image_cache:metadata"
        self.cache_data_prefix = "image_cache:data:"
        self.cache_metadata_prefix = "image_cache:meta:"
        self.redis_counter_key = "image_cache:file_count"
        
        logger.info(f"ImageCacheManager initialized with TTL: {cache_ttl_hours}h, cache_dir: {self.cache_dir}")
    
    async def is_cached(self, cache_key: str) -> bool:
        """
        Проверяет, есть ли изображение в кэше (Redis + файловая система).
        
        Args:
            cache_key: Ключ кэша (например, "GROUP_even")
            
        Returns:
            True если изображение есть в кэше, False иначе
        """
        try:
            # Проверяем Redis кэш
            redis_key = f"{self.cache_data_prefix}{cache_key}"
            redis_cached = await self.redis.exists(redis_key)
            
            # Проверяем файловую систему
            file_path = self.cache_dir / f"{cache_key}.png"
            file_cached = file_path.exists()
            
            # Дополнительная проверка: файл должен быть не пустым
            if file_cached:
                try:
                    file_size = file_path.stat().st_size
                    if file_size == 0:
                        logger.warning(f"⚠️ Cache file is empty: {file_path}")
                        file_cached = False
                except Exception as e:
                    logger.warning(f"⚠️ Error checking file size: {e}")
                    file_cached = False
            
            # Логируем состояние кэша
            logger.debug(f"Cache check for {cache_key}: Redis={redis_cached}, File={file_cached}")
            
            # Изображение считается кэшированным, если есть либо в Redis, либо в файле
            is_cached = redis_cached or file_cached
            
            if is_cached:
                IMAGE_CACHE_HITS.labels(cache_type="unified").inc()
                logger.info(f"Cache HIT for {cache_key}")
            else:
                IMAGE_CACHE_MISSES.labels(cache_type="unified").inc()
                logger.info(f"Cache MISS for {cache_key}")
            
            return is_cached
            
        except Exception as e:
            logger.error(f"Error checking cache for {cache_key}: {e}")
            IMAGE_CACHE_MISSES.labels(cache_type="error").inc()
            return False
    
    async def get_cached_image(self, cache_key: str) -> Optional[bytes]:
        """
        Получает изображение из кэша.
        
        Args:
            cache_key: Ключ кэша
            
        Returns:
            Байты изображения или None если не найдено
        """
        try:
            # Сначала пробуем Redis
            redis_key = f"{self.cache_data_prefix}{cache_key}"
            image_bytes = await self.redis.get(redis_key)
            
            if image_bytes:
                logger.info(f"Retrieved {cache_key} from Redis cache")
                IMAGE_CACHE_OPERATIONS.labels(operation="redis_get").inc()
                return image_bytes
            
            # Если нет в Redis, пробуем файловую систему
            file_path = self.cache_dir / f"{cache_key}.png"
            if file_path.exists():
                with open(file_path, 'rb') as f:
                    image_bytes = f.read()
                logger.info(f"Retrieved {cache_key} from file cache")
                IMAGE_CACHE_OPERATIONS.labels(operation="file_get").inc()
                return image_bytes
            
            logger.warning(f"Image {cache_key} not found in cache")
            return None
            
        except Exception as e:
            logger.error(f"Error retrieving image {cache_key} from cache: {e}")
            return None
    
    async def cache_image(self, cache_key: str, image_bytes: bytes, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Сохраняет изображение в кэш (Redis + файловая система).
        
        Args:
            cache_key: Ключ кэша
            image_bytes: Байты изображения
            metadata: Дополнительные метаданные
            
        Returns:
            True если успешно сохранено, False иначе
        """
        try:
            success_count = 0
            
            # В Redis сохраняем только метаданные (экономим RAM)
            try:
                meta_key = f"{self.cache_metadata_prefix}{cache_key}"
                meta_data = {
                    **(metadata or {}),
                    "cached_at": datetime.now(MOSCOW_TZ).isoformat(),
                    "size_bytes": len(image_bytes),
                    "ttl_hours": self.cache_ttl_hours
                }
                await self.redis.set(meta_key, json.dumps(meta_data), ex=self.cache_ttl_hours * 3600)
                success_count += 1
                IMAGE_CACHE_OPERATIONS.labels(operation="redis_store").inc()
            except Exception as e:
                logger.warning(f"Failed to save metadata for {cache_key} to Redis: {e}")
            
            # Сохраняем в файловую систему
            try:
                file_path = self.cache_dir / f"{cache_key}.png"
                with open(file_path, 'wb') as f:
                    f.write(image_bytes)
                
                success_count += 1
                logger.info(f"Saved {cache_key} to file cache ({len(image_bytes)} bytes)")
                IMAGE_CACHE_OPERATIONS.labels(operation="file_store").inc()
                
            except Exception as e:
                logger.warning(f"Failed to save {cache_key} to file: {e}")
            
            # Обновляем счетчик и метрики, применяем лимиты
            try:
                await self._increment_counter()
            except Exception:
                pass
            await self._update_cache_size_metrics()
            await self._enforce_limits()
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"Error caching image {cache_key}: {e}")
            return False
    
    async def get_cache_info(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        Получает информацию о кэшированном изображении.
        
        Args:
            cache_key: Ключ кэша
            
        Returns:
            Словарь с информацией или None если не найдено
        """
        try:
            # Проверяем Redis метаданные
            meta_key = f"{self.cache_metadata_prefix}{cache_key}"
            meta_json = await self.redis.get(meta_key)
            
            if meta_json:
                metadata = json.loads(meta_json)
                
                # Добавляем информацию о файле
                file_path = self.cache_dir / f"{cache_key}.png"
                if file_path.exists():
                    metadata["file_exists"] = True
                    metadata["file_size"] = file_path.stat().st_size
                    metadata["file_modified"] = datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                else:
                    metadata["file_exists"] = False
                
                return metadata
            
            # Если нет метаданных в Redis, но есть файл
            file_path = self.cache_dir / f"{cache_key}.png"
            if file_path.exists():
                return {
                    "file_exists": True,
                    "file_size": file_path.stat().st_size,
                    "file_modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
                    "cached_at": "unknown",
                    "size_bytes": file_path.stat().st_size
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting cache info for {cache_key}: {e}")
            return None
    
    async def invalidate_cache(self, cache_key: str) -> bool:
        """
        Удаляет изображение из кэша.
        
        Args:
            cache_key: Ключ кэша
            
        Returns:
            True если успешно удалено, False иначе
        """
        try:
            success_count = 0
            
            # Удаляем из Redis
            try:
                redis_key = f"{self.cache_data_prefix}{cache_key}"
                meta_key = f"{self.cache_metadata_prefix}{cache_key}"
                
                await self.redis.delete(redis_key, meta_key)
                success_count += 1
                logger.info(f"Removed {cache_key} from Redis cache")
                IMAGE_CACHE_OPERATIONS.labels(operation="redis_delete").inc()
                
            except Exception as e:
                logger.warning(f"Failed to remove {cache_key} from Redis: {e}")
            
            # Удаляем файл
            try:
                file_path = self.cache_dir / f"{cache_key}.png"
                if file_path.exists():
                    file_path.unlink()
                    success_count += 1
                    logger.info(f"Removed {cache_key} from file cache")
                    IMAGE_CACHE_OPERATIONS.labels(operation="file_delete").inc()
                
            except Exception as e:
                logger.warning(f"Failed to remove {cache_key} from file: {e}")
            
            # Обновляем метрики
            await self._update_cache_size_metrics()
            try:
                await self._decrement_counter()
            except Exception:
                pass
            
            return success_count > 0
            
        except Exception as e:
            logger.error(f"Error invalidating cache for {cache_key}: {e}")
            return False
    
    async def cleanup_expired_cache(self) -> int:
        """
        Очищает устаревшие файлы из кэша.
        
        Returns:
            Количество удаленных файлов
        """
        try:
            removed_count = 0
            cutoff_time = datetime.now(MOSCOW_TZ) - timedelta(hours=self.cache_ttl_hours)
            
            for file_path in self.cache_dir.glob("*.png"):
                try:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if file_mtime < cutoff_time:
                        file_path.unlink()
                        removed_count += 1
                        logger.info(f"Removed expired cache file: {file_path.name}")
                except Exception as e:
                    logger.warning(f"Failed to remove expired file {file_path}: {e}")
            
            if removed_count > 0:
                logger.info(f"Cleanup removed {removed_count} expired cache files")
                IMAGE_CACHE_OPERATIONS.labels(operation="cleanup").inc()
                await self._update_cache_size_metrics()
            
            return removed_count
            
        except Exception as e:
            logger.error(f"Error during cache cleanup: {e}")
            return 0
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Получает статистику кэша.
        
        Returns:
            Словарь со статистикой
        """
        try:
            # Подсчитываем файлы
            file_count = len(list(self.cache_dir.glob("*.png")))
            
            # Подсчитываем размер файлов
            total_size = sum(f.stat().st_size for f in self.cache_dir.glob("*.png") if f.is_file())
            
            # Получаем количество ключей в Redis из счетчика с fallback на scan
            redis_count = 0
            try:
                cnt = await self.redis.get(self.redis_counter_key)
                if cnt is not None:
                    try:
                        redis_count = int(cnt if isinstance(cnt, str) else cnt.decode())
                    except Exception:
                        redis_count = 0
                else:
                    async for _ in self.redis.scan_iter(f"{self.cache_data_prefix}*"):
                        redis_count += 1
            except Exception as e:
                logger.warning(f"Error getting Redis cache count: {e}")
            
            return {
                "file_count": file_count,
                "file_size_mb": round(total_size / (1024 * 1024), 2),
                "redis_count": redis_count,
                "cache_dir": str(self.cache_dir),
                "ttl_hours": self.cache_ttl_hours
            }
            
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {}
    
    async def _update_cache_size_metrics(self):
        """Обновляет метрики размера кэша."""
        try:
            stats = await self.get_cache_stats()
            IMAGE_CACHE_SIZE.labels(cache_type="files").set(stats.get("file_count", 0))
            IMAGE_CACHE_SIZE.labels(cache_type="size_mb").set(stats.get("file_size_mb", 0))
        except Exception as e:
            logger.warning(f"Failed to update cache size metrics: {e}")

    async def _enforce_limits(self):
        """Удаляет самые старые файлы при превышении лимита размера кэша."""
        try:
            max_bytes = self.max_cache_mb * 1024 * 1024
            files = [f for f in self.cache_dir.glob("*.png") if f.is_file()]
            total_size = sum(f.stat().st_size for f in files)
            if total_size <= max_bytes:
                return
            # Сортируем по времени модификации (старые сначала)
            files_sorted = sorted(files, key=lambda p: p.stat().st_mtime)
            for fpath in files_sorted:
                try:
                    size = fpath.stat().st_size
                    fpath.unlink()
                    total_size -= size
                    IMAGE_CACHE_OPERATIONS.labels(operation="cleanup").inc()
                    if total_size <= max_bytes:
                        break
                except Exception as e:
                    logger.warning(f"Failed to remove file during limit enforcement: {e}")
            await self._update_cache_size_metrics()
        except Exception as e:
            logger.warning(f"Error enforcing cache size limits: {e}")

    async def _increment_counter(self):
        try:
            await self.redis.incr(self.redis_counter_key)
        except Exception:
            pass

    async def _decrement_counter(self):
        try:
            await self.redis.decr(self.redis_counter_key)
        except Exception:
            pass
    
    def get_file_path(self, cache_key: str) -> Path:
        """
        Получает путь к файлу в кэше.
        
        Args:
            cache_key: Ключ кэша
            
        Returns:
            Путь к файлу
        """
        return self.cache_dir / f"{cache_key}.png"
    
    async def diagnose_cache(self, cache_key: str) -> Dict[str, Any]:
        """
        Диагностирует состояние кэша для конкретного ключа.
        
        Args:
            cache_key: Ключ кэша
            
        Returns:
            Словарь с диагностической информацией
        """
        try:
            # Проверяем Redis
            redis_key = f"{self.cache_data_prefix}{cache_key}"
            redis_exists = await self.redis.exists(redis_key)
            redis_size = 0
            if redis_exists:
                try:
                    redis_data = await self.redis.get(redis_key)
                    redis_size = len(redis_data) if redis_data else 0
                except Exception as e:
                    logger.warning(f"Error getting Redis data size: {e}")
            
            # Проверяем файл
            file_path = self.cache_dir / f"{cache_key}.png"
            file_exists = file_path.exists()
            file_size = 0
            file_modified = None
            
            if file_exists:
                try:
                    stat = file_path.stat()
                    file_size = stat.st_size
                    file_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()
                except Exception as e:
                    logger.warning(f"Error getting file stats: {e}")
            
            # Проверяем метаданные
            meta_key = f"{self.cache_metadata_prefix}{cache_key}"
            meta_exists = await self.redis.exists(meta_key)
            metadata = None
            if meta_exists:
                try:
                    meta_json = await self.redis.get(meta_key)
                    if meta_json:
                        metadata = json.loads(meta_json)
                except Exception as e:
                    logger.warning(f"Error getting metadata: {e}")
            
            return {
                "cache_key": cache_key,
                "redis": {
                    "exists": redis_exists,
                    "size_bytes": redis_size,
                    "key": redis_key
                },
                "file": {
                    "exists": file_exists,
                    "size_bytes": file_size,
                    "path": str(file_path),
                    "modified": file_modified
                },
                "metadata": {
                    "exists": meta_exists,
                    "data": metadata,
                    "key": meta_key
                },
                "overall_status": "healthy" if (redis_exists or file_exists) else "missing"
            }
            
        except Exception as e:
            logger.error(f"Error diagnosing cache for {cache_key}: {e}")
            return {
                "cache_key": cache_key,
                "error": str(e),
                "overall_status": "error"
            }
