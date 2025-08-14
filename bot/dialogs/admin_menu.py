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
from bot.scheduler import morning_summary_broadcast, evening_broadcast, generate_full_schedule_images
from bot.text_formatters import generate_reminder_text
from core.manager import TimetableManager
from core.metrics import TASKS_SENT_TO_QUEUE
from core.user_data import UserDataManager
from core.semester_settings import SemesterSettingsManager
from bot.dialogs.schedule_view import cleanup_old_cache, get_cache_info

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
    
    # Создаем правильный Redis-клиент
    from redis.asyncio import Redis
    import os
    redis_url = os.getenv("REDIS_URL")
    redis_password = os.getenv("REDIS_PASSWORD")
    redis_client = Redis.from_url(redis_url, password=redis_password, decode_responses=False)
    
    # Проверяем, не запущена ли уже генерация
    if admin_id in active_generations:
        await callback.answer("⚠️ Генерация уже запущена! Дождитесь завершения.")
        return
    
    await callback.answer("🚀 Запускаю генерацию полного расписания в фоне...")
    
    # Отправляем начальное сообщение с кнопкой отмены
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить генерацию", callback_data="cancel_generation")]
    ])
    
    status_msg = await bot.send_message(
        admin_id, 
        "🎨 <b>Генерация полного расписания</b>\n\n"
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
    
    # Запускаем генерацию через воркеры
    asyncio.create_task(
        generate_full_schedule_images(
            user_data_manager=user_data_manager,
            timetable_manager=timetable_manager,
            redis_client=redis_client,
            admin_id=admin_id,
            bot=bot
        )
    )

async def on_check_graduated_groups(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Запуск проверки выпустившихся групп."""
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    
    # Создаем правильный Redis-клиент
    from redis.asyncio import Redis
    import os
    redis_url = os.getenv("REDIS_URL")
    redis_password = os.getenv("REDIS_PASSWORD")
    redis_client = Redis.from_url(redis_url, password=redis_password, decode_responses=False)
    
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
    
    # Формируем список файлов для отображения (ограничиваем до 5 файлов)
    files_before = cache_info_before.get('files', [])
    files_after = cache_info_after.get('files', [])
    
    # Показываем только первые 5 файлов
    files_to_show = files_before[:5]
    files_text_before = "\n".join([f"   • {f}" for f in files_to_show]) if files_to_show else "   • Нет файлов"
    if len(files_before) > 5:
        files_text_before += f"\n   ... и еще {len(files_before) - 5} файлов"
    
    files_text_after = "\n".join([f"   • {f}" for f in files_after]) if files_after else "   • Нет файлов"
    
    # Добавляем информацию о Redis кэше
    redis_before = cache_info_before.get('redis_keys', 0)
    redis_after = cache_info_after.get('redis_keys', 0)
    redis_freed = redis_before - redis_after
    
    message = (
        f"✅ <b>Кэш очищен!</b>\n\n"
        f"📊 <b>До очистки:</b>\n"
        f"   • Файлов: {cache_info_before['total_files']}\n"
        f"   • Размер файлов: {cache_info_before['total_size_mb']} MB\n"
        f"   • Redis ключей: {redis_before}\n"
        f"   • Файлы:\n{files_text_before}\n\n"
        f"🧹 <b>После очистки:</b>\n"
        f"   • Файлов: {cache_info_after['total_files']}\n"
        f"   • Размер файлов: {cache_info_after['total_size_mb']} MB\n"
        f"   • Redis ключей: {redis_after}\n\n"
        f"💾 <b>Освобождено:</b>\n"
        f"   • Файлов: {freed_files}\n"
        f"   • Места: {freed_space} MB\n"
        f"   • Redis ключей: {redis_freed}"
    )
    
    await bot.send_message(admin_id, message, parse_mode="HTML")

# Глобальная переменная для отслеживания активных генераций
active_generations = {}



async def on_cancel_generation(callback: CallbackQuery):
    """Отменяет генерацию изображений."""
    admin_id = callback.from_user.id
    
    if admin_id in active_generations:
        active_generations[admin_id]["cancelled"] = True
        await callback.answer("⏹️ Отмена генерации...")
        
        # Обновляем сообщение
        try:
            status_msg_id = active_generations[admin_id]["status_msg_id"]
            await callback.message.edit_text(
                "⏹️ <b>Генерация отменена</b>\n\n"
                "Процесс остановлен пользователем.",
                parse_mode="HTML"
            )
        except:
            pass
        
        # Удаляем из активных генераций
        del active_generations[admin_id]
    else:
        await callback.answer("❌ Нет активной генерации для отмены")





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