from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.db.models import Event


class EventsManager:
    """
    Менеджер для управления мероприятиями.
    Работает через переданный async_sessionmaker.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory

    # --- События ---
    async def list_events(
        self,
        *,
        only_published: Optional[bool] = True,
        limit: int = 20,
        offset: int = 0,
        now: Optional[datetime] = None,
        time_filter: Optional[str] = None,  # 'today' | 'this_week' | None (all)
        from_now_only: bool = False,
    ) -> Tuple[List[Event], int]:
        async with self.session_factory() as session:
            base = select(Event)
            if only_published is True:
                base = base.where(Event.is_published == True)
            elif only_published is False:
                base = base.where(Event.is_published == False)
            
            # Фильтр по времени
            if time_filter:
                ref = now or datetime.utcnow()
                if time_filter == 'today':
                    start_day = ref.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_day = start_day + timedelta(days=1)
                    base = base.where(Event.start_at.is_not(None)).where(Event.start_at >= start_day).where(Event.start_at < end_day)
                elif time_filter == 'this_week':
                    weekday = ref.weekday()  # Monday=0
                    start_week = ref.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=weekday)
                    end_week = start_week + timedelta(days=7)
                    base = base.where(Event.start_at.is_not(None)).where(Event.start_at >= start_week).where(Event.start_at < end_week)
            elif from_now_only:
                ref = now or datetime.utcnow()
                # показываем события без даты или с датой >= сейчас
                base = base.where((Event.start_at.is_(None)) | (Event.start_at >= ref.replace(microsecond=0)))
            base = base.order_by(Event.start_at.nullslast(), Event.created_at.desc())

            count_stmt = base.with_only_columns(Event.id).order_by(None)
            count = len((await session.execute(count_stmt)).scalars().all())

            stmt = base.limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all()), count

    async def get_event(self, event_id: int) -> Optional[Event]:
        async with self.session_factory() as session:
            stmt = select(Event).where(Event.id == event_id)
            result = await session.execute(stmt)
            return result.scalars().first()

    async def create_event(
        self,
        *,
        title: str,
        description: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        location: Optional[str] = None,
        link: Optional[str] = None,
        image_file_id: Optional[str] = None,
        is_published: bool = True,
    ) -> int:
        # Валидация данных
        if not title or not title.strip():
            raise ValueError("Заголовок не может быть пустым")
        if len(title.strip()) > 255:
            raise ValueError("Заголовок слишком длинный (максимум 255 символов)")
        if location and len(location) > 255:
            raise ValueError("Локация слишком длинная (максимум 255 символов)")
        if link and len(link) > 512:
            raise ValueError("Ссылка слишком длинная (максимум 512 символов)")
        if image_file_id and len(image_file_id) > 512:
            raise ValueError("File ID изображения слишком длинный")
            
        async with self.session_factory() as session:
            event = Event(
                title=title.strip(),
                description=description,
                start_at=start_at,
                end_at=end_at,
                location=location.strip() if location else None,
                link=link.strip() if link else None,
                image_file_id=image_file_id,
                is_published=is_published,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event.id

    async def update_event(self, event_id: int, **values) -> bool:
        if not values:
            return True
        async with self.session_factory() as session:
            stmt = update(Event).where(Event.id == event_id).values(**values)
            await session.execute(stmt)
            await session.commit()
            return True

    async def delete_event(self, event_id: int) -> bool:
        async with self.session_factory() as session:
            stmt = delete(Event).where(Event.id == event_id)
            await session.execute(stmt)
            await session.commit()
            return True