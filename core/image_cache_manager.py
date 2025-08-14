import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
from redis.asyncio.client import Redis
from PIL import Image
import io

from core.config import MEDIA_PATH
from core.metrics import IMAGE_CACHE_HITS, IMAGE_CACHE_MISSES, IMAGE_CACHE_SIZE, IMAGE_CACHE_OPERATIONS

class ImageCacheManager:
    """
    Менеджер кэширования изображений с TTL и Redis.
    Поддерживает как файловый кэш, так и Redis для метаданных.
    """
    
    def __init__(self, redis_client: Redis, cache_ttl_hours: int = 720):
        self.redis = redis_client
        self.cache_ttl_hours = cache_ttl_hours
        self.cache_dir = MEDIA_PATH / "generated"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Ключи Redis для метаданных кэша
        self.cache_metadata_key = "image_cache:metadata"
        self.cache_stats_key = "image_cache:stats"
        # Инициализация статистики выполняется лениво в методах, без await в __init__
    
    async def get_cached_image(self, cache_key: str) -> Optional[bytes]:
        """
        Получает изображение из кэша.
        
        Args:
            cache_key: Уникальный ключ для изображения
            
        Returns:
            Байты изображения или None, если не найдено
        """
        try:
            metadata = await self._get_cache_metadata(cache_key)
            if not metadata:
                IMAGE_CACHE_MISSES.labels(cache_type="redis").inc()
                return None
            
            if self._is_cache_expired(metadata):
                await self._remove_cache_entry(cache_key)
                IMAGE_CACHE_MISSES.labels(cache_type="expired").inc()
                return None
            
            file_path = self.cache_dir / f"{cache_key}.png"
            if not file_path.exists():
                await self._remove_cache_entry(cache_key)
                IMAGE_CACHE_MISSES.labels(cache_type="file_missing").inc()
                return None
            
            with open(file_path, 'rb') as f:
                image_data = f.read()
            
            await self._update_cache_stats("hits")
            IMAGE_CACHE_HITS.labels(cache_type="file").inc()
            
            return image_data
            
        except Exception as e:
            logging.error(f"Ошибка при получении изображения из кэша {cache_key}: {e}")
            IMAGE_CACHE_MISSES.labels(cache_type="error").inc()
            return None
    
    async def cache_image(self, cache_key: str, image_data: bytes, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Сохраняет изображение в кэш.
        """
        try:
            file_path = self.cache_dir / f"{cache_key}.png"
            with open(file_path, 'wb') as f:
                f.write(image_data)
            
            cache_metadata = {
                "created_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(hours=self.cache_ttl_hours)).isoformat(),
                "size_bytes": len(image_data),
                "file_path": str(file_path),
                **(metadata or {})
            }
            
            await self._set_cache_metadata(cache_key, cache_metadata)
            await self._update_cache_stats("size", len(image_data))
            IMAGE_CACHE_OPERATIONS.labels(operation="store").inc()
            
            return True
            
        except Exception as e:
            logging.error(f"Ошибка при сохранении изображения в кэш {cache_key}: {e}")
            return False
    
    async def is_cached(self, cache_key: str) -> bool:
        try:
            metadata = await self._get_cache_metadata(cache_key)
            if not metadata:
                return False
            if self._is_cache_expired(metadata):
                await self._remove_cache_entry(cache_key)
                return False
            file_path = self.cache_dir / f"{cache_key}.png"
            return file_path.exists()
        except Exception as e:
            logging.error(f"Ошибка при проверке кэша {cache_key}: {e}")
            return False
    
    async def get_cache_info(self) -> Dict[str, Any]:
        try:
            stats_data = await self.redis.get(self.cache_stats_key)
            stats = json.loads(stats_data) if stats_data else {}
            metadata_keys = await self.redis.keys(f"{self.cache_metadata_key}:*")
            total_files = len(metadata_keys)
            total_size_bytes = 0
            for key in metadata_keys:
                try:
                    metadata_data = await self.redis.get(key)
                    if metadata_data:
                        metadata = json.loads(metadata_data)
                        total_size_bytes += metadata.get("size_bytes", 0)
                except Exception:
                    continue
            stats.update({
                "total_files": total_files,
                "total_size_bytes": total_size_bytes,
                "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
                "cache_dir": str(self.cache_dir),
                "ttl_hours": self.cache_ttl_hours
            })
            IMAGE_CACHE_SIZE.labels(cache_type="files").set(total_files)
            IMAGE_CACHE_SIZE.labels(cache_type="size_mb").set(stats.get("total_size_mb", 0))
            return stats
        except Exception as e:
            logging.error(f"Ошибка при получении информации о кэше: {e}")
            return {"error": str(e)}
    
    async def cleanup_expired_cache(self) -> Dict[str, Any]:
        try:
            cleaned_files = 0
            cleaned_size_bytes = 0
            metadata_keys = await self.redis.keys(f"{self.cache_metadata_key}:*")
            for key in metadata_keys:
                try:
                    metadata_data = await self.redis.get(key)
                    if metadata_data:
                        metadata = json.loads(metadata_data)
                        if self._is_cache_expired(metadata):
                            cache_key = key.split(":")[-1]
                            size = metadata.get("size_bytes", 0)
                            if await self._remove_cache_entry(cache_key):
                                cleaned_files += 1
                                cleaned_size_bytes += size
                except Exception as e:
                    logging.warning(f"Ошибка при очистке записи кэша {key}: {e}")
                    continue
            if cleaned_files > 0:
                await self._update_cache_stats("cleanup", cleaned_size_bytes)
                logging.info(f"Очищено {cleaned_files} файлов кэша, освобождено {cleaned_size_bytes} байт")
            return {
                "cleaned_files": cleaned_files,
                "cleaned_size_bytes": cleaned_size_bytes,
                "cleaned_size_mb": round(cleaned_size_bytes / (1024 * 1024), 2)
            }
        except Exception as e:
            logging.error(f"Ошибка при очистке кэша: {e}")
            return {"error": str(e)}
    
    async def _get_cache_metadata(self, cache_key: str) -> Optional[Dict[str, Any]]:
        try:
            key = f"{self.cache_metadata_key}:{cache_key}"
            data = await self.redis.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logging.error(f"Ошибка при получении метаданных кэша {cache_key}: {e}")
            return None
    
    async def _set_cache_metadata(self, cache_key: str, metadata: Dict[str, Any]) -> bool:
        try:
            key = f"{self.cache_metadata_key}:{cache_key}"
            ttl_seconds = self.cache_ttl_hours * 3600 + 3600
            await self.redis.set(key, json.dumps(metadata), ex=ttl_seconds)
            return True
        except Exception as e:
            logging.error(f"Ошибка при сохранении метаданных кэша {cache_key}: {e}")
            return False
    
    async def _remove_cache_entry(self, cache_key: str) -> bool:
        try:
            file_path = self.cache_dir / f"{cache_key}.png"
            if file_path.exists():
                file_path.unlink()
            key = f"{self.cache_metadata_key}:{cache_key}"
            await self.redis.delete(key)
            IMAGE_CACHE_OPERATIONS.labels(operation="delete").inc()
            return True
        except Exception as e:
            logging.error(f"Ошибка при удалении записи кэша {cache_key}: {e}")
            return False
    
    def _is_cache_expired(self, metadata: Dict[str, Any]) -> bool:
        try:
            expires_at_str = metadata.get("expires_at")
            if not expires_at_str:
                return True
            expires_at = datetime.fromisoformat(expires_at_str)
            return datetime.now() > expires_at
        except Exception:
            return True
    
    async def _update_cache_stats(self, operation: str, value: int = 1):
        try:
            stats_data = await self.redis.get(self.cache_stats_key)
            stats = json.loads(stats_data) if stats_data else {
                "total_hits": 0,
                "total_misses": 0,
                "total_size_bytes": 0,
                "total_files": 0,
                "last_cleanup": None,
            }
            if operation == "hits":
                stats["total_hits"] += value
            elif operation == "misses":
                stats["total_misses"] += value
            elif operation == "size":
                stats["total_size_bytes"] += value
            elif operation == "cleanup":
                stats["total_size_bytes"] = max(0, stats["total_size_bytes"] - value)
            stats["last_updated"] = datetime.now().isoformat()
            await self.redis.set(self.cache_stats_key, json.dumps(stats), ex=86400)
        except Exception as e:
            logging.error(f"Ошибка при обновлении статистики кэша: {e}")
