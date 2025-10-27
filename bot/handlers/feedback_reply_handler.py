"""
Обработчик ответов на фидбек в чате FEEDBACK_CHAT_ID
"""
import logging
import re
from aiogram import Bot, Router, F
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
    async def handle_feedback_reply(message: Message, bot: Bot, session_factory):
    """
    Обрабатывает ответы на фидбек в чате FEEDBACK_CHAT_ID.
    
    Когда администратор отвечает на пересланное сообщение фидбека или на сообщение с информацией о пользователе,
    бот отправляет ответ пользователю.
    """
        # Проверяем, что это reply на сообщение
        if not message.reply_to_message:
            return
    
    replied_msg = message.reply_to_message
    user_id = None
    
    # Вариант 1: Сообщение переслано от пользователя (если у него не скрыта пересылка)
    if replied_msg.forward_from:
        user_id = replied_msg.forward_from.id
        logger.info(f"Found user_id from forward_from: {user_id}")
    
    # Вариант 2: Пытаемся извлечь ID из текста сообщения (user info message от бота)
    if not user_id and (replied_msg.text or replied_msg.caption):
        text = replied_msg.text or replied_msg.caption
        # Ищем паттерн "ID: <code>123456</code>" или "ID: 123456"
        match = re.search(r'ID:\s*(?:<code>)?(\d+)(?:</code>)?', text)
        if match:
            user_id = int(match.group(1))
            logger.info(f"Found user_id from text pattern: {user_id}")
    
    # Вариант 3: Если это reply на пересланное сообщение со скрытым отправителем,
    # ищем следующее сообщение в цепочке (сообщение с info от бота)
    if not user_id and (replied_msg.forward_sender_name or replied_msg.forward_from_chat):
        logger.info("Forward from hidden user, trying to find user_id from next message")
        # В этом случае бот должен был отправить следующее сообщение с информацией о пользователе
        # Но мы не можем легко получить следующее сообщение, поэтому просим админа ответить на сообщение с информацией
        await message.reply(
            "⚠️ <b>Пользователь скрыл пересылку сообщений</b>\n\n"
            "Пожалуйста, ответьте на следующее сообщение от бота с информацией о пользователе "
            "(оно содержит ID пользователя).",
            parse_mode="HTML"
        )
        return
    
    if not user_id:
        await message.reply(
            "❌ <b>Не удалось определить пользователя для ответа</b>\n\n"
            "Убедитесь, что вы отвечаете на:\n"
            "• Пересланное сообщение от пользователя\n"
            "• Или на сообщение с информацией о пользователе (с его ID)",
            parse_mode="HTML"
        )
        return
    
    # Отправляем ответ пользователю
    try:
        admin_response = message.text or message.caption or "Администратор прислал вам сообщение"
        
        if message.content_type == 'text':
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "📬 <b>Ответ на ваш фидбек</b>\n\n"
                    f"🔄 <b>Ответ администратора:</b>\n{admin_response}\n\n"
                    "Спасибо за обращение! Мы всегда рады помочь. 🤖"
                ),
                parse_mode="HTML"
            )
        else:
            # Копируем медиа-сообщение
            caption_text = (
                "📬 <b>Ответ на ваш фидбек</b>\n\n"
                f"🔄 <b>Ответ администратора:</b>\n{message.caption or ''}\n\n"
                "Спасибо за обращение! Мы всегда рады помочь. 🤖"
            )
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                caption=caption_text,
                parse_mode="HTML"
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
                    .where(Feedback.user_id == user_id, Feedback.is_answered == False)
                    .order_by(Feedback.created_at.desc())
                )
                feedback = result.scalar_one_or_none()
                
                if feedback:
                    await feedback_manager.answer_feedback(
                        feedback.id,
                        message.from_user.id,
                        admin_response
                    )
                    logger.info(f"Marked feedback #{feedback.id} as answered")
        except Exception as e:
            logger.error(f"Error marking feedback as answered: {e}")
        
        # Подтверждаем администратору
        await message.reply(
            f"✅ <b>Ответ отправлен пользователю!</b>\n"
            f"👤 User ID: <code>{user_id}</code>",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error sending reply to user {user_id}: {e}")
        await message.reply(
            f"❌ <b>Ошибка при отправке ответа</b>\n\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Ошибка: {str(e)}",
            parse_mode="HTML"
        )
else:
    # Если FEEDBACK_CHAT_ID не задан, обработчик не регистрируется — это допустимо для тестов/CI
    pass
