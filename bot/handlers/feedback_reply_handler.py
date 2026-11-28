"""
Обработчик ответов на фидбек в чате FEEDBACK_CHAT_ID
"""

import logging
import re
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import Message

from core.config import FEEDBACK_CHAT_ID
from core.feedback_manager import FeedbackManager

logger = logging.getLogger(__name__)
feedback_reply_router = Router()

# Безопасно парсим FEEDBACK_CHAT_ID, чтобы не падать при None/пустом значении в тестах или CI
try:
    FEEDBACK_CHAT_ID_INT = int(FEEDBACK_CHAT_ID) if FEEDBACK_CHAT_ID is not None else None
except Exception:
    FEEDBACK_CHAT_ID_INT = None


if FEEDBACK_CHAT_ID_INT is not None:

    @feedback_reply_router.message(
        F.chat.id == FEEDBACK_CHAT_ID_INT,
        F.reply_to_message,
    )
    async def handle_feedback_reply(message: Message, bot: Bot, session_factory: Any) -> None:
        """
        Обрабатывает ответы на фидбек в чате FEEDBACK_CHAT_ID.

        Когда администратор отвечает на пересланное сообщение фидбека или на сообщение с информацией о пользователе,
        бот отправляет ответ пользователю.
        """

        # Проверяем, что это reply на сообщение
        if not message.reply_to_message:
            return

        from core.config import ADMIN_IDS

        if ADMIN_IDS and (not message.from_user or message.from_user.id not in ADMIN_IDS):
            return

        replied_msg = message.reply_to_message
        user_id = None

        # Вариант 1: Сообщение переслано от пользователя (если у него не скрыта пересылка)
        if replied_msg.forward_from:
            user_id = replied_msg.forward_from.id
            logger.info(f"Found user_id from forward_from: {user_id}")

        # Вариант 2 (пересылка со скрытым отправителем проверяется ДО regex по тексту,
        # чтобы исключить подмену user_id через "ID: <victim>" в тексте самого пользователя)
        if not user_id and (replied_msg.forward_sender_name or replied_msg.forward_from_chat):
            logger.info("Forward from hidden user, trying to find user_id from next message")
            await message.reply(
                "⚠️ <b>Пользователь скрыл пересылку сообщений</b>\n\n"
                "Пожалуйста, ответьте на следующее сообщение от бота с информацией о пользователе "
                "(оно содержит ID пользователя).",
                parse_mode="HTML",
            )
            return

        # Вариант 3: Пытаемся извлечь ID из текста сервисного сообщения от бота
        if not user_id and (replied_msg.text or replied_msg.caption):
            text = replied_msg.text or replied_msg.caption
            # Ищем паттерн "ID: <code>123456</code>" или "ID: 123456"
            match = re.search(r"ID:\s*(?:<code>)?(\d+)(?:</code>)?", text)
            if match:
                user_id = int(match.group(1))
                logger.info(f"Found user_id from text pattern: {user_id}")

        if not user_id:
            await message.reply(
                "❌ <b>Не удалось определить пользователя для ответа</b>\n\n"
                "Убедитесь, что вы отвечаете на:\n"
                "• Пересланное сообщение от пользователя\n"
                "• Или на сообщение с информацией о пользователе (с его ID)",
                parse_mode="HTML",
            )
            return

        # Отправляем ответ пользователю
        try:
            import html as html_lib

            admin_response = message.text or message.caption or "Администратор прислал вам сообщение"
            escaped_response = html_lib.escape(admin_response)

            if message.content_type == "text":
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "📬 <b>Ответ на ваш фидбек</b>\n\n"
                        f"🔄 <b>Ответ администратора:</b>\n{escaped_response}\n\n"
                        "Спасибо за обращение! Мы всегда рады помочь. 🤖"
                    ),
                    parse_mode="HTML",
                )
            else:
                # Копируем медиа-сообщение
                escaped_caption = html_lib.escape(message.caption or "")
                caption_text = (
                    "📬 <b>Ответ на ваш фидбек</b>\n\n"
                    f"🔄 <b>Ответ администратора:</b>\n{escaped_caption}\n\n"
                    "Спасибо за обращение! Мы всегда рады помочь. 🤖"
                )
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    caption=caption_text,
                    parse_mode="HTML",
                )

            # Отмечаем последний фидбек пользователя как отвеченный
            try:
                feedback_manager = FeedbackManager(session_factory)
                # Ищем последний неотвеченный фидбек от этого пользователя
                async with session_factory() as session:
                    from sqlalchemy import select

                    from core.db.models import Feedback

                    result = await session.execute(
                        select(Feedback)
                        .where(Feedback.user_id == user_id, Feedback.is_answered.is_(False))
                        .order_by(Feedback.created_at.desc())
                        .limit(1)
                    )
                    feedback = result.scalar_one_or_none()

                    if feedback:
                        responder_id = message.from_user.id if message.from_user else 0
                        await feedback_manager.answer_feedback(feedback.id, responder_id, admin_response)
                        logger.info(f"Marked feedback #{feedback.id} as answered")
            except Exception as e:
                logger.error(f"Error marking feedback as answered: {e}")

            # Подтверждаем администратору
            await message.reply(
                f"✅ <b>Ответ отправлен пользователю!</b>\n" f"👤 User ID: <code>{user_id}</code>",
                parse_mode="HTML",
            )

        except Exception as e:
            import html as html_lib

            logger.error(f"Error sending reply to user {user_id}: {e}")
            await message.reply(
                f"❌ <b>Ошибка при отправке ответа</b>\n\n"
                f"User ID: <code>{user_id}</code>\n"
                f"Ошибка: {html_lib.escape(str(e))}",
                parse_mode="HTML",
            )

else:
    # Если FEEDBACK_CHAT_ID не задан, обработчик не регистрируется — это допустимо для тестов/CI
    pass
