from aiogram.types import Message, ContentType
from aiogram import Bot
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.text import Const, Format
from aiogram_dialog.widgets.kbd import Button, SwitchTo, Back

from .states import Admin
from core.user_data import UserDataManager

# --- Геттер для окна статистики ---
async def get_stats_data(user_data_manager: UserDataManager, **kwargs):
    total_users = await user_data_manager.get_total_users_count()
    new_today = await user_data_manager.get_new_users_count(days=1)
    new_week = await user_data_manager.get_new_users_count(days=7)
    active_users = await user_data_manager.get_active_users_count()
    top_groups = await user_data_manager.get_top_groups(limit=5)

    top_groups_text = "\n".join([f"  - {group or 'Не указана'}: {count}" for group, count in top_groups])
    if not top_groups_text:
        top_groups_text = "Нет данных"

    stats_text = (
        f"📊 <b>Статистика бота:</b>\n\n"
        f"👤 Всего пользователей: <b>{total_users}</b>\n"
        f"📈 Новых за сегодня: <b>{new_today}</b>\n"
        f"📈 Новых за неделю: <b>{new_week}</b>\n"
        f"🔥 Активных (с подписками): <b>{active_users}</b>\n\n"
        f"🏆 <b>Топ-5 групп:</b>\n{top_groups_text}"
    )
    return {"stats_text": stats_text}


# --- Обработчик для рассылки (остается без изменений) ---
async def on_broadcast_received(message: Message, message_input: MessageInput, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")

    try:
        all_users = await user_data_manager.get_all_user_ids()
        sent_count, failed_count = 0, 0
        
        await bot.send_message(message.from_user.id, f"🚀 Начинаю рассылку для {len(all_users)} пользователей...")

        for user_id in all_users:
            try:
                await message.copy_to(chat_id=user_id)
                sent_count += 1
            except Exception:
                failed_count += 1
        
        await bot.send_message(
            message.from_user.id,
            f"✅ Рассылка завершена!\n👍 Отправлено: {sent_count}\n👎 Ошибок: {failed_count}"
        )
    except Exception as e:
        await bot.send_message(message.from_user.id, f"❌ Ошибка рассылки: {e}")
    
    await manager.done()

# --- Диалог админки ---
admin_dialog = Dialog(
    # --- Окно 1: Главное меню админки ---
    Window(
        Const("👑 <b>Админ-панель</b>\n\nВыберите действие:"),
        SwitchTo(Const("📊 Статистика"), id="stats", state=Admin.stats),
        SwitchTo(Const("📣 Сделать рассылку"), id="broadcast", state=Admin.broadcast),
        state=Admin.menu
    ),
    # --- Окно 2: Статистика ---
    Window(
        Format("{stats_text}"),
        Back(Const("◀️ Назад")),
        getter=get_stats_data,
        state=Admin.stats
    ),
    # --- Окно 3: Ввод сообщения для рассылки ---
    Window(
        Const("Введите сообщение для рассылки. Можно отправить текст, фото, видео или стикер."),
        MessageInput(on_broadcast_received, content_types=[ContentType.ANY]),
        Back(Const("◀️ Назад")),
        state=Admin.broadcast
    )
)