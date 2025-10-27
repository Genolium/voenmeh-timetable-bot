from aiogram.types import Message, ContentType
from aiogram import Bot
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.text import Const

from .states import Feedback
from core.config import FEEDBACK_CHAT_ID
from core.feedback_manager import FeedbackManager


async def on_feedback_received(message: Message, message_input: MessageInput, manager: DialogManager):
    """
    Получает фидбэк, сохраняет в БД, пересылает и завершает диалог.
    """
    bot: Bot = manager.middleware_data.get("bot")
    session_factory = manager.middleware_data.get("session_factory")

    if not FEEDBACK_CHAT_ID:
        await message.answer("❌ К сожалению, функция обратной связи временно не работает.")
        await manager.done()
        return

    # Сохраняем фидбек в базе данных
    feedback_manager = FeedbackManager(session_factory)

    # Определяем тип сообщения и извлекаем текст/файл
    message_text = None
    message_type = 'text'
    file_id = None

    if message.text:
        message_text = message.text
        message_type = 'text'
    elif message.photo:
        message_text = message.caption or "Фото без подписи"
        message_type = 'photo'
        file_id = message.photo[-1].file_id  # Берем фото в максимальном разрешении
    elif message.video:
        message_text = message.caption or "Видео без подписи"
        message_type = 'video'
        file_id = message.video.file_id
    elif message.document:
        message_text = message.caption or f"Документ: {message.document.file_name}"
        message_type = 'document'
        file_id = message.document.file_id
    elif message.audio:
        message_text = message.caption or "Аудио без подписи"
        message_type = 'audio'
        file_id = message.audio.file_id
    elif message.voice:
        message_text = "Голосовое сообщение"
        message_type = 'voice'
        file_id = message.voice.file_id
    elif message.sticker:
        message_text = f"Стикер: {message.sticker.emoji}"
        message_type = 'sticker'
        file_id = message.sticker.file_id

    # Сохраняем в БД
    await feedback_manager.create_feedback(
        user_id=message.from_user.id,
        username=message.from_user.username,
        user_full_name=message.from_user.full_name,
        message_text=message_text,
        message_type=message_type,
        file_id=file_id
    )

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
        f"ID: <code>{message.from_user.id}</code>\n"
        f"Тип: {message_type}"
    )
    await bot.send_message(FEEDBACK_CHAT_ID, user_info)

    await message.answer(
        "✅ <b>Спасибо! Ваш отзыв отправлен.</b>\n"
        "Мы ценим вашу помощь в улучшении бота!\n\n"
        "P.S. Все новости и обновления мы публикуем в нашем канале "
        "<a href='https://t.me/voenmeh404'>Аудитория 404 | Военмех</a>. Подписывайтесь, чтобы быть в курсе!\n\n"
        "<i>Вы можете вернуться в главное меню командой /start.</i>"
    )
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