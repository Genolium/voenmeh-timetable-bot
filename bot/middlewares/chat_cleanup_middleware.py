"""
Middleware для автоматической очистки старых сообщений в чате
"""
import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery

logger = logging.getLogger(__name__)

class ChatCleanupMiddleware(BaseMiddleware):
    def __init__(self, keep_messages: int = 5):
        self.keep_messages = keep_messages
        
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        # Получаем user_id из события
        user_id = None
        message = None
        
        if event.message:
            user_id = event.message.from_user.id if event.message.from_user else None
            message = event.message
        elif event.callback_query and event.callback_query.from_user:
            user_id = event.callback_query.from_user.id
            message = event.callback_query.message
            
        # Выполняем основной обработчик
        result = await handler(event, data)
        
        # После обработки пытаемся очистить старые сообщения
        if user_id and message:
            try:
                await self._auto_cleanup_messages(user_id, message, data)
            except Exception as e:
                logger.debug(f"Auto cleanup failed for user {user_id}: {e}")
                
        return result
    
    async def _auto_cleanup_messages(self, user_id: int, message: Message, data: Dict[str, Any]):
        """Автоматически удаляет старые сообщения пользователя."""
        try:
            # Получаем bot и redis из middleware data
            bot = data.get("bot")
            redis = None
            
            # Пытаемся получить redis из разных источников
            if 'manager' in data and hasattr(data['manager'], 'redis'):
                redis = data['manager'].redis
            elif 'redis_client' in data:
                redis = data['redis_client']
                
            if not bot or not redis:
                return
                
            # Ключ для хранения ID сообщений пользователя
            msg_key = f"chat_cleanup:{user_id}"
            
            # Добавляем текущее сообщение в список (если это не callback)
            if hasattr(message, 'message_id'):
                await redis.rpush(msg_key, message.message_id)
                await redis.expire(msg_key, 86400)  # TTL 24 часа
            
            # Получаем все сообщения пользователя
            message_ids = await redis.lrange(msg_key, 0, -1)
            if not message_ids:
                return
                
            # Если сообщений больше лимита, удаляем старые
            if len(message_ids) > self.keep_messages:
                to_delete = message_ids[:-self.keep_messages]
                
                deleted_count = 0
                for msg_id in to_delete:
                    try:
                        msg_id_int = int(msg_id.decode() if isinstance(msg_id, bytes) else msg_id)
                        await bot.delete_message(chat_id=user_id, message_id=msg_id_int)
                        await redis.lrem(msg_key, 1, msg_id)
                        deleted_count += 1
                    except Exception:
                        # Сообщение не может быть удалено (возможно уже удалено)
                        await redis.lrem(msg_key, 1, msg_id)
                        continue
                
                if deleted_count > 0:
                    logger.debug(f"Auto-deleted {deleted_count} old messages for user {user_id}")
                    
        except Exception as e:
            logger.debug(f"Chat cleanup error: {e}")
