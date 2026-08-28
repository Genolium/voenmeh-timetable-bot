import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db.models import ScheduleChangeLog
from core.schedule_diff import LessonChange

logger = logging.getLogger(__name__)


class AuditManager:
    """Менеджер для сохранения и анализа истории изменений в расписании."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def record_changes(
        self,
        target_name: str,
        schedule_date: date,
        changes: List[LessonChange],
        target_type: str = "group",
    ) -> int:
        """
        Сохраняет обнаруженные изменения в расписании в базу данных.
        
        Returns:
            int: Количество сохраненных записей
        """
        if not changes:
            return 0

        saved_count = 0
        try:
            async with self.session_factory() as session:
                for ch in changes:
                    log_entry = ScheduleChangeLog(
                        target_name=target_name,
                        target_type=target_type,
                        schedule_date=schedule_date,
                        change_type=ch.change_type.value if hasattr(ch.change_type, "value") else str(ch.change_type),
                        subject=ch.subject or "Неизвестный предмет",
                        lesson_time=ch.time,
                        old_value=ch.old_value,
                        new_value=ch.new_value,
                    )
                    session.add(log_entry)
                    saved_count += 1
                await session.commit()
                logger.info(f"💾 Записано {saved_count} изменений в аудит-лог для {target_name} на {schedule_date}")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения изменений в аудит-лог для {target_name}: {e}", exc_info=True)
            return 0

        return saved_count

    async def get_recent_changes(
        self,
        target_name: Optional[str] = None,
        limit: int = 50,
        days: int = 7,
    ) -> Sequence[ScheduleChangeLog]:
        """Получает последние изменения расписания."""
        since_date = (datetime.now() - timedelta(days=days)).date()
        async with self.session_factory() as session:
            stmt = select(ScheduleChangeLog).where(ScheduleChangeLog.schedule_date >= since_date)
            if target_name:
                stmt = stmt.where(ScheduleChangeLog.target_name == target_name)
            stmt = stmt.order_by(desc(ScheduleChangeLog.created_at)).limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()

    async def get_frequently_changing_targets(self, days: int = 30, limit: int = 10) -> list[tuple[str, int]]:
        """Возвращает группы или преподавателей с наибольшим количеством изменений расписания."""
        since_date = datetime.now() - timedelta(days=days)
        async with self.session_factory() as session:
            stmt = (
                select(ScheduleChangeLog.target_name, func.count(ScheduleChangeLog.id).label("change_count"))
                .where(ScheduleChangeLog.created_at >= since_date)
                .group_by(ScheduleChangeLog.target_name)
                .order_by(desc("change_count"))
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [(row[0], row[1]) for row in result.all()]
