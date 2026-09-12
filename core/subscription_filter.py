from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from redis.asyncio.client import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.db.models import RequiredChannel, SubscriptionFilterSetting

logger = logging.getLogger(__name__)

KEY_FILTER_ENABLED = "timetable:sub_filter:enabled"
KEY_CHANNELS_LIST = "timetable:sub_filter:channels"
KEY_USER_PREFIX = "timetable:sub_filter:user"
USER_CACHE_TTL = 600  # 10 минут кэширования статуса подписки пользователя


class SubscriptionFilterManager:
    """
    Менеджер для управления обязательной подпиской на каналы.
    Поддерживает хранение в PostgreSQL и высокопроизводительное кэширование в Redis.
    """

    def __init__(self, session_factory: async_sessionmaker, redis_client: Optional[Redis] = None):
        self.session_factory = session_factory
        self.redis = redis_client

    async def is_filter_enabled(self) -> bool:
        """Проверяет, включен ли фильтр обязательной подписки."""
        if self.redis:
            try:
                cached = await self.redis.get(KEY_FILTER_ENABLED)
                if cached is not None:
                    return cached == b"1" or cached == "1"
            except Exception as e:
                logger.warning(f"Error reading sub filter enabled from Redis: {e}")

        async with self.session_factory() as session:
            result = await session.execute(
                select(SubscriptionFilterSetting).order_by(SubscriptionFilterSetting.id.desc()).limit(1)
            )
            setting = result.scalar_one_or_none()
            enabled = setting.is_enabled if setting else False

            if self.redis:
                try:
                    await self.redis.set(KEY_FILTER_ENABLED, "1" if enabled else "0", ex=300)
                except Exception as e:
                    logger.warning(f"Error caching sub filter enabled to Redis: {e}")

            return enabled

    async def set_filter_enabled(self, enabled: bool) -> None:
        """Включает или выключает фильтр обязательной подписки."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(SubscriptionFilterSetting).order_by(SubscriptionFilterSetting.id.desc()).limit(1)
            )
            setting = result.scalar_one_or_none()
            if setting:
                setting.is_enabled = enabled
            else:
                setting = SubscriptionFilterSetting(is_enabled=enabled)
                session.add(setting)
            await session.commit()

        if self.redis:
            try:
                await self.redis.set(KEY_FILTER_ENABLED, "1" if enabled else "0", ex=300)
                # Сбрасываем кэш каналов и пользователей
                await self.invalidate_all_caches()
            except Exception as e:
                logger.warning(f"Error invalidating sub filter in Redis: {e}")

    async def get_required_channels(self) -> List[Dict[str, Any]]:
        """
        Возвращает список обязательных каналов в виде словарей:
        [{"id": int, "channel_id": str, "title": str, "invite_link": str}]
        """
        if self.redis:
            try:
                cached = await self.redis.get(KEY_CHANNELS_LIST)
                if cached:
                    data = json.loads(cached.decode() if isinstance(cached, bytes) else cached)
                    return data
            except Exception as e:
                logger.warning(f"Error reading required channels from Redis: {e}")

        async with self.session_factory() as session:
            result = await session.execute(select(RequiredChannel).order_by(RequiredChannel.id.asc()))
            channels = result.scalars().all()
            channels_data = [
                {
                    "id": ch.id,
                    "channel_id": ch.channel_id,
                    "title": ch.title,
                    "invite_link": ch.invite_link or f"https://t.me/{ch.channel_id.lstrip('@')}",
                }
                for ch in channels
            ]

            if self.redis:
                try:
                    await self.redis.set(KEY_CHANNELS_LIST, json.dumps(channels_data), ex=300)
                except Exception as e:
                    logger.warning(f"Error caching required channels to Redis: {e}")

            return channels_data

    async def add_required_channel(
        self,
        channel_id: str,
        title: str,
        invite_link: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Добавляет канал в список обязательных или обновляет существующий."""
        clean_channel_id = str(channel_id).strip()
        async with self.session_factory() as session:
            result = await session.execute(
                select(RequiredChannel).where(RequiredChannel.channel_id == clean_channel_id)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.title = title
                if invite_link:
                    existing.invite_link = invite_link
                await session.commit()
                channel_dict = {
                    "id": existing.id,
                    "channel_id": existing.channel_id,
                    "title": existing.title,
                    "invite_link": existing.invite_link,
                }
            else:
                new_channel = RequiredChannel(
                    channel_id=clean_channel_id,
                    title=title,
                    invite_link=invite_link,
                )
                session.add(new_channel)
                await session.commit()
                await session.refresh(new_channel)
                channel_dict = {
                    "id": new_channel.id,
                    "channel_id": new_channel.channel_id,
                    "title": new_channel.title,
                    "invite_link": new_channel.invite_link,
                }

        await self.invalidate_all_caches()
        return channel_dict

    async def remove_required_channel(self, channel_id: str) -> bool:
        """Удаляет канал из списка обязательных по channel_id или ID записи."""
        clean_channel_id = str(channel_id).strip()
        async with self.session_factory() as session:
            if clean_channel_id.isdigit():
                # Пробуем удалить как PK, иначе как channel_id
                res = await session.execute(
                    delete(RequiredChannel).where(
                        (RequiredChannel.id == int(clean_channel_id))
                        | (RequiredChannel.channel_id == clean_channel_id)
                    )
                )
            else:
                res = await session.execute(
                    delete(RequiredChannel).where(RequiredChannel.channel_id == clean_channel_id)
                )
            await session.commit()
            removed = res.rowcount > 0

        await self.invalidate_all_caches()
        return removed

    async def invalidate_user_cache(self, user_id: int) -> None:
        """Инвалидирует кэш статуса проверки для конкретного пользователя."""
        if self.redis:
            try:
                await self.redis.delete(f"{KEY_USER_PREFIX}:{user_id}")
            except Exception:
                pass

    async def invalidate_all_caches(self) -> None:
        """Сбрасывает кэш каналов в Redis."""
        if self.redis:
            try:
                await self.redis.delete(KEY_CHANNELS_LIST)
            except Exception:
                pass

    async def check_user_subscription(
        self,
        bot: Bot,
        user_id: int,
        channels: Optional[List[Dict[str, Any]]] = None,
        bypass_cache: bool = False,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Проверяет, подписан ли пользователь на все обязательные каналы.

        Returns:
            Tuple[bool, List[Dict[str, Any]]]:
                (is_subscribed, list_of_missing_channels)
        """
        user_cache_key = f"{KEY_USER_PREFIX}:{user_id}"

        # 1. Проверяем кэш Redis, если не форсированная проверка
        if not bypass_cache and self.redis:
            try:
                cached_status = await self.redis.get(user_cache_key)
                if cached_status == b"1" or cached_status == "1":
                    return True, []
            except Exception as e:
                logger.warning(f"Error checking user sub cache in Redis: {e}")

        if channels is None:
            channels = await self.get_required_channels()

        if not channels:
            return True, []

        missing_channels: List[Dict[str, Any]] = []

        for ch in channels:
            cid = ch["channel_id"]
            # Преобразуем числовые ID каналов (начинающиеся с -100 или цифр) в int
            target_chat_id = int(cid) if (cid.startswith("-") and cid[1:].isdigit()) or cid.isdigit() else cid
            try:
                member = await bot.get_chat_member(chat_id=target_chat_id, user_id=user_id)
                # Статусы участника
                if member.status not in ("member", "administrator", "creator", "restricted"):
                    missing_channels.append(ch)
            except TelegramForbiddenError:
                # Бот заблокирован или не имеет доступа к каналу
                logger.warning(f"Bot forbidden from checking channel {cid}")
                missing_channels.append(ch)
            except TelegramBadRequest as e:
                err_msg = str(e).lower()
                if "user not found" in err_msg or "participant_id_invalid" in err_msg or "chat not found" in err_msg:
                    missing_channels.append(ch)
                else:
                    logger.warning(f"BadRequest when checking sub for {user_id} in {cid}: {e}")
                    # В случае неизвестной ошибки API не блокируем пользователя жестко
            except Exception as e:
                logger.error(f"Unexpected error checking sub for {user_id} in {cid}: {e}")

        if not missing_channels:
            # Все каналы подписаны, кэшируем статус
            if self.redis:
                try:
                    await self.redis.set(user_cache_key, "1", ex=USER_CACHE_TTL)
                except Exception as e:
                    logger.warning(f"Error caching user sub status in Redis: {e}")
            return True, []

        # Есть неподписанные каналы
        if self.redis:
            try:
                await self.redis.delete(user_cache_key)
            except Exception:
                pass

        return False, missing_channels
