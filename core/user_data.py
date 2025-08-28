import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import select, update, func, or_, case
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from core.db import User

class UserDataManager:
    """
    Класс для управления данными пользователей через SQLAlchemy.
    """
    def __init__(self, db_url: str):
        self.engine = create_async_engine(db_url)
        self.async_session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def register_user(self, user_id: int, username: Optional[str]):
        """Регистрирует нового пользователя или обновляет дату последней активности."""
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            if user:
                user.last_active_date = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                user = User(user_id=user_id, username=username)
                session.add(user)
            await session.commit()

    async def set_user_group(self, user_id: int, group: str):
        """Устанавливает или обновляет учебную группу пользователя."""
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values(group=group.upper())
            await session.execute(stmt)
            await session.commit()

    async def set_user_type(self, user_id: int, user_type: str):
        """Устанавливает тип пользователя (student/teacher)."""
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values(user_type=user_type)
            await session.execute(stmt)
            await session.commit()

    async def get_user_type(self, user_id: int) -> Optional[str]:
        """Возвращает тип пользователя (student/teacher)."""
        async with self.async_session_maker() as session:
            stmt = select(User.user_type).where(User.user_id == user_id)
            result = await session.scalar(stmt)
            return result

    async def get_user_group(self, user_id: int) -> Optional[str]:
        """Получает учебную группу пользователя."""
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            return user.group if user else None

    async def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Получает настройки уведомлений для пользователя."""
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            if user:
                return {
                    "evening_notify": user.evening_notify,
                    "morning_summary": user.morning_summary,
                    "lesson_reminders": user.lesson_reminders,
                    "reminder_time_minutes": user.reminder_time_minutes
                }
            return {"evening_notify": False, "morning_summary": False, "lesson_reminders": False, "reminder_time_minutes": 60}

    async def update_setting(self, user_id: int, setting_name: str, status: bool):
        """Обновляет конкретную настройку уведомлений для пользователя."""
        if setting_name not in ["evening_notify", "morning_summary", "lesson_reminders"]:
            logging.warning(f"Attempt to update invalid setting: {setting_name}")
            return
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values({setting_name: status})
            await session.execute(stmt)
            await session.commit()

    async def set_reminder_time(self, user_id: int, minutes: int):
        """Устанавливает время напоминания для пользователя."""
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values(reminder_time_minutes=minutes)
            await session.execute(stmt)
            await session.commit()

    async def get_full_user_info(self, user_id: int) -> Optional[User]:
        """Возвращает полный объект User по его ID."""
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            return user

    # --- Методы для статистики ---
    async def get_total_users_count(self) -> int:
        async with self.async_session_maker() as session:
            stmt = select(func.count(User.user_id))
            result = await session.scalar(stmt)
            return result or 0

    async def get_new_users_count(self, days: int) -> int:
        async with self.async_session_maker() as session:
            start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
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
            start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            stmt = select(func.count(User.user_id)).where(User.last_active_date >= start_date)
            result = await session.scalar(stmt)
            return result or 0

    async def get_all_users_with_groups(self) -> List[Tuple[int, Optional[str]]]:
        """Получает всех пользователей с их группами."""
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group)
            result = await session.execute(stmt)
            return [(row.user_id, row.group) for row in result.fetchall()]

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
            return [tuple(row) for row in result.all()]

    async def get_unsubscribed_count(self) -> int:
        """Считает количество пользователей, отписавшихся от ВСЕХ рассылок."""
        async with self.async_session_maker() as session:
            stmt = select(func.count(User.user_id)).where(
                User.evening_notify == False,
                User.morning_summary == False,
                User.lesson_reminders == False
            )
            result = await session.scalar(stmt)
            return result or 0

    async def get_subscription_breakdown(self) -> Dict[str, int]:
        """Возвращает разбивку по типам подписок."""
        async with self.async_session_maker() as session:
            stmt = select(
                func.count(User.user_id).filter(User.evening_notify == True).label("evening"),
                func.count(User.user_id).filter(User.morning_summary == True).label("morning"),
                func.count(User.user_id).filter(User.lesson_reminders == True).label("reminders")
            )
            result = await session.execute(stmt)
            row = result.one_or_none()
            return dict(row._mapping) if row else {}

    async def get_group_distribution(self) -> Dict[str, int]:
        """Возвращает распределение пользователей по размеру групп."""
        async with self.async_session_maker() as session:
            subquery = (
                select(func.count(User.user_id).label("students_in_group"))
                .where(User.group.isnot(None))
                .group_by(User.group)
                .subquery()
            )
            stmt = select(
                case(
                    (subquery.c.students_in_group == 1, "1 студент"),
                    (subquery.c.students_in_group.between(2, 5), "2-5 студентов"),
                    (subquery.c.students_in_group.between(6, 10), "6-10 студентов"),
                    else_="11+ студентов"
                ).label("group_size_category"),
                func.count().label("number_of_groups")
            ).group_by("group_size_category").order_by("group_size_category")
            
            result = await session.execute(stmt)
            return {row.group_size_category: row.number_of_groups for row in result}

    async def get_all_user_ids(self) -> List[int]:
        async with self.async_session_maker() as session:
            stmt = select(User.user_id)
            result = await session.scalars(stmt)
            return list(result)

    # --- Методы для рассылок ---
    async def get_users_for_evening_notify(self) -> List[Tuple[int, str]]:
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group).where(User.evening_notify == True, User.group.isnot(None))
            result = await session.execute(stmt)
            return [tuple(row) for row in result.all()]

    async def get_users_for_morning_summary(self) -> List[Tuple[int, str]]:
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group).where(User.morning_summary == True, User.group.isnot(None))
            result = await session.execute(stmt)
            return [tuple(row) for row in result.all()]

    async def get_users_for_lesson_reminders(self) -> List[Tuple[int, str, int]]:
        """Получает пользователей для напоминаний о парах, включая время напоминания."""
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group, User.reminder_time_minutes).where(User.lesson_reminders == True, User.group.isnot(None))
            result = await session.execute(stmt)
            rows = result.all()
            adjusted: List[Tuple[int, str, int]] = []
            for row in rows:
                user_id, group, minutes = row
                # По требованиям рассылки, дефолтное значение для напоминаний — 20 минут,
                # даже если в настройках по умолчанию хранится 60.
                if minutes is None or minutes == 60:
                    minutes = 20
                adjusted.append((user_id, group, minutes))
            return adjusted

    async def get_admin_users(self) -> List[int]:
        """Получает список ID администраторов бота."""
        from core.config import ADMIN_IDS
        return ADMIN_IDS