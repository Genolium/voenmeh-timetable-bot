import asyncio
import random
from datetime import datetime, time, timedelta, date

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, ContentType
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import MessageInput, TextInput
from aiogram_dialog.widgets.kbd import Back, Button, Select, Row, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

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
        
    test_user_id, test_group_name, _ = random.choice(test_users)
    await bot.send_message(admin_id, f"ℹ️ Тестирую логику для случайного пользователя: <code>{test_user_id}</code> (группа <code>{test_group_name}</code>)")

    for i in range(7):
        test_date = date.today() + timedelta(days=i)
        await bot.send_message(admin_id, f"--- 🗓️ <b>Тест для даты: {test_date.strftime('%A, %d.%m.%Y')}</b> ---")
        schedule_info = timetable_manager.get_schedule_for_day(test_group_name, target_date=test_date)
        
        if not (schedule_info and not schedule_info.get('error') and schedule_info.get('lessons')):
            await bot.send_message(admin_id, "<i>Нет пар — нет напоминаний. ✅</i>")
        else:
            try:
                pass
            except Exception as e:
                await bot.send_message(admin_id, f"⚠️ Ошибка обработки расписания: {e}")
    
    await bot.send_message(admin_id, "✅ <b>Тестирование планировщика напоминаний завершено.</b>")

async def on_period_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str):
    """Обновляет период в `dialog_data` при нажатии на кнопку."""
    manager.dialog_data['stats_period'] = int(item_id)

async def get_stats_data(dialog_manager: DialogManager, **kwargs):
    """Собирает и форматирует все данные для дашборда статистики."""
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    period = dialog_manager.dialog_data.get('stats_period', 7)
    
    total_users, dau, wau, mau, subscribed_total, unsubscribed_total, subs_breakdown, top_groups, group_dist = await asyncio.gather(
        user_data_manager.get_total_users_count(),
        user_data_manager.get_active_users_by_period(days=1),
        user_data_manager.get_active_users_by_period(days=7),
        user_data_manager.get_active_users_by_period(days=30),
        user_data_manager.get_subscribed_users_count(),
        user_data_manager.get_unsubscribed_count(),
        user_data_manager.get_subscription_breakdown(),
        user_data_manager.get_top_groups(limit=5),
        user_data_manager.get_group_distribution()
    )
    new_users = await user_data_manager.get_new_users_count(days=period)
    active_users = await user_data_manager.get_active_users_by_period(days=period)

    period_map = {1: "День", 7: "Неделя", 30: "Месяц"}
    
    top_groups_text = "\n".join([f"  - {g or 'Не указана'}: {c}" for g, c in top_groups])
    subs_breakdown_text = (
        f"  - Вечер: {subs_breakdown.get('evening', 0)}\n"
        f"  - Утро: {subs_breakdown.get('morning', 0)}\n"
        f"  - Пары: {subs_breakdown.get('reminders', 0)}"
    )
    group_dist_text = "\n".join([f"  - {category}: {count}" for category, count in group_dist.items()])

    stats_text = (
        f"📊 <b>Статистика бота</b> (Период: <b>{period_map.get(period, '')}</b>)\n\n"
        f"👤 <b>Общая картина</b>\n"
        f"  - Всего пользователей: <b>{total_users}</b>\n"
        f"  - Новых за период: <b>{new_users}</b>\n\n"
        f"🏃‍♂️ <b>Активность</b>\n"
        f"  - Активных за период: <b>{active_users}</b>\n"
        f"  - DAU / WAU / MAU: <b>{dau} / {wau} / {mau}</b>\n\n"
        f"🔔 <b>Вовлеченность</b>\n"
        f"  - С подписками: <b>{subscribed_total}</b>\n"
        f"  - Отписались от всего: <b>{unsubscribed_total}</b>\n"
        f"  <u>Разбивка по подпискам:</u>\n{subs_breakdown_text}\n\n"
        f"🎓 <b>Группы</b>\n"
        f"  <u>Топ-5 групп:</u>\n{top_groups_text}\n"
        f"  <u>Распределение по размеру:</u>\n{group_dist_text}"
    )
    
    return {
        "stats_text": stats_text,
        "period": period,
        "periods": [("День", 1), ("Неделя", 7), ("Месяц", 30)]
    }

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

async def on_user_id_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ ID пользователя должно быть числом.")
        return

    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_info = await user_data_manager.get_full_user_info(user_id)

    if not user_info:
        await message.answer(f"❌ Пользователь с ID <code>{user_id}</code> не найден в базе.")
        return
    
    manager.dialog_data['found_user_info'] = {
        'user_id': user_info.user_id,
        'username': user_info.username,
        'group': user_info.group,
        'reg_date': user_info.registration_date.strftime('%Y-%m-%d %H:%M'),
        'last_active': user_info.last_active_date.strftime('%Y-%m-%d %H:%M'),
    }
    await manager.switch_to(Admin.user_manage)

async def on_new_group_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
    new_group = message.text.upper()
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")

    if new_group not in timetable_manager._schedules:
        await message.answer(f"❌ Группа <b>{new_group}</b> не найдена в расписании.")
        return
    
    user_id = manager.dialog_data['found_user_info']['user_id']
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    
    await user_data_manager.set_user_group(user_id, new_group)
    await message.answer(f"✅ Группа для пользователя <code>{user_id}</code> успешно изменена на <b>{new_group}</b>.")
    
    manager.dialog_data['found_user_info']['group'] = new_group
    await manager.switch_to(Admin.user_manage)

async def get_user_manage_data(dialog_manager: DialogManager, **kwargs):
    user_info = dialog_manager.dialog_data.get('found_user_info', {})
    if not user_info:
        return {}
    
    return {
        "user_info_text": (
            f"👤 <b>Пользователь:</b> <code>{user_info.get('user_id')}</code> (@{user_info.get('username')})\n"
            f"🎓 <b>Группа:</b> {user_info.get('group') or 'Не установлена'}\n"
            f"📅 <b>Регистрация:</b> {user_info.get('reg_date')}\n"
            f"🏃‍♂️ <b>Последняя активность:</b> {user_info.get('last_active')}"
        )
    }

admin_dialog = Dialog(
    Window(
        Const("👑 <b>Админ-панель</b>\n\nВыберите действие:"),
        SwitchTo(Const("📊 Статистика"), id=WidgetIds.STATS, state=Admin.stats),
        SwitchTo(Const("👤 Управление пользователем"), id="manage_user", state=Admin.enter_user_id),
        SwitchTo(Const("📣 Сделать рассылку"), id=WidgetIds.BROADCAST, state=Admin.broadcast),
        Button(Const("⚙️ Тест утренней рассылки"), id=WidgetIds.TEST_MORNING, on_click=on_test_morning),
        Button(Const("⚙️ Тест вечерней рассылки"), id=WidgetIds.TEST_EVENING, on_click=on_test_evening),
        Button(Const("🧪 Тест напоминаний о парах"), id=WidgetIds.TEST_REMINDERS, on_click=on_test_reminders_for_week),
        state=Admin.menu
    ),
    Window(
        Format("{stats_text}"),
        Row(
            Select(
                Jinja(
                    "{% if item[1] == period %}"
                    "🔘 {{ item[0] }}"
                    "{% else %}"
                    "⚪️ {{ item[0] }}"
                    "{% endif %}"
                ),
                id="select_stats_period",
                item_id_getter=lambda item: str(item[1]),
                items="periods",
                on_click=on_period_selected
            )
        ),
        Back(Const("◀️ Назад")),
        getter=get_stats_data,
        state=Admin.stats,
        parse_mode="HTML"
    ),
    Window(
        Const("Введите сообщение для рассылки. Можно отправить текст, фото, видео или стикер."),
        MessageInput(on_broadcast_received, content_types=[ContentType.ANY]),
        Back(Const("◀️ Назад")),
        state=Admin.broadcast
    ),
    Window(
        Const("Введите ID пользователя для управления:"),
        TextInput(id="input_user_id", on_success=on_user_id_input),
        Back(Const("◀️ Назад")),
        state=Admin.enter_user_id
    ),
    Window(
        Format("{user_info_text}"),
        SwitchTo(Const("🔄 Сменить группу"), id="change_group", state=Admin.change_group_confirm),
        SwitchTo(Const("◀️ К меню"), id="back_to_admin_menu", state=Admin.menu),
        state=Admin.user_manage,
        getter=get_user_manage_data,
        parse_mode="HTML"
    ),
    Window(
        Const("Введите новый номер группы для пользователя:"),
        TextInput(id="input_new_group", on_success=on_new_group_input),
        Back(Const("◀️ Назад")),
        state=Admin.change_group_confirm
    )
)