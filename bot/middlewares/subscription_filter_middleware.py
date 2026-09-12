from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware, Bot
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.config import ADMIN_IDS
from core.subscription_filter import SubscriptionFilterManager

logger = logging.getLogger(__name__)

SUB_CHECK_CALLBACK = "sub_filter_check"


class SubscriptionFilterMiddleware(BaseMiddleware):
    """
    Middleware обязательной подписки на каналы (Gatekeeper).
    Блокирует доступ к боту пользователям, не подписанным на заданные каналы.
    Администраторы (ADMIN_IDS) автоматически пропускаются.
    """

    def __init__(self, session_factory: async_sessionmaker, redis_client: Optional[Redis] = None):
        self.session_factory = session_factory
        self.redis = redis_client
        self.manager = SubscriptionFilterManager(session_factory, redis_client)

    def _get_user_id_and_chat_id(self, update: Update) -> tuple[Optional[int], Optional[int]]:
        msg = getattr(update, "message", None)
        if msg and getattr(msg, "from_user", None):
            chat = getattr(msg, "chat", None)
            chat_id = getattr(chat, "id", None)
            return msg.from_user.id, chat_id

        cb = getattr(update, "callback_query", None)
        if cb and getattr(cb, "from_user", None):
            cb_msg = getattr(cb, "message", None)
            chat = getattr(cb_msg, "chat", None) if cb_msg else None
            chat_id = getattr(chat, "id", None)
            return cb.from_user.id, chat_id

        return None, None

    def _build_gatekeeper_markup(self, channels: list[dict[str, Any]]) -> InlineKeyboardMarkup:
        rows = []
        for ch in channels:
            title = ch.get("title", "Канал")
            link = ch.get("invite_link") or f"https://t.me/{str(ch.get('channel_id', '')).lstrip('@')}"
            rows.append([InlineKeyboardButton(text=f"📢 {title}", url=link)])

        rows.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data=SUB_CHECK_CALLBACK)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _build_gatekeeper_text(self, channels: list[dict[str, Any]]) -> str:
        lines = [
            "<b>📢 Обязательная подписка</b>\n",
            "Для использования бота необходимо подписаться на следующие каналы:\n",
        ]
        for i, ch in enumerate(channels, 1):
            title = ch.get("title", "Канал")
            lines.append(f"  {i}. <b>{title}</b>")

        lines.append("\n<i>После подписки нажмите кнопку «🔄 Проверить подписку» ниже.</i>")
        return "\n".join(lines)

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        user_id, chat_id = self._get_user_id_and_chat_id(event)
        if not user_id:
            return await handler(event, data)

        # 1. Администраторы всегда имеют полный доступ
        if ADMIN_IDS and user_id in ADMIN_IDS:
            return await handler(event, data)

        bot: Bot = data.get("bot")

        # 2. Обработка кнопки «🔄 Проверить подписку»
        if event.callback_query and event.callback_query.data == SUB_CHECK_CALLBACK:
            if not bot:
                await event.callback_query.answer("⚠️ Ошибка бота, повторите позже.")
                return None

            channels = await self.manager.get_required_channels()
            is_sub, missing = await self.manager.check_user_subscription(
                bot=bot,
                user_id=user_id,
                channels=channels,
                bypass_cache=True,
            )

            if is_sub:
                await event.callback_query.answer("✅ Подписка подтверждена! Приятного пользования ботом.", show_alert=True)
                try:
                    if event.callback_query.message:
                        await event.callback_query.message.delete()
                except Exception:
                    pass

                # Отправляем сообщение для старта
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text="🎉 Вы успешно подтвердили подписку! Нажмите /start или выберите действие в меню.",
                    )
                except Exception:
                    pass
            else:
                await event.callback_query.answer(
                    "❌ Вы ещё не подписались на все обязательные каналы! Пожалуйста, подпишитесь и нажмите кнопку снова.",
                    show_alert=True,
                )
            return None

        # 3. Проверяем, включен ли фильтр
        try:
            filter_enabled = await self.manager.is_filter_enabled()
        except Exception as e:
            logger.error(f"Error checking if sub filter is enabled: {e}")
            return await handler(event, data)

        if not filter_enabled:
            return await handler(event, data)

        # 4. Проверяем каналы
        try:
            channels = await self.manager.get_required_channels()
        except Exception as e:
            logger.error(f"Error getting required channels: {e}")
            return await handler(event, data)

        if not channels:
            return await handler(event, data)

        if not bot:
            return await handler(event, data)

        # 5. Проверяем статус подписки пользователя
        is_sub, missing = await self.manager.check_user_subscription(
            bot=bot,
            user_id=user_id,
            channels=channels,
            bypass_cache=False,
        )

        if is_sub:
            return await handler(event, data)

        # 6. Пользователь не подписан - блокируем и показываем Gatekeeper
        markup = self._build_gatekeeper_markup(channels)
        text = self._build_gatekeeper_text(channels)

        if event.callback_query:
            try:
                await event.callback_query.answer(
                    "⚠️ Для использования бота необходимо подписаться на каналы!",
                    show_alert=True,
                )
            except Exception:
                pass
            if chat_id:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=markup,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except Exception as e:
                    logger.warning(f"Failed to send gatekeeper message to {chat_id}: {e}")
        elif event.message:
            try:
                await event.message.answer(
                    text=text,
                    reply_markup=markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"Failed to send gatekeeper message to {event.message.chat.id}: {e}")

        return None
