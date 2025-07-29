import asyncio
import random
from datetime import datetime, time, timedelta

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, ContentType
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Back, Button, SwitchTo
from aiogram_dialog.widgets.text import Const, Format

from bot.tasks import copy_message_task
from bot.scheduler import morning_summary_broadcast, evening_broadcast
from bot.text_formatters import generate_reminder_text
from core.manager import TimetableManager
from core.metrics import TASKS_SENT_TO_QUEUE
from core.user_data import UserDataManager

from .states import Admin
from .constants import WidgetIds

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

async def on_test_reminders_for_week(callback: CallbackQuery, button: Button, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    
    await callback.answer("🚀 Начинаю тест планировщика напоминаний...")
    
    test_users = await user_data_manager.get_users_for_lesson_reminders()
    if not test_users:
        await bot.send_message(admin_id, "❌ Не найдено ни одного пользователя с подпиской на напоминания для теста.")
        return
        
    test_user_id, test_group_name = random.choice(test_users)
    await bot.send_message(admin_id, f"ℹ️ Тестирую логику для случайного пользователя: <code>{test_user_id}</code> (группа <code>{test_group_name}</code>)")

    for i in range(7):
        test_date = date.today() + timedelta(days=i)
        await bot.send_message(admin_id, f"--- 🗓️ <b>Тест для даты: {test_date.strftime('%A, %d.%m.%Y')}</b> ---")
        schedule_info = timetable_manager.get_schedule_for_day(test_group_name, target_date=test_date)
        
        if not (schedule_info and not schedule_info.get('error') and schedule_info.get('lessons')):
            await bot.send_message(admin_id, "<i>Нет пар — нет напоминаний. ✅</i>")
        else:
            try:
                lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
                
                first_lesson = lessons[0]
                first_reminder_text = generate_reminder_text(first_lesson, "first", None)
                if first_reminder_text:
                    start_time = datetime.strptime(first_lesson['start_time_raw'], '%H:%M').time()
                    reminder_time = (datetime.combine(test_date, start_time) - timedelta(minutes=20)).strftime('%H:%M')
                    await bot.send_message(admin_id, f"<b>[ТЕСТ НАПОМИНАНИЯ в {reminder_time}]</b>\n\n{first_reminder_text}")
                    await asyncio.sleep(0.5)

                for j, lesson in enumerate(lessons):
                    is_last = (j == len(lessons) - 1)
                    reminder_type = "final" if is_last else "break"
                    next_lesson = lessons[j+1] if not is_last else None
                    break_duration = None
                    if next_lesson:
                        end_time = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                        next_start_time = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
                        break_duration = int((datetime.combine(date.min, next_start_time) - datetime.combine(date.min, end_time)).total_seconds() / 60)
                    
                    reminder_text = generate_reminder_text(next_lesson, reminder_type, break_duration)
                    if reminder_text:
                        await bot.send_message(admin_id, f"<b>[ТЕСТ НАПОМИНАНИЯ в {lesson['end_time_raw']}]</b>\n\n{reminder_text}")
                        await asyncio.sleep(0.5)

            except (ValueError, KeyError) as e:
                await bot.send_message(admin_id, f"⚠️ Ошибка обработки расписания: {e}")
    
    await bot.send_message(admin_id, "✅ <b>Тестирование планировщика напоминаний завершено.</b>")

async def get_stats_data(user_data_manager: UserDataManager, **kwargs):
    total = await user_data_manager.get_total_users_count()
    new_today = await user_data_manager.get_new_users_count(days=1)
    new_week = await user_data_manager.get_new_users_count(days=7)
    active_day = await user_data_manager.get_active_users_by_period(days=1)
    active_week = await user_data_manager.get_active_users_by_period(days=7)
    active_month = await user_data_manager.get_active_users_by_period(days=30)
    subscribed = await user_data_manager.get_subscribed_users_count()
    top_groups = await user_data_manager.get_top_groups(limit=5)
    top_groups_text = "\n".join([f"  - {g or 'Не указана'}: {c}" for g, c in top_groups])
    
    return {"stats_text": (
        f"📊 <b>Статистика бота:</b>\n\n"
        f"<b>Регистрации:</b>\n"
        f"👤 Всего: <b>{total}</b> | Сегодня: <b>{new_today}</b> | Неделя: <b>{new_week}</b>\n\n"
        f"<b>Активность (DAU/WAU/MAU):</b>\n"
        f"🔥 <b>{active_day}</b> / <b>{active_week}</b> / <b>{active_month}</b>\n\n"
        f"<b>Вовлеченность:</b>\n"
        f"🔔 С подписками: <b>{subscribed}</b>\n\n"
        f"🏆 <b>Топ-5 групп:</b>\n{top_groups_text}"
    )}

async def on_broadcast_received(message: Message, message_input: MessageInput, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    admin_id = message.from_user.id
    try:
        users = await user_data_manager.get_all_user_ids()
        await bot.send_message(admin_id, f"🚀 Начинаю постановку задач на рассылку для {len(users)} пользователей...")
        
        count = 0
        for user_id in users:
            copy_message_task.send(user_id, message.chat.id, message.message_id)
            TASKS_SENT_TO_QUEUE.labels(actor_name='copy_message_task').inc()
            count += 1
        
        await bot.send_message(admin_id, f"✅ Задачи рассылки поставлены в очередь!\n👍 Поставлено: {count}")
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Ошибка при подготовке рассылки: {e}")
    await manager.done()

admin_dialog = Dialog(
    Window(
        Const("👑 <b>Админ-панель</b>\n\nВыберите действие:"),
        SwitchTo(Const("📊 Статистика"), id=WidgetIds.STATS, state=Admin.stats),
        SwitchTo(Const("📣 Сделать рассылку"), id=WidgetIds.BROADCAST, state=Admin.broadcast),
        Button(Const("⚙️ Тест утренней рассылки"), id=WidgetIds.TEST_MORNING, on_click=on_test_morning),
        Button(Const("⚙️ Тест вечерней рассылки"), id=WidgetIds.TEST_EVENING, on_click=on_test_evening),
        Button(Const("🧪 Тест напоминаний о парах"), id=WidgetIds.TEST_REMINDERS, on_click=on_test_reminders_for_week),
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