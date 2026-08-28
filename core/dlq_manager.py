import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from redis.asyncio import Redis
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db.models import FailedMessage

logger = logging.getLogger(__name__)

REDIS_DLQ_KEY = "dlq:failed_messages"


class DLQManager:
    """Менеджер Dead-Letter Queue (DLQ) для сбора и анализа сбойных доставок сообщений."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        redis_client: Optional[Redis] = None,
    ):
        self.session_factory = session_factory
        self.redis = redis_client

    async def record_failed_message(
        self,
        user_id: int,
        payload: str | Dict[str, Any],
        error_message: str,
        message_type: str = "text",
        retry_count: int = 0,
    ) -> Optional[int]:
        """
        Регистрирует сообщение, которое не удалось доставить после исчерпания попыток.
        """
        payload_str = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        msg_id: Optional[int] = None

        # 1. Запись в базу данных
        if self.session_factory:
            try:
                async with self.session_factory() as session:
                    entry = FailedMessage(
                        user_id=user_id,
                        message_type=message_type,
                        payload=payload_str,
                        error_message=str(error_message),
                        retry_count=retry_count,
                        status="failed",
                    )
                    session.add(entry)
                    await session.commit()
                    msg_id = entry.id
                    logger.warning(f"⚠️ [DLQ] Сообщение для user {user_id} сохранено в DB (ID={msg_id}): {error_message}")
            except Exception as e:
                logger.error(f"❌ [DLQ] Ошибка записи в базу данных: {e}")

        # 2. Быстрая запись в Redis список
        if self.redis:
            try:
                dlq_item = {
                    "id": msg_id,
                    "user_id": user_id,
                    "type": message_type,
                    "error": error_message,
                    "retry_count": retry_count,
                }
                await self.redis.lpush(REDIS_DLQ_KEY, json.dumps(dlq_item, ensure_ascii=False))
                await self.redis.ltrim(REDIS_DLQ_KEY, 0, 999)  # Хранить последние 1000 записей
            except Exception as e:
                logger.error(f"❌ [DLQ] Ошибка записи в Redis: {e}")

        return msg_id

    async def get_failed_messages(
        self,
        status: str = "failed",
        limit: int = 50,
        user_id: Optional[int] = None,
    ) -> Sequence[FailedMessage]:
        """Получает список сбойных сообщений из базы данных."""
        if not self.session_factory:
            return []

        async with self.session_factory() as session:
            stmt = select(FailedMessage).where(FailedMessage.status == status)
            if user_id:
                stmt = stmt.where(FailedMessage.user_id == user_id)
            stmt = stmt.order_by(desc(FailedMessage.created_at)).limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()

    async def mark_status(self, message_id: int, new_status: str) -> bool:
        """Обновляет статус сообщения (resolved, discarded)."""
        if not self.session_factory:
            return False

        try:
            async with self.session_factory() as session:
                stmt = (
                    update(FailedMessage)
                    .where(FailedMessage.id == message_id)
                    .values(status=new_status)
                )
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"❌ [DLQ] Ошибка обновления статуса сообщения {message_id}: {e}")
            return False

    async def get_stats(self) -> Dict[str, int]:
        """Возвращает статистику по состояниям DLQ."""
        stats = {"failed": 0, "resolved": 0, "discarded": 0, "total": 0}
        if not self.session_factory:
            return stats

        try:
            async with self.session_factory() as session:
                stmt = select(FailedMessage.status, func.count(FailedMessage.id)).group_by(FailedMessage.status)
                result = await session.execute(stmt)
                for status, count in result.all():
                    if status in stats:
                        stats[status] = count
                stats["total"] = sum(v for k, v in stats.items() if k != "total")
        except Exception as e:
            logger.error(f"❌ [DLQ] Ошибка получения статистики: {e}")

        return stats
