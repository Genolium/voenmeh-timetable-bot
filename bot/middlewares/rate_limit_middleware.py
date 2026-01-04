"""
Rate Limiting Middleware для защиты от спама.

Улучшенная версия с раздельными лимитами по типам запросов
и метриками для мониторинга.
"""
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineQuery, Message, Update
from redis.asyncio.client import Redis

from core.metrics import RATE_LIMIT_HITS

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware для ограничения частоты запросов.
    
    Использует Redis для хранения истории запросов.
    Поддерживает разные лимиты для разных типов событий.
    """
    
    # Лимиты по умолчанию (запросов в секунду)
    DEFAULT_LIMITS: Dict[str, float] = {
        "message": 1.0,         # 1 сообщение в секунду
        "callback_query": 3.0,  # 3 callback в секунду
        "inline_query": 0.5,    # 1 inline за 2 секунды
        "default": 2.0,         # Для прочих типов
    }
    
    def __init__(
        self,
        redis_client: Redis,
        limits: Optional[Dict[str, float]] = None,
        window_seconds: float = 1.0,
    ):
        """
        Args:
            redis_client: Redis клиент для хранения данных
            limits: Кастомные лимиты (событий в секунду)
            window_seconds: Размер окна для подсчёта
        """
        self.redis = redis_client
        self.limits = {**self.DEFAULT_LIMITS, **(limits or {})}
        self.window = window_seconds
        
    def _get_event_type(self, update: Update) -> str:
        """Определяет тип события."""
        if update.message:
            return "message"
        elif update.callback_query:
            return "callback_query"
        elif update.inline_query:
            return "inline_query"
        else:
            return "default"
    
    def _get_user_id(self, update: Update) -> Optional[int]:
        """Извлекает user_id из события."""
        if update.message and update.message.from_user:
            return update.message.from_user.id
        elif update.callback_query and update.callback_query.from_user:
            return update.callback_query.from_user.id
        elif update.inline_query and update.inline_query.from_user:
            return update.inline_query.from_user.id
        return None
    
    async def _is_rate_limited(self, key: str, limit: float) -> bool:
        """Проверяет, превышен ли лимит."""
        now = time.time()
        window_start = now - self.window
        
        try:
            # Получаем историю запросов
            history = await self.redis.lrange(key, 0, -1)
            
            # Фильтруем запросы в текущем окне
            valid_timestamps = []
            for ts in history:
                try:
                    ts_float = float(ts.decode() if isinstance(ts, bytes) else ts)
                    if ts_float > window_start:
                        valid_timestamps.append(ts_float)
                except (ValueError, AttributeError):
                    continue
            
            # Проверяем лимит
            if len(valid_timestamps) >= limit:
                return True
            
            # Добавляем текущий запрос
            await self.redis.rpush(key, str(now))
            await self.redis.expire(key, int(self.window) + 1)
            
            # Очищаем старые записи
            if len(history) > 100:
                await self.redis.ltrim(key, -50, -1)
            
            return False
            
        except Exception as e:
            logger.warning(f"Rate limit check failed: {e}")
            return False  # При ошибках пропускаем
    
    async def _notify_user(self, update: Update) -> None:
        """Уведомляет пользователя о превышении лимита."""
        try:
            if update.callback_query:
                await update.callback_query.answer(
                    "⚠️ Слишком много запросов. Подождите немного.",
                    show_alert=True,
                )
            elif update.message:
                await update.message.answer(
                    "⚠️ Слишком много запросов. Подождите немного.",
                )
        except Exception:
            pass
    
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        """Основная логика middleware."""
        user_id = self._get_user_id(event)
        if not user_id:
            return await handler(event, data)
        
        event_type = self._get_event_type(event)
        limit = self.limits.get(event_type, self.limits["default"])
        key = f"rate:{user_id}:{event_type}"
        
        if await self._is_rate_limited(key, limit):
            # Записываем метрику
            try:
                RATE_LIMIT_HITS.labels(event_type=event_type).inc()
            except Exception:
                pass
            
            logger.debug(f"Rate limited user {user_id} for {event_type}")
            await self._notify_user(event)
            return None
        
        return await handler(event, data)
