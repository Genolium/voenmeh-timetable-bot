import logging
from datetime import datetime, timedelta, UTC 
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import select, update, func, or_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from core.db import User

class UserDataManager:
    def __init__(self, db_url: str):
        self.engine = create_async_engine(db_url)
        self.async_session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def register_user(self, user_id: int, username: Optional[str]):
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            if user:
                user.last_active_date = datetime.now(UTC)
            else:
                user = User(user_id=user_id, username=username)
                session.add(user)
            await session.commit()

    async def set_user_group(self, user_id: int, group: str):
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values(group=group.upper())
            await session.execute(stmt)
            await session.commit()

    async def get_user_group(self, user_id: int) -> Optional[str]:
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            return user.group if user else None

    async def get_user_settings(self, user_id: int) -> Dict[str, bool]:
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            if user:
                return {
                    "evening_notify": user.evening_notify,
                    "morning_summary": user.morning_summary,
                    "lesson_reminders": user.lesson_reminders
                }
            return {"evening_notify": False, "morning_summary": False, "lesson_reminders": False}

    async def update_setting(self, user_id: int, setting_name: str, status: bool):
        if setting_name not in ["evening_notify", "morning_summary", "lesson_reminders"]:
            logging.warning(f"Attempt to update invalid setting: {setting_name}")
            return
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values({setting_name: status})
            await session.execute(stmt)
            await session.commit()

    async def get_total_users_count(self) -> int:
        async with self.async_session_maker() as session:
            stmt = select(func.count(User.user_id))
            result = await session.scalar(stmt)
            return result or 0

    async def get_new_users_count(self, days: int = 1) -> int:
        async with self.async_session_maker() as session:
            start_date = datetime.now(UTC) - timedelta(days=days)
            stmt = select(func.count(User.user_id)).where(User.registration_date >= start_date)
            result = await session.scalar(stmt)
            return result or 0
    
    async def get_subscribed_users_count(self) -> int:
        async with self.async_session_maker() as session:
            stmt = select(func.count(User.user_id)).where(
                or_(User.evening_notify, User.morning_summary, User.lesson_reminders)
            )
            result = await session.scalar(stmt)
            return result or 0

    async def get_active_users_by_period(self, days: int) -> int:
        async with self.async_session_maker() as session:
            start_date = datetime.now(UTC) - timedelta(days=days)
            stmt = select(func.count(User.user_id)).where(User.last_active_date >= start_date)
            result = await session.scalar(stmt)
            return result or 0

    async def get_top_groups(self, limit: int = 5) -> List[Tuple[str, int]]:
        async with self.async_session_maker() as session:
            stmt = (
                select(User.group, func.count(User.user_id).label("user_count"))
                .where(User.group.isnot(None))
                .group_by(User.group)
                .order_by(func.count(User.user_id).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return result.all()

    async def get_all_user_ids(self) -> List[int]:
        async with self.async_session_maker() as session:
            stmt = select(User.user_id)
            result = await session.scalars(stmt)
            return result.all()

    async def get_users_for_evening_notify(self) -> List[Tuple[int, str]]:
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group).where(User.evening_notify == True, User.group.isnot(None))
            result = await session.execute(stmt)
            return result.all()

    async def get_users_for_morning_summary(self) -> List[Tuple[int, str]]:
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group).where(User.morning_summary == True, User.group.isnot(None))
            result = await session.execute(stmt)
            return result.all()

    async def get_users_for_lesson_reminders(self) -> List[Tuple[int, str]]:
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group).where(User.lesson_reminders == True, User.group.isnot(None))
            result = await session.execute(stmt)
            return result.all()