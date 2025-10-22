import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple, Callable, TypeVar, Awaitable
from functools import wraps

from sqlalchemy import select, update, func, or_, case, event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from redis.asyncio.client import Redis
from core.config import MOSCOW_TZ

from core.db import User

T = TypeVar('T')

def cached(ttl: int = 3600) -> Callable:
    """
    Декоратор для кэширования результатов асинхронных функций в Redis.

    Args:
        ttl: Время жизни кэша в секундах (по умолчанию 1 час)

    Returns:
        Декоратор для кэширования
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(self: "UserDataManager", *args: Any, **kwargs: Any) -> T:
            # Создаем ключ кэша на основе имени функции и аргументов
            cache_key: str = f"cache:{func.__name__}:{hash(str(args) + str(sorted(kwargs.items())))}"

            # Получаем Redis клиент
            redis_client: Redis = await self._get_redis_client()

            try:
                # Проверяем кэш
                cached_data = await redis_client.get(cache_key)
                if cached_data:
                    logger.debug(f"Cache hit for {func.__name__}")
                    return json.loads(cached_data.decode('utf-8'))
                else:
                    logger.debug(f"Cache miss for {func.__name__}")
            except Exception as e:
                logger.warning(f"Redis cache error in {func.__name__}: {e}")

            # Выполняем оригинальную функцию
            result: T = await func(self, *args, **kwargs)

            # Сохраняем результат в кэш
            try:
                await redis_client.setex(
                    cache_key,
                    ttl,
                    json.dumps(result, default=str, ensure_ascii=False)
                )
                logger.debug(f"Cached result for {func.__name__}")
            except Exception as e:
                logger.warning(f"Failed to cache result for {func.__name__}: {e}")

            return result
        return wrapper
    return decorator


class UserDataManager:
    """
    Класс для управления данными пользователей через SQLAlchemy.
    """
    def __init__(self, db_url: str, redis_url: Optional[str] = None) -> None:
        tz_name: str = str(MOSCOW_TZ)

        # Настраиваем часовой пояс на уровне соединения с PostgreSQL
        engine_kwargs = {}
        try:
            if db_url.startswith("postgresql") and "+asyncpg" in db_url:
                # Для asyncpg используем server_settings
                engine_kwargs["connect_args"] = {
                    "server_settings": {"TimeZone": tz_name}
                }
        except Exception:
            # На всякий случай не блокируем инициализацию
            engine_kwargs = {}

        self.engine = create_async_engine(db_url, **engine_kwargs)

        # Для sync-адаптеров (psycopg2) или если server_settings недоступно
        try:
            if db_url.startswith("postgresql") and "+asyncpg" not in db_url:
                @event.listens_for(self.engine.sync_engine, "connect")
                def _set_timezone(dbapi_connection, connection_record):
                    try:
                        cursor = dbapi_connection.cursor()
                        cursor.execute(f"SET TIME ZONE '{tz_name}'")
                        cursor.close()
                    except Exception:
                        # Не прерываем подключение, если установка TZ не удалась
                        pass
        except Exception:
            pass

        self.async_session_maker = async_sessionmaker(self.engine, expire_on_commit=False)

        # Redis клиент для кэширования
        self._redis_url = redis_url
        self._redis_client = None

    async def _get_redis_client(self) -> Redis:
        """Получает Redis клиент, создавая его при необходимости."""
        if self._redis_client is None and self._redis_url:
            self._redis_client = Redis.from_url(self._redis_url)
        elif self._redis_client is None:
            # Fallback: получить из config если URL не передан
            from core.config import get_redis_client
            self._redis_client = await get_redis_client()
        return self._redis_client

    async def clear_user_cache(self) -> None:
        """Очищает кэш пользователей (вызывается при изменении данных)."""
        if not self._redis_client:
            return

        try:
            # Удаляем ключи кэша для методов получения пользователей
            cache_patterns: List[str] = [
                "cache:get_users_for_evening_notify:*",
                "cache:get_users_for_morning_summary:*",
                "cache:get_users_for_lesson_reminders:*"
            ]

            for pattern in cache_patterns:
                # Используем SCAN для поиска ключей по паттерну
                cursor: int = 0
                while True:
                    cursor, keys = await self._redis_client.scan(cursor, match=pattern)
                    if keys:
                        await self._redis_client.delete(*keys)
                    if cursor == 0:
                        break

            logger.info("User cache cleared")
        except Exception as e:
            logger.warning(f"Failed to clear user cache: {e}")

    async def register_user(self, user_id: int, username: Optional[str]) -> None:
        """Регистрирует нового пользователя или обновляет дату последней активности."""
        async with self.async_session_maker() as session:
            user = await session.get(User, user_id)
            if user:
                user.last_active_date = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                user = User(user_id=user_id, username=username)
                session.add(user)
            await session.commit()

        # Очищаем кэш пользователей после регистрации/обновления
        await self.clear_user_cache()

    async def set_user_group(self, user_id: int, group: str) -> None:
        """Устанавливает или обновляет учебную группу пользователя."""
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values(group=group.upper())
            await session.execute(stmt)
            await session.commit()

        # Очищаем кэш пользователей после изменения группы
        await self.clear_user_cache()

    async def set_user_type(self, user_id: int, user_type: str) -> None:
        """Устанавливает тип пользователя (student/teacher)."""
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values(user_type=user_type)
            await session.execute(stmt)
            await session.commit()

        # Очищаем кэш пользователей после изменения типа
        await self.clear_user_cache()

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

    async def update_setting(self, user_id: int, setting_name: str, status: bool) -> None:
        """Обновляет конкретную настройку уведомлений для пользователя."""
        if setting_name not in ["evening_notify", "morning_summary", "lesson_reminders"]:
            logging.warning(f"Attempt to update invalid setting: {setting_name}")
            return
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values({setting_name: status})
            await session.execute(stmt)
            await session.commit()

        # Очищаем кэш пользователей после изменения настроек
        await self.clear_user_cache()

    async def set_reminder_time(self, user_id: int, minutes: int) -> None:
        """Устанавливает время напоминания для пользователя."""
        async with self.async_session_maker() as session:
            stmt = update(User).where(User.user_id == user_id).values(reminder_time_minutes=minutes)
            await session.execute(stmt)
            await session.commit()

        # Очищаем кэш пользователей после изменения времени напоминания
        await self.clear_user_cache()

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
    @cached(ttl=3600)  # Кэшируем на 1 час
    async def get_users_for_evening_notify(self) -> List[Tuple[int, str]]:
        """Получает пользователей для вечерних уведомлений с кэшированием."""
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group).where(User.evening_notify == True, User.group.isnot(None))
            result = await session.execute(stmt)
            return [tuple(row) for row in result.all()]

    @cached(ttl=3600)  # Кэшируем на 1 час
    async def get_users_for_morning_summary(self) -> List[Tuple[int, str]]:
        """Получает пользователей для утренних уведомлений с кэшированием."""
        async with self.async_session_maker() as session:
            stmt = select(User.user_id, User.group).where(User.morning_summary == True, User.group.isnot(None))
            result = await session.execute(stmt)
            return [tuple(row) for row in result.all()]

    @cached(ttl=3600)  # Кэшируем на 1 час
    async def get_users_for_lesson_reminders(self) -> List[Tuple[int, str, int]]:
        """Получает пользователей для напоминаний о парах, включая время напоминания с кэшированием."""
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

    async def gather_stats(self) -> tuple:
        """Собирает всю статистику для отчётов."""
        import asyncio

        total_users, dau, wau, mau, subscribed_total, unsubscribed_total, subs_breakdown, top_groups, group_dist = await asyncio.gather(
            self.get_total_users_count(),
            self.get_active_users_by_period(days=1),
            self.get_active_users_by_period(days=7),
            self.get_active_users_by_period(days=30),
            self.get_subscribed_users_count(),
            self.get_unsubscribed_count(),
            self.get_subscription_breakdown(),
            self.get_top_groups(limit=5),
            self.get_group_distribution()
        )

        return total_users, dau, wau, mau, subscribed_total, unsubscribed_total, subs_breakdown, top_groups, group_dist