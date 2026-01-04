from aiogram import Bot
from aiogram.types import ContentType, Message
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.text import Const, Format

from core.config import FEEDBACK_CHAT_ID
from core.feedback_manager import FeedbackManager
from core.i18n import i18n
from core.user_data import UserDataManager

from .states import Feedback


async def get_feedback_data(dialog_manager: DialogManager, **kwargs):
    user_id = dialog_manager.event.from_user.id
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_lang = await user_data_manager.get_user_language(user_id)

    # Реинициализируем функцию перевода с актуальным языком
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    return {
        "feedback_enter": _("feedback_enter"),
    }


async def on_feedback_received(message: Message, message_input: MessageInput, manager: DialogManager):
    """
    Получает фидбэк, сохраняет в БД, пересылает и завершает диалог.
    """
    bot: Bot = manager.middleware_data.get("bot")
    session_factory = manager.middleware_data.get("session_factory")
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_lang = await user_data_manager.get_user_language(message.from_user.id)

    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    if not FEEDBACK_CHAT_ID:
        await message.answer(_("feedback_offline"))
        await manager.done()
        return

    # Сохраняем фидбек в базе данных
    feedback_manager = FeedbackManager(session_factory)

    # Определяем тип сообщения и извлекаем текст/файл
    message_text = None
    message_type = "text"
    file_id = None

    if message.text:
        message_text = message.text
        message_type = "text"
    elif message.photo:
        message_text = message.caption or "Фото без подписи"
        message_type = "photo"
        file_id = message.photo[-1].file_id  # Берем фото в максимальном разрешении
    elif message.video:
        message_text = message.caption or "Видео без подписи"
        message_type = "video"
        file_id = message.video.file_id
    elif message.document:
        message_text = message.caption or f"Документ: {message.document.file_name}"
        message_type = "document"
        file_id = message.document.file_id
    elif message.audio:
        message_text = message.caption or "Аудио без подписи"
        message_type = "audio"
        file_id = message.audio.file_id
    elif message.voice:
        message_text = "Голосовое сообщение"
        message_type = "voice"
        file_id = message.voice.file_id
    elif message.sticker:
        message_text = f"Стикер: {message.sticker.emoji}"
        message_type = "sticker"
        file_id = message.sticker.file_id

    # Сохраняем в БД
    await feedback_manager.create_feedback(
        user_id=message.from_user.id,
        username=message.from_user.username,
        user_full_name=message.from_user.full_name,
        message_text=message_text,
        message_type=message_type,
        file_id=file_id,
    )

    # Пересылаем сообщение в чат для фидбэка
    await bot.forward_message(
        chat_id=FEEDBACK_CHAT_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
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

    await message.answer(_("feedback_sent"))
    await manager.done()


feedback_dialog = Dialog(
    Window(
        Format("{feedback_enter}"),
        # MessageInput ловит любое сообщение, так как мы не указали фильтр по типу контента
        MessageInput(on_feedback_received, content_types=[ContentType.ANY]),
        state=Feedback.enter_feedback,
        getter=get_feedback_data,
    )
)
