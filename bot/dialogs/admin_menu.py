import asyncio
import random
from datetime import datetime, time, timedelta, date
import os
from pathlib import Path

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, ContentType
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import MessageInput, TextInput
from aiogram_dialog.widgets.kbd import Back, Button, Select, Row, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

from bot.tasks import copy_message_task, send_message_task
from bot.scheduler import morning_summary_broadcast, evening_broadcast
from bot.text_formatters import generate_reminder_text
from core.manager import TimetableManager
from core.metrics import TASKS_SENT_TO_QUEUE
from core.user_data import UserDataManager
from core.semester_settings import SemesterSettingsManager
from bot.dialogs.schedule_view import cleanup_old_cache, get_cache_info
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
from core.config import MEDIA_PATH

from .states import Admin
from .constants import WidgetIds

async def on_test_morning(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    await callback.answer("🚀 Запускаю постановку задач на утреннюю рассылку...")
    await morning_summary_broadcast(user_data_manager, timetable_manager)
    await callback.message.answer("✅ Задачи для утренней рассылки поставлены в очередь.")

async def on_test_evening(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    await callback.answer("🚀 Запускаю постановку задач на вечернюю рассылку...")
    await evening_broadcast(user_data_manager, timetable_manager)
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
        schedule_info = await timetable_manager.get_schedule_for_day(test_group_name, target_date=test_date)
        
        if not (schedule_info and not schedule_info.get('error') and schedule_info.get('lessons')):
            await bot.send_message(admin_id, "<i>Нет пар — нет напоминаний. ✅</i>")
        else:
            try:
                pass
            except Exception as e:
                await bot.send_message(admin_id, f"⚠️ Ошибка обработки расписания: {e}")
    
    await bot.send_message(admin_id, "✅ <b>Тестирование планировщика напоминаний завершено.</b>")

async def on_test_alert(callback: CallbackQuery, button: Button, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    await callback.answer("🧪 Отправляю тестовый алёрт...")
    text = (
        "ALERTMANAGER: FIRING (1 alert)\n\n"
        "⚠️ ScheduleStale [critical]\n"
        "No update > 1h\n"
        "source=scheduler\n"
        "startsAt=now"
    )
    await bot.send_message(admin_id, text)

async def on_generate_full_schedule(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Запуск генерации полного расписания для всех групп."""
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    redis_client = manager.middleware_data.get("redis_client")
    
    await callback.answer("🔄 Запускаю генерацию полного расписания...")
    await bot.send_message(admin_id, "🔄 Начинаю генерацию полного расписания для всех групп. Это может занять несколько минут...")
    
    try:
        # Импортируем функцию из scheduler
        from bot.scheduler import generate_full_schedule_images
        await generate_full_schedule_images(user_data_manager, timetable_manager, redis_client)
        await bot.send_message(admin_id, "✅ Генерация полного расписания завершена успешно!")
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Ошибка при генерации полного расписания: {e}")
        import traceback
        await bot.send_message(admin_id, f"🔍 Детали ошибки:\n<code>{traceback.format_exc()}</code>")

async def on_check_graduated_groups(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Запуск проверки выпустившихся групп."""
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    redis_client = manager.middleware_data.get("redis_client")
    
    await callback.answer("🔍 Запускаю проверку выпустившихся групп...")
    await bot.send_message(admin_id, "🔍 Начинаю проверку выпустившихся групп...")
    
    try:
        # Импортируем функцию из scheduler
        from bot.scheduler import handle_graduated_groups
        await handle_graduated_groups(user_data_manager, timetable_manager, redis_client)
        await bot.send_message(admin_id, "✅ Проверка выпустившихся групп завершена!")
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Ошибка при проверке выпустившихся групп: {e}")
        import traceback
        await bot.send_message(admin_id, f"🔍 Детали ошибки:\n<code>{traceback.format_exc()}</code>")

async def on_semester_settings(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переход к настройкам семестров."""
    await manager.switch_to(Admin.semester_settings)

async def on_edit_fall_semester(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переход к редактированию даты осеннего семестра."""
    await manager.switch_to(Admin.edit_fall_semester)

async def on_edit_spring_semester(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переход к редактированию даты весеннего семестра."""
    await manager.switch_to(Admin.edit_spring_semester)

async def on_fall_semester_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
    """Обработка ввода даты осеннего семестра."""
    try:
        from datetime import datetime
        # Парсим дату в формате DD.MM.YYYY
        date_obj = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
        
        # Получаем менеджер настроек
        session_factory = manager.middleware_data.get("session_factory")
        settings_manager = SemesterSettingsManager(session_factory)
        
        # Получаем текущие настройки
        current_settings = await settings_manager.get_semester_settings()
        spring_start = current_settings[1] if current_settings else date(2025, 2, 9)
        
        # Обновляем настройки
        success = await settings_manager.update_semester_settings(
            date_obj, spring_start, message.from_user.id
        )
        
        if success:
            await message.answer("✅ Дата начала осеннего семестра успешно обновлена!")
        else:
            await message.answer("❌ Ошибка при обновлении настроек.")
            
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 01.09.2024)")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    
    await manager.switch_to(Admin.semester_settings)

async def on_spring_semester_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
    """Обработка ввода даты весеннего семестра."""
    try:
        from datetime import datetime
        # Парсим дату в формате DD.MM.YYYY
        date_obj = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
        
        # Получаем менеджер настроек
        session_factory = manager.middleware_data.get("session_factory")
        settings_manager = SemesterSettingsManager(session_factory)
        
        # Получаем текущие настройки
        current_settings = await settings_manager.get_semester_settings()
        fall_start = current_settings[0] if current_settings else date(2024, 9, 1)
        
        # Обновляем настройки
        success = await settings_manager.update_semester_settings(
            fall_start, date_obj, message.from_user.id
        )
        
        if success:
            await message.answer("✅ Дата начала весеннего семестра успешно обновлена!")
        else:
            await message.answer("❌ Ошибка при обновлении настроек.")
            
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 09.02.2025)")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    
    await manager.switch_to(Admin.semester_settings)

# --- Сегментированная рассылка с шаблонами ---
async def build_segment_users(user_data_manager: UserDataManager, group_prefix: str | None, days_active: int | None):
    group_prefix_up = (group_prefix or "").upper().strip()
    all_ids = await user_data_manager.get_all_user_ids()
    selected_ids: list[int] = []
    from datetime import timezone
    from datetime import datetime as dt
    threshold = None
    if days_active and days_active > 0:
        threshold = dt.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_active)
    for uid in all_ids:
        info = await user_data_manager.get_full_user_info(uid)
        if not info:
            continue
        if group_prefix_up and not (info.group or "").upper().startswith(group_prefix_up):
            continue
        if threshold and (not info.last_active_date or info.last_active_date < threshold):
            continue
        selected_ids.append(uid)
    return selected_ids

def render_template(template_text: str, user_info) -> str:
    placeholders = {
        "user_id": str(user_info.user_id),
        "username": user_info.username or "N/A",
        "group": user_info.group or "N/A",
        "last_active": user_info.last_active_date.strftime("%d.%m.%Y") if user_info.last_active_date else "N/A",
    }
    text = template_text
    for key, value in placeholders.items():
        text = text.replace(f"{{{key}}}", value)
    return text

async def on_segment_criteria_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
    dialog_data = manager.dialog_data
    # ожидаем ввод в формате: PREFIX|DAYS (например: О7|7). Пусто для всех
    raw = (message.text or "").strip()
    if "|" in raw:
        prefix, days_str = raw.split("|", 1)
        days = None
        try:
            days = int(days_str) if days_str.strip() else None
        except ValueError:
            days = None
        dialog_data['segment_group_prefix'] = prefix.strip()
        dialog_data['segment_days_active'] = days
    else:
        dialog_data['segment_group_prefix'] = raw
        dialog_data['segment_days_active'] = None
    await manager.switch_to(Admin.template_input)

async def on_template_input_message(message: Message, message_input: MessageInput, manager: DialogManager):
    manager.dialog_data['segment_template'] = message.text or ""
    await manager.switch_to(Admin.preview)

async def get_preview_data(dialog_manager: DialogManager, **kwargs):
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    prefix = dialog_manager.dialog_data.get('segment_group_prefix')
    days_active = dialog_manager.dialog_data.get('segment_days_active')
    template = dialog_manager.dialog_data.get('segment_template', "")
    users = await build_segment_users(user_data_manager, prefix, days_active)
    preview_text = ""
    if users:
        info = await user_data_manager.get_full_user_info(users[0])
        preview_text = render_template(template, info)
    dialog_manager.dialog_data['segment_selected_ids'] = users
    return {
        "preview_text": preview_text or "(не удалось сформировать превью)",
        "selected_count": len(users)
    }

async def on_confirm_segment_send(callback: CallbackQuery, button: Button, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    udm: UserDataManager = manager.middleware_data.get("user_data_manager")
    template = manager.dialog_data.get('segment_template', "")
    user_ids = manager.dialog_data.get('segment_selected_ids', [])
    await callback.answer("🚀 Рассылка по сегменту поставлена в очередь...")
    count = 0
    for uid in user_ids:
        info = await udm.get_full_user_info(uid)
        if not info:
            continue
        text = render_template(template, info)
        send_message_task.send(uid, text)
        TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()
        count += 1
    await bot.send_message(admin_id, f"✅ Отправка по сегменту запущена. Поставлено задач: {count}")
    await manager.switch_to(Admin.menu)

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

async def on_broadcast_received(message: Message, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = message.from_user.id
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")

    if message.content_type == ContentType.TEXT:
        template = message.text
        # Получаем всех пользователей
        all_users = await user_data_manager.get_all_user_ids()
        await message.reply("🚀 Рассылка поставлена в очередь...")

        sent_count = 0
        for user_id in all_users:
            user_info = await user_data_manager.get_full_user_info(user_id)
            if not user_info:
                continue
            text = render_template(template, user_info)
            send_message_task.send(user_id, text)
            TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()
            sent_count += 1

        await bot.send_message(admin_id, f"✅ Рассылка завершена. Поставлено задач: {sent_count}")
    else:
        # Обработка медиа: Ставим задачи на копирование сообщения всем пользователям
        try:
            all_users = await user_data_manager.get_all_user_ids()
            await message.reply(f"🚀 Начинаю постановку задач на медиа-рассылку для {len(all_users)} пользователей...")

            count = 0
            for user_id in all_users:
                copy_message_task.send(user_id, message.chat.id, message.message_id)
                TASKS_SENT_TO_QUEUE.labels(actor_name='copy_message_task').inc()
                count += 1

            await bot.send_message(admin_id, f"✅ Медиа-рассылка завершена! Поставлено задач: {count}")
        except Exception as e:
            await bot.send_message(admin_id, f"❌ Ошибка при медиа-рассылке: {e}")
            # Assuming ERRORS_TOTAL is defined elsewhere or needs to be imported
            # ERRORS_TOTAL.labels(source='admin_broadcast').inc()

        await manager.switch_to(Admin.menu)

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

async def get_semester_settings_data(dialog_manager: DialogManager, **kwargs):
    """Получает данные для окна настроек семестров."""
    session_factory = dialog_manager.middleware_data.get("session_factory")
    if not session_factory:
        return {"semester_settings_text": "❌ Ошибка: не удалось подключиться к базе данных."}
    
    settings_manager = SemesterSettingsManager(session_factory)
    settings_text = await settings_manager.get_formatted_settings()
    
    return {"semester_settings_text": settings_text}

async def on_clear_cache(callback: CallbackQuery, button: Button, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    
    await callback.answer("🧹 Очищаю кэш картинок...")
    
    # Получаем информацию о кэше до очистки
    cache_info_before = await get_cache_info()
    
    # Очищаем кэш
    await cleanup_old_cache()
    
    # Получаем информацию о кэше после очистки
    cache_info_after = await get_cache_info()
    
    if "error" in cache_info_before or "error" in cache_info_after:
        await bot.send_message(admin_id, "❌ Ошибка при работе с кэшем")
        return
    
    freed_space = cache_info_before["total_size_mb"] - cache_info_after["total_size_mb"]
    freed_files = cache_info_before["total_files"] - cache_info_after["total_files"]
    
    # Формируем список файлов для отображения
    files_before = cache_info_before.get('files', [])
    files_after = cache_info_after.get('files', [])
    
    files_text_before = "\n".join([f"   • {f}" for f in files_before]) if files_before else "   • Нет файлов"
    files_text_after = "\n".join([f"   • {f}" for f in files_after]) if files_after else "   • Нет файлов"
    
    message = (
        f"✅ <b>Кэш очищен!</b>\n\n"
        f"📊 <b>До очистки:</b>\n"
        f"   • Файлов: {cache_info_before['total_files']}\n"
        f"   • Размер: {cache_info_before['total_size_mb']} MB\n"
        f"   • Файлы:\n{files_text_before}\n\n"
        f"🧹 <b>После очистки:</b>\n"
        f"   • Файлов: {cache_info_after['total_files']}\n"
        f"   • Размер: {cache_info_after['total_size_mb']} MB\n"
        f"   • Файлы:\n{files_text_after}\n\n"
        f"💾 <b>Освобождено:</b>\n"
        f"   • Файлов: {freed_files}\n"
        f"   • Места: {freed_space} MB"
    )
    
    await bot.send_message(admin_id, message, parse_mode="HTML")

# Глобальная переменная для отслеживания активных генераций
active_generations = {}

async def on_generate_all_images(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Запускает генерацию всех изображений в фоновом режиме."""
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    
    # Проверяем, не запущена ли уже генерация
    if admin_id in active_generations:
        await callback.answer("⚠️ Генерация уже запущена! Дождитесь завершения.")
        return
    
    await callback.answer("🚀 Запускаю генерацию всех изображений в фоне...")
    
    # Отправляем начальное сообщение с кнопкой отмены
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить генерацию", callback_data="cancel_generation")]
    ])
    
    status_msg = await bot.send_message(
        admin_id, 
        "🎨 <b>Генерация всех изображений</b>\n\n"
        "⏳ Подготовка к генерации...\n"
        "📊 Прогресс: 0%\n"
        "✅ Сгенерировано: 0\n"
        "❌ Ошибок: 0\n"
        "⏱️ Время: 0с",
        parse_mode="HTML",
        reply_markup=cancel_kb
    )
    
    # Отмечаем генерацию как активную
    active_generations[admin_id] = {
        "status_msg_id": status_msg.message_id,
        "cancelled": False,
        "start_time": None
    }
    
    # Запускаем генерацию в фоне
    asyncio.create_task(
        generate_all_images_background(
            bot=bot,
            admin_id=admin_id,
            status_msg_id=status_msg.message_id,
            timetable_manager=timetable_manager
        )
    )

async def on_cancel_generation(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Отменяет генерацию изображений."""
    admin_id = callback.from_user.id
    
    if admin_id in active_generations:
        active_generations[admin_id]["cancelled"] = True
        await callback.answer("⏹️ Отмена генерации...")
        
        # Обновляем сообщение
        bot: Bot = manager.middleware_data.get("bot")
        status_msg_id = active_generations[admin_id]["status_msg_id"]
        
        try:
            await bot.edit_message_text(
                "⏹️ <b>Генерация отменена</b>\n\n"
                "Процесс остановлен пользователем.",
                chat_id=admin_id,
                message_id=status_msg_id,
                parse_mode="HTML"
            )
        except:
            pass
        
        # Удаляем из активных генераций
        del active_generations[admin_id]
    else:
        await callback.answer("❌ Нет активной генерации для отмены")

async def generate_all_images_background(
    bot: Bot,
    admin_id: int,
    status_msg_id: int,
    timetable_manager: TimetableManager
):
    """Генерирует все изображения в фоновом режиме с прогресс-баром."""
    import time
    from datetime import datetime
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    start_time = time.time()
    generated_count = 0
    error_count = 0
    total_tasks = 0
    completed_tasks = 0
    
    # Обновляем время начала
    if admin_id in active_generations:
        active_generations[admin_id]["start_time"] = start_time
    
    try:
        # Получаем все уникальные группы из расписания
        all_groups = list(timetable_manager._schedules.keys())
        all_groups = [g for g in all_groups if not g.startswith('__')]  # Исключаем служебные ключи
        
        # Подсчитываем общее количество задач
        week_types = [
            ("Нечётная неделя", "odd"),
            ("Чётная неделя", "even")
        ]
        
        tasks = []
        for group in all_groups:
            for week_name, week_key in week_types:
                # Проверяем, есть ли расписание для этой группы и недели
                group_schedule = timetable_manager._schedules.get(group.upper(), {})
                week_schedule = group_schedule.get(week_key, {})
                
                if week_schedule:  # Только если есть расписание
                    tasks.append((group, week_schedule, week_name, week_key))
        
        total_tasks = len(tasks)
        
        if total_tasks == 0:
            await bot.edit_message_text(
                "❌ <b>Нет данных для генерации</b>\n\n"
                "Расписания не найдены или пусты.",
                chat_id=admin_id,
                message_id=status_msg_id,
                parse_mode="HTML"
            )
            if admin_id in active_generations:
                del active_generations[admin_id]
            return
        
        # Обновляем статус с количеством задач
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить генерацию", callback_data="cancel_generation")]
        ])
        
        await bot.edit_message_text(
            f"🎨 <b>Генерация всех изображений</b>\n\n"
            f"📊 Всего задач: {total_tasks}\n"
            f"📁 Групп: {len(all_groups)}\n"
            f"⏳ Начинаю генерацию...\n"
            f"📊 Прогресс: 0%\n"
            f"✅ Сгенерировано: 0\n"
            f"❌ Ошибок: 0\n"
            f"⏱️ Время: 0с",
            chat_id=admin_id,
            message_id=status_msg_id,
            parse_mode="HTML",
            reply_markup=cancel_kb
        )
        
        # Создаем директорию для результатов
        output_dir = MEDIA_PATH / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Генерируем изображения
        for i, (group, week_schedule, week_name, week_key) in enumerate(tasks):
            # Проверяем отмену
            if admin_id in active_generations and active_generations[admin_id]["cancelled"]:
                await bot.edit_message_text(
                    "⏹️ <b>Генерация отменена</b>\n\n"
                    f"✅ Сгенерировано: {generated_count}\n"
                    f"❌ Ошибок: {error_count}\n"
                    f"⏱️ Время: {time.time() - start_time:.1f}с",
                    chat_id=admin_id,
                    message_id=status_msg_id,
                    parse_mode="HTML"
                )
                del active_generations[admin_id]
                return
            
            try:
                # Проверяем кэш
                cache_key = f"{group}_{week_key}"
                cache_manager = ImageCacheManager(timetable_manager.redis, cache_ttl_hours=720)
                
                if await cache_manager.is_cached(cache_key):
                    completed_tasks += 1
                    continue
                
                # Генерируем изображение
                output_filename = f"{group}_{week_key}.png"
                output_path = output_dir / output_filename
                
                success = await generate_schedule_image(
                    schedule_data=week_schedule,
                    week_type=week_name,
                    group=group,
                    output_path=str(output_path)
                )
                
                if success and os.path.exists(output_path):
                    # Сохраняем в кэш
                    try:
                        with open(output_path, 'rb') as f:
                            image_bytes = f.read()
                        await cache_manager.cache_image(cache_key, image_bytes, metadata={
                            "group": group,
                            "week_key": week_key,
                            "generated_at": datetime.now().isoformat()
                        })
                        generated_count += 1
                    except Exception as e:
                        error_count += 1
                        print(f"Ошибка кэширования {cache_key}: {e}")
                else:
                    error_count += 1
                    
            except Exception as e:
                error_count += 1
                print(f"Ошибка генерации {group}_{week_key}: {e}")
            
            completed_tasks += 1
            
            # Обновляем статус каждые 5 задач или каждые 10 секунд
            if completed_tasks % 5 == 0 or completed_tasks == total_tasks:
                elapsed_time = time.time() - start_time
                progress_percent = int((completed_tasks / total_tasks) * 100)
                
                # Создаем прогресс-бар
                bar_length = 20
                filled_length = int(bar_length * completed_tasks // total_tasks)
                progress_bar = '█' * filled_length + '░' * (bar_length - filled_length)
                
                status_text = (
                    f"🎨 <b>Генерация всех изображений</b>\n\n"
                    f"📊 Всего задач: {total_tasks}\n"
                    f"📁 Групп: {len(all_groups)}\n\n"
                    f"⏳ Прогресс: {progress_bar} {progress_percent}%\n"
                    f"✅ Сгенерировано: {generated_count}\n"
                    f"❌ Ошибок: {error_count}\n"
                    f"⏱️ Время: {elapsed_time:.1f}с\n"
                    f"🚀 Скорость: {completed_tasks/elapsed_time:.1f} задач/с" if elapsed_time > 0 else "🚀 Скорость: 0 задач/с"
                )
                
                try:
                    await bot.edit_message_text(
                        status_text,
                        chat_id=admin_id,
                        message_id=status_msg_id,
                        parse_mode="HTML",
                        reply_markup=cancel_kb
                    )
                except Exception as e:
                    print(f"Ошибка обновления статуса: {e}")
        
        # Финальное сообщение
        total_time = time.time() - start_time
        final_text = (
            f"🎉 <b>Генерация завершена!</b>\n\n"
            f"📊 <b>Результаты:</b>\n"
            f"✅ Сгенерировано: {generated_count} изображений\n"
            f"❌ Ошибок: {error_count}\n"
            f"⏱️ Общее время: {total_time:.1f}с\n"
            f"🚀 Средняя скорость: {total_tasks/total_time:.1f} задач/с\n\n"
            f"📁 Изображения сохранены в: <code>bot/media/generated/</code>"
        )
        
        await bot.edit_message_text(
            final_text,
            chat_id=admin_id,
            message_id=status_msg_id,
            parse_mode="HTML"
        )
        
        # Удаляем из активных генераций
        if admin_id in active_generations:
            del active_generations[admin_id]
        
    except Exception as e:
        error_text = (
            f"❌ <b>Критическая ошибка генерации</b>\n\n"
            f"Ошибка: {str(e)}\n"
            f"✅ Сгенерировано: {generated_count}\n"
            f"❌ Ошибок: {error_count}\n"
            f"⏱️ Время: {time.time() - start_time:.1f}с"
        )
        
        try:
            await bot.edit_message_text(
                error_text,
                chat_id=admin_id,
                message_id=status_msg_id,
                parse_mode="HTML"
            )
        except:
            await bot.send_message(admin_id, error_text, parse_mode="HTML")
        
        # Удаляем из активных генераций
        if admin_id in active_generations:
            del active_generations[admin_id]

admin_dialog = Dialog(
    Window(
        Const("👑 <b>Админ-панель</b>\n\nВыберите действие:"),
        SwitchTo(Const("📊 Статистика"), id=WidgetIds.STATS, state=Admin.stats),
        SwitchTo(Const("👤 Управление пользователем"), id="manage_user", state=Admin.enter_user_id),
        SwitchTo(Const("📣 Сделать рассылку"), id=WidgetIds.BROADCAST, state=Admin.broadcast),
        SwitchTo(Const("🎯 Сегментированная рассылка"), id="segmented", state=Admin.segment_menu),
        Button(Const("⚙️ Тест утренней рассылки"), id=WidgetIds.TEST_MORNING, on_click=on_test_morning),
        Button(Const("⚙️ Тест вечерней рассылки"), id=WidgetIds.TEST_EVENING, on_click=on_test_evening),
        Button(Const("🧪 Тест напоминаний о парах"), id=WidgetIds.TEST_REMINDERS, on_click=on_test_reminders_for_week),
        Button(Const("🧪 Тест алёрта"), id="test_alert", on_click=on_test_alert),
        Button(Const("🗑️ Очистить кэш картинок"), id="clear_cache", on_click=on_clear_cache),
        Button(Const("📸 Сгенерировать все изображения"), id="generate_all_images", on_click=on_generate_all_images),
        Button(Const("📸 Сгенерировать полное расписание"), id=WidgetIds.GENERATE_FULL_SCHEDULE, on_click=on_generate_full_schedule),
        Button(Const("👥 Проверить выпустившиеся группы"), id="check_graduated_groups", on_click=on_check_graduated_groups),
        SwitchTo(Const("📅 Настройки семестров"), id="semester_settings", state=Admin.semester_settings),
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
        SwitchTo(Const("◀️ В админ-панель"), id="stats_back", state=Admin.menu),
        getter=get_stats_data,
        state=Admin.stats,
        parse_mode="HTML"
    ),
    Window(
        Const("Введите критерии сегментации в формате PREFIX|DAYS (например: О7|7). Пусто — все."),
        TextInput(id="segment_input", on_success=on_segment_criteria_input),
        SwitchTo(Const("◀️ В админ-панель"), id="segment_back", state=Admin.menu),
        state=Admin.segment_menu
    ),
    Window(
        Const("Введите шаблон сообщения. Доступные плейсхолдеры: {user_id}, {username}, {group}"),
        MessageInput(on_template_input_message, content_types=[ContentType.TEXT]),
        SwitchTo(Const("◀️ Назад"), id="template_back", state=Admin.segment_menu),
        state=Admin.template_input
    ),
    Window(
        Format("Предпросмотр (1-й получатель):\n\n{preview_text}\n\nВсего получателей: {selected_count}"),
        Button(Const("🚀 Отправить"), id="confirm_segment_send", on_click=on_confirm_segment_send),
        SwitchTo(Const("◀️ Назад"), id="preview_back", state=Admin.template_input),
        getter=get_preview_data,
        state=Admin.preview,
        parse_mode="HTML"
    ),
    Window(
        Const("Введите сообщение для рассылки. Можно отправить текст, фото, видео или стикер. Для текста поддерживаются плейсхолдеры: {user_id}, {username}, {group}"),
        MessageInput(on_broadcast_received, content_types=[ContentType.ANY]),
        SwitchTo(Const("◀️ В админ-панель"), id="broadcast_back", state=Admin.menu),
        state=Admin.broadcast
    ),
    Window(
        Const("Введите ID пользователя для управления:"),
        TextInput(id="input_user_id", on_success=on_user_id_input),
        SwitchTo(Const("◀️ В админ-панель"), id="user_id_back", state=Admin.menu),
        state=Admin.enter_user_id
    ),
    Window(
        Format("{user_info_text}"),
        SwitchTo(Const("🔄 Сменить группу"), id="change_group", state=Admin.change_group_confirm),
        SwitchTo(Const("◀️ Новый поиск"), id="back_to_user_search", state=Admin.enter_user_id),
        state=Admin.user_manage,
        getter=get_user_manage_data,
        parse_mode="HTML"
    ),
    Window(
        Const("Введите новый номер группы для пользователя:"),
        TextInput(id="input_new_group", on_success=on_new_group_input),
        SwitchTo(Const("◀️ Назад"), id="change_group_back", state=Admin.user_manage),
        state=Admin.change_group_confirm
    ),
    Window(
        Format("{semester_settings_text}"),
        Button(Const("🍂 Изменить осенний семестр"), id="edit_fall_semester", on_click=on_edit_fall_semester),
        Button(Const("🌸 Изменить весенний семестр"), id="edit_spring_semester", on_click=on_edit_spring_semester),
        SwitchTo(Const("◀️ В админ-панель"), id="semester_back", state=Admin.menu),
        getter=get_semester_settings_data,
        state=Admin.semester_settings,
        parse_mode="HTML"
    ),
    Window(
        Const("Введите дату начала осеннего семестра в формате ДД.ММ.ГГГГ (например, 01.09.2024):"),
        TextInput(id="fall_semester_input", on_success=on_fall_semester_input),
        SwitchTo(Const("◀️ Назад"), id="fall_semester_back", state=Admin.semester_settings),
        state=Admin.edit_fall_semester
    ),
    Window(
        Const("Введите дату начала весеннего семестра в формате ДД.ММ.ГГГГ (например, 09.02.2025):"),
        TextInput(id="spring_semester_input", on_success=on_spring_semester_input),
        SwitchTo(Const("◀️ Назад"), id="spring_semester_back", state=Admin.semester_settings),
        state=Admin.edit_spring_semester
    )
)