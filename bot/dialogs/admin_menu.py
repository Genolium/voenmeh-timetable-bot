from aiogram.types import Message, ContentType, CallbackQuery
from aiogram import Bot 
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.text import Const, Format
from aiogram_dialog.widgets.kbd import Button, SwitchTo, Back

from bot.scheduler import morning_summary_broadcast, evening_broadcast
from bot.tasks import copy_message_task 
from core.user_data import UserDataManager
from core.metrics import TASKS_SENT_TO_QUEUE

from .states import Admin 


async def on_test_morning(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager = manager.middleware_data.get("user_data_manager")
    await callback.answer("🚀 Запускаю постановку задач на утреннюю рассылку...")
    await morning_summary_broadcast(user_data_manager) 
    await callback.message.answer("✅ Задачи для утренней рассылки поставлены в очередь.")


async def on_test_evening(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager = manager.middleware_data.get("user_data_manager")
    await callback.answer("🚀 Запускаю постановку задач на вечернюю рассылку...")
    await evening_broadcast(user_data_manager)
    await callback.message.answer("✅ Задачи для вечерней рассылки поставлены в очередь.")


async def get_stats_data(user_data_manager: UserDataManager, **kwargs):
    total_users = await user_data_manager.get_total_users_count()
    new_today = await user_data_manager.get_new_users_count(days=1)
    new_week = await user_data_manager.get_new_users_count(days=7)
    
    active_day = await user_data_manager.get_active_users_by_period(days=1)
    active_week = await user_data_manager.get_active_users_by_period(days=7)
    active_month = await user_data_manager.get_active_users_by_period(days=30)
    
    subscribed_users = await user_data_manager.get_subscribed_users_count()

    top_groups = await user_data_manager.get_top_groups(limit=5)
    top_groups_text = "\n".join([f"  - {group or 'Не указана'}: {count}" for group, count in top_groups])
    if not top_groups_text:
        top_groups_text = "Нет данных"

    stats_text = (
        f"📊 <b>Статистика бота:</b>\n\n"
        f"<b>Регистрации:</b>\n"
        f"👤 Всего пользователей: <b>{total_users}</b>\n"
        f"📈 Новых за сегодня: <b>{new_today}</b>\n"
        f"📈 Новых за неделю: <b>{new_week}</b>\n\n"
        f"<b>Активность:</b>\n"
        f"🔥 Активных за день: <b>{active_day}</b>\n"
        f"🔥 Активных за неделю: <b>{active_week}</b>\n"
        f"🔥 Активных за месяц: <b>{active_month}</b>\n\n"
        f"<b>Вовлеченность:</b>\n"
        f"🔔 С подписками: <b>{subscribed_users}</b>\n\n"
        f"🏆 <b>Топ-5 групп:</b>\n{top_groups_text}"
    )
    return {"stats_text": stats_text}

async def on_broadcast_received(message: Message, message_input: MessageInput, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot") 
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")

    try:
        all_users = await user_data_manager.get_all_user_ids()
        queued_count, failed_to_queue_count = 0, 0
        
        admin_chat_id = message.from_user.id
        original_chat_id = message.chat.id 
        original_message_id = message.message_id

        await bot.send_message(admin_chat_id, f"🚀 Начинаю постановку задач на рассылку для {len(all_users)} пользователей...")

        for user_id in all_users:
            try:
                copy_message_task.send(user_id, original_chat_id, original_message_id)
                TASKS_SENT_TO_QUEUE.labels(actor_name='copy_message_task').inc() 
                queued_count += 1
            except Exception as e:
                logging.error(f"Failed to queue broadcast message for user {user_id}: {e}")
                failed_to_queue_count += 1
        
        await bot.send_message(
            admin_chat_id,
            f"✅ Задачи рассылки поставлены в очередь!\n"
            f"👍 Поставлено: {queued_count}\n"
            f"👎 Ошибок при постановке: {failed_to_queue_count}\n\n"
            f"Результаты отправки сообщений будут видны в логах worker-а Dramatiq."
        )
    except Exception as e:
        await bot.send_message(admin_chat_id, f"❌ Общая ошибка при подготовке рассылки: {e}")
    
    await manager.done()

admin_dialog = Dialog(
    Window(
        Const("👑 <b>Админ-панель</b>\n\nВыберите действие:"),
        SwitchTo(Const("📊 Статистика"), id="stats", state=Admin.stats), # Теперь Admin.stats будет доступен
        SwitchTo(Const("📣 Сделать рассылку"), id="broadcast", state=Admin.broadcast),
        Button(Const("⚙️ Тест утренней рассылки"), id="test_morning", on_click=on_test_morning),
        Button(Const("⚙️ Тест вечерней рассылки"), id="test_evening", on_click=on_test_evening),
        state=Admin.menu
    ),
    Window(
        Format("{stats_text}"),
        Back(Const("◀️ Назад")),
        getter=get_stats_data,
        state=Admin.stats
    ),
    Window(
        Const("Введите сообщение для рассылки. Можно отправить текст, фото, видео или стикер."),
        MessageInput(on_broadcast_received, content_types=[ContentType.ANY]),
        Back(Const("◀️ Назад")),
        state=Admin.broadcast
    )
)