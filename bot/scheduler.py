import logging
from datetime import datetime, time, timedelta
import os
import asyncio
import json

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from redis.asyncio.client import Redis

from bot.tasks import send_lesson_reminder_task, send_message_task
from bot.text_formatters import (
    format_schedule_text, format_teacher_schedule_text, generate_evening_intro, generate_morning_intro, get_footer_with_promo
)
from core.config import (
    CHECK_INTERVAL_MINUTES, MOSCOW_TZ, OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS, REDIS_SCHEDULE_HASH_KEY,
    REDIS_SCHEDULE_CACHE_KEY,
)
from core.manager import TimetableManager
from core.schedule_diff import ScheduleDiffDetector, ScheduleDiffFormatter
from core.metrics import SUBSCRIBED_USERS, TASKS_SENT_TO_QUEUE, USERS_TOTAL, LAST_SCHEDULE_UPDATE_TS, ERRORS_TOTAL
from core.parser import fetch_and_parse_all_schedules
from datetime import datetime as _dt
from core.user_data import UserDataManager
from core.weather_api import WeatherAPI
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
from core.admin_reports import send_daily_reports, send_weekly_reports, send_monthly_reports
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)

def print_progress_bar(current: int, total: int, prefix: str = "Прогресс", suffix: str = "", length: int = 50):
    """
    Выводит прогресс-бар в консоль.
    
    Args:
        current: Текущий прогресс
        total: Общее количество
        prefix: Префикс сообщения
        suffix: Суффикс сообщения
        length: Длина прогресс-бара
    """
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    percent = f"{100 * current // total}%"
    print(f'\r{prefix} |{bar}| {percent} {suffix}', end='', flush=True)
    if current == total:
        print()  # Новая строка в конце

async def evening_broadcast(user_data_manager: UserDataManager, timetable_manager: TimetableManager):
    tomorrow = datetime.now(MOSCOW_TZ) + timedelta(days=1)
    logger.info(f"Начинаю постановку задач на вечернюю рассылку для даты {tomorrow.date().isoformat()}")

    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow_9am = MOSCOW_TZ.localize(datetime.combine(tomorrow.date(), time(9, 0)))
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)

    users_to_notify = await user_data_manager.get_users_for_evening_notify()
    if not users_to_notify:
        logger.info("Вечерняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        # Получаем тип пользователя
        user = await user_data_manager.get_full_user_info(user_id)
        user_type = getattr(user, 'user_type', 'student')

        # Генерируем intro с учетом типа пользователя
        intro_text = generate_evening_intro(weather_forecast, target_date=tomorrow, user_type=user_type)

        # Получаем расписание в зависимости от типа пользователя
        if user_type == 'teacher':
            schedule_info = await timetable_manager.get_teacher_schedule(group_name, target_date=tomorrow.date())
        else:
            schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=tomorrow.date())

        has_lessons = bool(schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'))

        # Форматируем расписание в зависимости от типа пользователя
        if has_lessons:
            if user_type == 'teacher':
                schedule_text = format_teacher_schedule_text(schedule_info)
                text_body = f"<b>Ваше расписание на завтра:</b>\n\n{schedule_text}"
            else:
                schedule_text = format_schedule_text(schedule_info)
                text_body = f"<b>Ваше расписание на завтра:</b>\n\n{schedule_text}"
        else:
            if user_type == 'teacher':
                text_body = "Завтра занятий не запланировано."
            else:
                text_body = "🎉 <b>Завтра занятий нет!</b>"
        
        text = f"{intro_text}{text_body}{get_footer_with_promo()}"

        send_message_task.send(user_id, text)
        TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()

async def morning_summary_broadcast(user_data_manager: UserDataManager, timetable_manager: TimetableManager):
    today = datetime.now(MOSCOW_TZ)
    logger.info(f"Начинаю постановку задач на утреннюю рассылку для даты {today.date().isoformat()}")

    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today_9am = MOSCOW_TZ.localize(datetime.combine(today.date(), time(9, 0)))
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)

    users_to_notify = await user_data_manager.get_users_for_morning_summary()
    if not users_to_notify:
        logger.info("Утренняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        # Получаем тип пользователя
        user = await user_data_manager.get_full_user_info(user_id)
        user_type = getattr(user, 'user_type', 'student')

        # Генерируем intro с учетом типа пользователя
        intro_text = generate_morning_intro(weather_forecast, user_type=user_type)

        # Получаем расписание в зависимости от типа пользователя
        if user_type == 'teacher':
            schedule_info = await timetable_manager.get_teacher_schedule(group_name, target_date=today.date())
        else:
            schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=today.date())

        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            # Форматируем расписание в зависимости от типа пользователя
            if user_type == 'teacher':
                schedule_text = format_teacher_schedule_text(schedule_info)
            else:
                schedule_text = format_schedule_text(schedule_info)
            
            text = f"{intro_text}\n<b>Ваше расписание на сегодня:</b>\n\n{schedule_text}{get_footer_with_promo()}"
            send_message_task.send(user_id, text)
            TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()

async def lesson_reminders_planner(scheduler: AsyncIOScheduler, user_data_manager: UserDataManager, timetable_manager: TimetableManager):
    now_in_moscow = datetime.now(MOSCOW_TZ)
    today = now_in_moscow.date()

    users_to_plan = await user_data_manager.get_users_for_lesson_reminders()
    if not users_to_plan:
        return

    for user_id, group_name, reminder_time in users_to_plan:
        try:
            # Пропускаем преподавателей
            user = await user_data_manager.get_full_user_info(user_id)
            if getattr(user, 'user_type', 'student') == 'teacher':
                continue
        except Exception:
            pass
        schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=today)
        if not (schedule_info and not schedule_info.get('error') and schedule_info.get('lessons')):
            continue

        try:
            lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        except (ValueError, KeyError):
            continue
        
        if lessons:
            try:
                start_time_obj = datetime.strptime(lessons[0]['start_time_raw'], '%H:%M').time()
                start_dt = MOSCOW_TZ.localize(datetime.combine(today, start_time_obj))
                reminder_dt = start_dt - timedelta(minutes=reminder_time)

                # Планируем только если время напоминания еще не прошло
                if reminder_dt >= now_in_moscow:
                    run_at = reminder_dt
                    scheduler.add_job(
                        send_lesson_reminder_task.send,
                        trigger=DateTrigger(run_date=run_at),
                        args=(user_id, lessons[0], "first", None, reminder_time),
                        id=f"reminder_{user_id}_{today.isoformat()}_first",
                        replace_existing=True,
                    )
                else:
                    # Если время напоминания уже прошло, пропускаем напоминание
                    continue
            except (ValueError, KeyError) as e:
                logger.warning(f"Ошибка планирования напоминания о первой паре для user_id={user_id}: {e}")

        for i, lesson in enumerate(lessons):
            try:
                end_time_obj = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                reminder_dt = MOSCOW_TZ.localize(datetime.combine(today, end_time_obj))
                
                # Планируем только если время напоминания еще не прошло
                if reminder_dt < now_in_moscow:
                    continue  # Пропускаем прошедшие напоминания
                run_at = reminder_dt
                is_last_lesson = (i == len(lessons) - 1)
                reminder_type = "final" if is_last_lesson else "break"
                next_lesson = lessons[i+1] if not is_last_lesson else None
                break_duration = None
                if next_lesson:
                    next_start_time_obj = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
                    break_duration = int((datetime.combine(today, next_start_time_obj) - datetime.combine(today, end_time_obj)).total_seconds() / 60)
                
                scheduler.add_job(
                    send_lesson_reminder_task.send,
                    trigger=DateTrigger(run_date=run_at),
                    args=(user_id, next_lesson, reminder_type, break_duration, None),
                    id=f"reminder_{user_id}_{today.isoformat()}_{lesson['end_time_raw']}",
                    replace_existing=True,
                )
            except (ValueError, KeyError) as e:
                 logger.warning(f"Ошибка планирования напоминания в перерыве для user_id={user_id}: {e}")

async def cancel_reminders_for_user(scheduler: AsyncIOScheduler, user_id: int):
    try:
        now_in_moscow = datetime.now(MOSCOW_TZ)
        today_iso = now_in_moscow.date().isoformat()
        for job in list(scheduler.get_jobs()):
            if job.id and job.id.startswith(f"reminder_{user_id}_{today_iso}"):
                try:
                    scheduler.remove_job(job.id)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"cancel_reminders_for_user failed for {user_id}: {e}")

async def plan_reminders_for_user(scheduler: AsyncIOScheduler, user_data_manager: UserDataManager, timetable_manager: TimetableManager, user_id: int):
    try:
        # Получаем группу и время напоминания
        user = await user_data_manager.get_full_user_info(user_id)
        # Пропускаем преподавателей
        if user and getattr(user, 'user_type', 'student') == 'teacher':
            return
        if not user or not user.group or not user.lesson_reminders:
            return
        today = datetime.now(MOSCOW_TZ).date()
        now_in_moscow = datetime.now(MOSCOW_TZ)
        
        # Сначала проверяем, есть ли вообще смысл планировать напоминания
        # Получаем расписание только если время напоминания еще актуально
        schedule_info = await timetable_manager.get_schedule_for_day(user.group, target_date=today)
        if not (schedule_info and not schedule_info.get('error') and schedule_info.get('lessons')):
            return
        
        # Сортируем пары
        try:
            lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        except (ValueError, KeyError):
            return
        
        # Первая пара с учётом времени напоминания
        if lessons:
            try:
                start_time_obj = datetime.strptime(lessons[0]['start_time_raw'], '%H:%M').time()
                start_dt = MOSCOW_TZ.localize(datetime.combine(today, start_time_obj))
                reminder_dt = start_dt - timedelta(minutes=(user.reminder_time_minutes or 60))
                
                # ИСПРАВЛЕНИЕ: Проверяем время напоминания ПЕРЕД любыми действиями
                if reminder_dt < now_in_moscow:
                    logger.info(f"Время напоминания уже прошло для пользователя {user_id} (напоминание должно было быть в {reminder_dt}, сейчас {now_in_moscow}), пропускаем")
                    return
                    
                run_at = reminder_dt
                scheduler.add_job(
                    send_lesson_reminder_task.send,
                    trigger=DateTrigger(run_date=run_at),
                    args=(user_id, lessons[0], "first", None, user.reminder_time_minutes),
                    id=f"reminder_{user_id}_{today.isoformat()}_first",
                    replace_existing=True,
                )
                logger.info(f"Запланировано напоминание для пользователя {user_id} на {reminder_dt}")
            except Exception as e:
                logger.warning(f"Ошибка планирования напоминания о первой паре для user_id={user_id}: {e}")
        # Перерывы/конец
        for i, lesson in enumerate(lessons):
            try:
                end_time_obj = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                reminder_dt = MOSCOW_TZ.localize(datetime.combine(today, end_time_obj))
                # Планируем только если время напоминания еще не прошло
                if reminder_dt < now_in_moscow:
                    continue  # Пропускаем прошедшие напоминания
                run_at = reminder_dt
                is_last = (i == len(lessons) - 1)
                next_lesson = lessons[i+1] if not is_last else None
                break_duration = None
                if next_lesson:
                    next_start_time_obj = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
                    break_duration = int((datetime.combine(today, next_start_time_obj) - datetime.combine(today, end_time_obj)).total_seconds() / 60)
                scheduler.add_job(
                    send_lesson_reminder_task.send,
                    trigger=DateTrigger(run_date=run_at),
                    args=(user_id, next_lesson, ("final" if is_last else "break"), break_duration, None),
                    id=f"reminder_{user_id}_{today.isoformat()}_{lesson['end_time_raw']}",
                    replace_existing=True,
                )
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"plan_reminders_for_user failed for {user_id}: {e}")

async def monitor_schedule_changes(user_data_manager: UserDataManager, redis_client: Redis, bot: Bot):
    logger.info("Проверка изменений в расписании...")
    
    global global_timetable_manager_instance # Оставляем для возможности переприсвоения
    
    old_hash = (await redis_client.get(REDIS_SCHEDULE_HASH_KEY) or b'').decode()
    # Add retries
    attempts = 0
    while attempts < 3:
        try:
            new_schedule_data = await fetch_and_parse_all_schedules()
            break
        except Exception as e:
            attempts += 1
            logger.warning(f"Parse attempt {attempts} failed: {e}")
            if attempts == 3:
                logger.critical("Schedule parse failed after retries.")
                # Send alert
                from core.alert_sender import AlertSender
                async with AlertSender({}) as sender:
                    await sender.send({"severity": "critical", "summary": "Schedule parse failed"})
                return

    # For race condition: Use Redis lock
    async with redis_client.lock("timetable_manager_update_lock"):
        if new_schedule_data is None:
            logger.info("Данные расписания не изменились или недоступны (условный запрос).")
            LAST_SCHEDULE_UPDATE_TS.set(_dt.now(MOSCOW_TZ).timestamp())
            return

        if not new_schedule_data:
            logger.error("Не удалось получить расписание с сервера вуза.")
            # Проверяем, есть ли актуальные данные в основном кэше
            cached_data = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)
            if not cached_data:
                logger.warning("Основной кэш также пуст. Система работает на резервных копиях.")
                # Здесь можно добавить дополнительные действия при работе на резервных копиях
            return
            
        new_hash = new_schedule_data.get('__current_xml_hash__')
        if new_hash != old_hash:
            logger.warning(f"ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ В РАСПИСАНИИ! Старый хеш: {old_hash}, Новый: {new_hash}")
            await redis_client.set(REDIS_SCHEDULE_HASH_KEY, new_hash)
            
            # Создаем новый менеджер с обновленными данными
            new_manager = TimetableManager(new_schedule_data, redis_client)
            await new_manager.save_to_cache()
            
            # Отправляем дифф-уведомления пользователям (ОТКЛЮЧЕНО)
            # await send_schedule_diff_notifications(
            #     user_data_manager=user_data_manager,
            #     old_manager=global_timetable_manager_instance,
            #     new_manager=new_manager
            # )
            
            # Переприсваиваем глобальный экземпляр
            global_timetable_manager_instance = new_manager
            
            # Уведомляем администраторов о автоматической генерации
            try:
                admin_users = await user_data_manager.get_admin_users()
                admin_message = (
                    "🔄 <b>Автоматическая генерация изображений</b>\n\n"
                    "Обнаружены изменения в расписании!\n"
                    "✅ Запущена автоматическая генерация изображений для всех групп\n"
                    "📊 Задачи отправлены в очередь Dramatiq\n"
                    "⏱️ Обработка займет несколько минут"
                )
                for admin_id in admin_users:
                    send_message_task.send(admin_id, admin_message)
                    TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()
            except Exception as e:
                logger.warning(f"Не удалось уведомить администраторов: {e}")
        else:
            logger.info("Изменений в расписании не обнаружено.")

    # Обновляем метку времени при КАЖДОЙ успешной проверке
    LAST_SCHEDULE_UPDATE_TS.set(_dt.now(MOSCOW_TZ).timestamp())


# --- Резервные копии расписания ---
BACKUP_PREFIX = "timetable:backup:"

async def backup_current_schedule(redis_client: Redis):
    try:
        data = await redis_client.get(REDIS_SCHEDULE_HASH_KEY)
        # Сохраняем «снапшот» ключа хеша и дамп данных кэша расписания
        from core.config import REDIS_SCHEDULE_CACHE_KEY
        cached_json = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)
        if cached_json:
            ts = _dt.now(MOSCOW_TZ).strftime('%Y%m%d_%H%M%S')
            await redis_client.set(f"{BACKUP_PREFIX}{ts}", cached_json)
            logger.info("Создана резервная копия расписания: %s", ts)
    except Exception as e:
        logger.error("Ошибка при создании резервной копии расписания: %s", e)

async def collect_db_metrics(user_data_manager: UserDataManager):
    try:
        total_users = await user_data_manager.get_total_users_count()
        subscribed_users = await user_data_manager.get_subscribed_users_count()
        USERS_TOTAL.set(total_users)
        SUBSCRIBED_USERS.set(subscribed_users)
    except Exception as e:
        logger.error(f"Ошибка при сборе метрик из БД: {e}")

async def cleanup_image_cache(redis_client: Redis):
    try:
        cache = ImageCacheManager(redis_client, cache_ttl_hours=192)
        await cache.cleanup_expired_cache()
    except Exception as e:
        logger.error(f"Ошибка при плановой очистке кэша изображений: {e}")

# Массовые функции генерации изображений удалены: теплый прогрев, полная генерация, pre-cache helper

async def auto_backup(redis_client: Redis):
    try:
        # Backup DB using Docker (safer approach)
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            backup_file = f"db_backup_{datetime.now(MOSCOW_TZ).strftime('%Y%m%d_%H%M%S')}.sql"
            backup_path = f"/app/data/{backup_file}"
            
            # Extract connection details from DATABASE_URL
            import re
            match = re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', db_url)
            if match:
                user, password, host, port, dbname = match.groups()
                
                # Use docker exec to run pg_dump inside PostgreSQL container
                cmd = [
                    "docker", "exec", "-i", "voenmeh_postgres",
                    "pg_dump", "-h", "localhost", "-U", user, "-d", dbname
                ]
                
                try:
                    import subprocess
                    with open(backup_path, 'w') as f:
                        result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, 
                                              text=True, timeout=300, 
                                              env={**os.environ, 'PGPASSWORD': password})
                    
                    if result.returncode == 0:
                        logger.info(f"Database backup created: {backup_file}")
                    else:
                        logger.error(f"Failed to create database backup: {backup_file}")
                        logger.error(f"Error: {result.stderr}")
                except subprocess.TimeoutExpired:
                    logger.error(f"Database backup timed out: {backup_file}")
                except Exception as e:
                    logger.error(f"Exception during database backup: {e}")
            else:
                logger.error("Could not parse DATABASE_URL for backup")
        else:
            logger.warning("DATABASE_URL not found, skipping database backup")

        # Backup schedules from Redis
        from core.config import REDIS_SCHEDULE_CACHE_KEY
        schedules = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)

        if schedules:
            try:
                # Try to decompress the data (it's likely gzip-compressed)
                import gzip
                import pickle
                import json

                try:
                    # First try to decompress as gzip + pickle (the format used by TimetableManager)
                    decompressed = gzip.decompress(schedules)
                    schedule_data = pickle.loads(decompressed)
                    logger.info("Successfully decompressed schedule data using gzip+pickle")
                except (gzip.BadGzipFile, pickle.UnpicklingError):
                    try:
                        # Fallback: try as plain JSON
                        schedule_data = json.loads(schedules.decode('utf-8'))
                        logger.info("Successfully parsed schedule data as plain JSON")
                    except UnicodeDecodeError:
                        # Last resort: try to decode with error handling
                        schedule_data = json.loads(schedules.decode('utf-8', errors='replace'))
                        logger.warning("Successfully parsed schedule data with error replacement")

                # Write the decompressed data as readable JSON
                backup_file = f"schedules_backup_{datetime.now(MOSCOW_TZ).strftime('%Y%m%d_%H%M%S')}.json"
                with open(backup_file, "w", encoding='utf-8') as f:
                    json.dump(schedule_data, f, ensure_ascii=False, indent=2)
                logger.info(f"Schedule backup created: {backup_file}")

            except Exception as e:
                logger.error(f"Failed to process schedule data for backup: {e}")
                # Fallback: save raw data as binary
                backup_file = f"schedules_backup_raw_{datetime.now(MOSCOW_TZ).strftime('%Y%m%d_%H%M%S')}.bin"
                with open(backup_file, "wb") as f:
                    f.write(schedules)
                logger.info(f"Raw schedule backup created: {backup_file}")
        else:
            logger.info("No schedule data found in Redis cache")

        logger.info("Auto-backup completed successfully")

    except Exception as e:
        logger.error(f"Auto-backup failed with error: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def send_schedule_diff_notifications(
    user_data_manager: UserDataManager,
    old_manager: TimetableManager,
    new_manager: TimetableManager
):
    """
    Отправляет пользователям уведомления только о реальных изменениях в расписании.
    """
    try:
        # Получаем всех пользователей с их группами
        users_with_groups = await user_data_manager.get_all_users_with_groups()
        if not users_with_groups:
            logger.info("Нет пользователей для отправки дифф-уведомлений")
            return
        
        # Группируем пользователей по группам для оптимизации
        groups_to_users = {}
        for user_id, group_name in users_with_groups:
            if group_name:
                if group_name not in groups_to_users:
                    groups_to_users[group_name] = []
                groups_to_users[group_name].append(user_id)
        
        # Проверяем изменения для каждой группы на следующие несколько дней
        from datetime import date, timedelta
        today = date.today()
        dates_to_check = [today + timedelta(days=i) for i in range(7)]  # Проверяем неделю вперед
        
        total_notifications_sent = 0
        groups_with_changes = set()  # Отслеживаем группы с изменениями
        
        for group_name, user_ids in groups_to_users.items():
            group_has_changes = False
            
            for check_date in dates_to_check:
                try:
                    # Получаем старое и новое расписание на дату
                    old_schedule = await old_manager.get_schedule_for_day(group_name, target_date=check_date)
                    new_schedule = await new_manager.get_schedule_for_day(group_name, target_date=check_date)
                    
                    # Сравниваем расписания
                    diff = ScheduleDiffDetector.compare_group_schedules(
                        group=group_name,
                        target_date=check_date,
                        old_schedule_data=old_schedule,
                        new_schedule_data=new_schedule
                    )
                    
                    # Если есть изменения, отправляем уведомления
                    if diff.has_changes():
                        group_has_changes = True
                        groups_with_changes.add(group_name)  # Добавляем группу в список с изменениями
                        message = ScheduleDiffFormatter.format_group_diff(diff)
                        
                        if message:
                            # Отправляем уведомление всем пользователям группы
                            for user_id in user_ids:
                                send_message_task.send(user_id, message)
                                TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()
                                total_notifications_sent += 1
                            
                            logger.info(f"Отправлены дифф-уведомления для группы {group_name} на {check_date}: {len(user_ids)} пользователей")
                
                except Exception as e:
                    logger.error(f"Ошибка при сравнении расписания группы {group_name} на {check_date}: {e}")
                    continue
            
            if not group_has_changes:
                logger.debug(f"Нет изменений в расписании группы {group_name}")
        
        logger.info(f"Отправлено {total_notifications_sent} дифф-уведомлений о изменениях в расписании")
        
        # Уведомляем администраторов о количестве отправленных уведомлений
        if total_notifications_sent > 0:
            try:
                admin_users = await user_data_manager.get_admin_users()
                admin_message = (
                    f"📊 <b>Отчет о дифф-уведомлениях</b>\n\n"
                    f"Обнаружены изменения в расписании\n"
                    f"📤 Отправлено уведомлений: {total_notifications_sent}\n"
                    f"👥 Затронуто групп: {len(groups_with_changes)}\n"
                    f"⏱️ Проверены даты: {len(dates_to_check)} дней вперед"
                )
                for admin_id in admin_users:
                    send_message_task.send(admin_id, admin_message)
                    TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()
            except Exception as e:
                logger.warning(f"Не удалось уведомить администраторов о дифф-уведомлениях: {e}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при отправке дифф-уведомлений: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def handle_graduated_groups(user_data_manager: UserDataManager, timetable_manager: TimetableManager, redis_client: Redis):
    """
    Обрабатывает ситуации, когда группы выпустились и больше не существуют в расписании.
    Уведомляет пользователей и предлагает выбрать новую группу.
    """
    try:
        logger.info("🔍 Проверяю наличие выпустившихся групп...")
        
        # Получаем всех пользователей с их группами
        users_with_groups = await user_data_manager.get_all_users_with_groups()
        if not users_with_groups:
            logger.info("Нет пользователей для проверки")
            return
        
        # Получаем актуальные группы из расписания
        current_groups = set(timetable_manager._schedules.keys())
        # Исключаем служебные ключи
        current_groups = {g for g in current_groups if not g.startswith('__')}
        
        graduated_groups = set()
        affected_users = []
        
        # Проверяем каждую группу пользователей
        for user_id, group_name in users_with_groups:
            if group_name and group_name.upper() not in current_groups:
                graduated_groups.add(group_name.upper())
                affected_users.append((user_id, group_name))
        
        if not affected_users:
            logger.info("✅ Все группы пользователей актуальны")
            return
        
        logger.warning(f"⚠️ Обнаружены выпустившиеся группы: {', '.join(graduated_groups)}")
        logger.info(f"📊 Затронуто пользователей: {len(affected_users)}")
        
        # Получаем список доступных групп для предложения
        available_groups = sorted(list(current_groups))
        
        # Уведомляем пользователей
        bot = None
        try:
            from main import bot_instance
            bot = bot_instance
        except:
            logger.warning("Не удалось получить экземпляр бота для уведомлений")
            return
        
        if not bot:
            logger.warning("Бот недоступен для отправки уведомлений")
            return
        
        notified_count = 0
        for user_id, old_group in affected_users:
            try:
                # Формируем сообщение
                message_text = (
                    f"⚠️ <b>Внимание!</b>\n\n"
                    f"Группа <b>{old_group}</b> больше не существует в расписании.\n"
                    f"Возможно, группа выпустилась или была переименована.\n\n"
                    f"Пожалуйста, выберите новую группу:\n"
                    f"<code>/start</code> - для выбора группы\n\n"
                    f"Доступные группы: {', '.join(available_groups[:10])}"
                    + (f"\n... и еще {len(available_groups) - 10} групп" if len(available_groups) > 10 else "")
                )
                
                # Отправляем уведомление
                await bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML"
                )
                
                # Очищаем группу пользователя
                await user_data_manager.set_user_group(user_id, None)
                
                notified_count += 1
                logger.info(f"✅ Уведомлен пользователь {user_id} о выпуске группы {old_group}")
                
            except Exception as e:
                logger.error(f"❌ Ошибка уведомления пользователя {user_id}: {e}")
        
        logger.info(f"📊 Уведомлено пользователей: {notified_count}/{len(affected_users)}")
        
        # Сохраняем статистику в Redis для мониторинга
        try:
            stats_key = "graduated_groups_stats"
            stats_data = {
                "timestamp": datetime.now(MOSCOW_TZ).isoformat(),
                "graduated_groups": list(graduated_groups),
                "affected_users": len(affected_users),
                "notified_users": notified_count
            }
            await redis_client.set(stats_key, json.dumps(stats_data, ensure_ascii=False), ex=86400)
        except Exception as e:
            logger.warning(f"Не удалось сохранить статистику: {e}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке выпустившихся групп: {e}")
        import traceback
        logger.error(traceback.format_exc())

def setup_scheduler(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager, redis_client: Redis) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))
    
    global global_timetable_manager_instance
    global_timetable_manager_instance = manager

    scheduler.add_job(evening_broadcast, 'cron', hour=20, minute=0, args=[user_data_manager, manager])
    scheduler.add_job(morning_summary_broadcast, 'cron', hour=8, minute=0, args=[user_data_manager, manager])
    scheduler.add_job(lesson_reminders_planner, 'cron', hour=6, minute=0, args=[scheduler, user_data_manager, manager])
    scheduler.add_job(monitor_schedule_changes, 'interval', minutes=CHECK_INTERVAL_MINUTES, args=[user_data_manager, redis_client, bot])
    scheduler.add_job(collect_db_metrics, 'interval', minutes=1, args=[user_data_manager])
    scheduler.add_job(backup_current_schedule, 'cron', hour='*/6', args=[redis_client])
    scheduler.add_job(auto_backup, 'cron', hour=2, args=[redis_client])
    scheduler.add_job(handle_graduated_groups, 'interval', minutes=10, args=[user_data_manager, manager, redis_client])

    # Задачи для отправки отчётов администраторам
    scheduler.add_job(send_daily_reports, 'cron', hour=9, minute=0, args=[bot, user_data_manager])  # Ежедневно в 9:00
    scheduler.add_job(send_weekly_reports, 'cron', hour=9, minute=0, day_of_week='mon', args=[bot, user_data_manager])  # Еженедельно по понедельникам в 9:00
    scheduler.add_job(send_monthly_reports, 'cron', hour=9, minute=0, day=1, args=[bot, user_data_manager])  # Ежемесячно 1-го числа в 9:00
    
    # Дополнительные задания можно включать через флаг окружения (по умолчанию выключены для облегчения нагрузки и совместимости с тестами)
    if os.getenv('ENABLE_IMAGE_CACHE_JOBS', '0') in ('1', 'true', 'True'):
        # Ежечасная очистка устаревших изображений из кэша
        scheduler.add_job(cleanup_image_cache, 'cron', minute=5, args=[redis_client])
    
    return scheduler