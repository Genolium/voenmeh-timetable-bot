from aiogram.types import Message, ContentType
from aiogram import Bot
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.text import Const

from .states import Feedback
from core.config import FEEDBACK_CHAT_ID

async def on_feedback_received(message: Message, message_input: MessageInput, manager: DialogManager):
    """
    Получает фидбэк, пересылает его и завершает диалог.
    """
    bot: Bot = manager.middleware_data.get("bot")

    if not FEEDBACK_CHAT_ID:
        await message.answer("❌ К сожалению, функция обратной связи временно не работает.")
        await manager.done()
        return

    # Пересылаем сообщение в чат для фидбэка
    await bot.forward_message(
        chat_id=FEEDBACK_CHAT_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id
    )

    # Дополнительно отправляем информацию о пользователе
    user_info = (
        f"📝 <b>Новый фидбэк!</b>\n"
        f"От: {message.from_user.full_name}\n"
        f"Ник: @{message.from_user.username}\n"
        f"ID: <code>{message.from_user.id}</code>"
    )
    await bot.send_message(FEEDBACK_CHAT_ID, user_info)
    
    await message.answer("✅ <b>Спасибо! Ваш отзыв отправлен.</b>\nМы ценим вашу помощь в улучшении бота!")
    await manager.done()


feedback_dialog = Dialog(
    Window(
        Const(
            "📝 <b>Пожалуйста, напишите ваш отзыв, предложение или сообщение об ошибке.</b>\n\n"
            "Вы можете отправить текст, фото или видео. Я перешлю ваше сообщение администратору."
        ),
        # MessageInput ловит любое сообщение, так как мы не указали фильтр по типу контента
        MessageInput(on_feedback_received, content_types=[ContentType.ANY]),
        state=Feedback.enter_feedback
    )
)