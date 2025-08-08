import logging
from datetime import datetime, time, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from redis.asyncio.client import Redis

from bot.tasks import send_lesson_reminder_task, send_message_task
from bot.text_formatters import (
    format_schedule_text, generate_evening_intro, generate_morning_intro, UNSUBSCRIBE_FOOTER
)
from core.config import (
    CHECK_INTERVAL_MINUTES, MOSCOW_TZ, OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS, REDIS_SCHEDULE_HASH_KEY
)
from core.manager import TimetableManager
from core.metrics import SUBSCRIBED_USERS, TASKS_SENT_TO_QUEUE, USERS_TOTAL, LAST_SCHEDULE_UPDATE_TS, ERRORS_TOTAL
from core.parser import fetch_and_parse_all_schedules
from datetime import datetime as _dt
from core.user_data import UserDataManager
from core.weather_api import WeatherAPI

global_timetable_manager_instance = None
logger = logging.getLogger(__name__)

async def evening_broadcast(user_data_manager: UserDataManager):
    tomorrow = datetime.now(MOSCOW_TZ) + timedelta(days=1)
    logger.info(f"Начинаю постановку задач на вечернюю рассылку для даты {tomorrow.date().isoformat()}")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow_9am = MOSCOW_TZ.localize(datetime.combine(tomorrow.date(), time(9, 0)))
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)
    intro_text = generate_evening_intro(weather_forecast, target_date=tomorrow)
    
    users_to_notify = await user_data_manager.get_users_for_evening_notify()
    if not users_to_notify:
        logger.info("Вечерняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=tomorrow.date())
        has_lessons = bool(schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'))
        
        text_body = f"<b>Ваше расписание на завтра:</b>\n\n{format_schedule_text(schedule_info)}" if has_lessons else "🎉 <b>Завтра занятий нет!</b>"
        text = f"{intro_text}{text_body}{UNSUBSCRIBE_FOOTER}"
        
        send_message_task.send(user_id, text)
        TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()

async def morning_summary_broadcast(user_data_manager: UserDataManager):
    today = datetime.now(MOSCOW_TZ)
    logger.info(f"Начинаю постановку задач на утреннюю рассылку для даты {today.date().isoformat()}")

    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today_9am = MOSCOW_TZ.localize(datetime.combine(today.date(), time(9, 0)))
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)
    intro_text = generate_morning_intro(weather_forecast)
    
    users_to_notify = await user_data_manager.get_users_for_morning_summary()
    if not users_to_notify:
        logger.info("Утренняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today.date())
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"{intro_text}\n<b>Ваше расписание на сегодня:</b>\n\n{format_schedule_text(schedule_info)}{UNSUBSCRIBE_FOOTER}"
            send_message_task.send(user_id, text)
            TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()

async def lesson_reminders_planner(scheduler: AsyncIOScheduler, user_data_manager: UserDataManager):
    now_in_moscow = datetime.now(MOSCOW_TZ)
    today = now_in_moscow.date()
    logger.info(f"Запуск планировщика напоминаний о парах для даты {today.isoformat()}")

    users_to_plan = await user_data_manager.get_users_for_lesson_reminders()
    if not users_to_plan:
        return

    for user_id, group_name, reminder_time in users_to_plan:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today)
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
                
                if reminder_dt > now_in_moscow:
                    scheduler.add_job(send_lesson_reminder_task.send, trigger=DateTrigger(run_date=reminder_dt),
                                      args=(user_id, lessons[0], "first", None, reminder_time), id=f"reminder_{user_id}_{today.isoformat()}_first", replace_existing=True)
            except (ValueError, KeyError) as e:
                logger.warning(f"Ошибка планирования напоминания о первой паре для user_id={user_id}: {e}")

        for i, lesson in enumerate(lessons):
            try:
                end_time_obj = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                reminder_dt = MOSCOW_TZ.localize(datetime.combine(today, end_time_obj))
                
                if reminder_dt > now_in_moscow:
                    is_last_lesson = (i == len(lessons) - 1)
                    reminder_type = "final" if is_last_lesson else "break"
                    next_lesson = lessons[i+1] if not is_last_lesson else None
                    break_duration = None
                    if next_lesson:
                        next_start_time_obj = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
                        break_duration = int((datetime.combine(today, next_start_time_obj) - datetime.combine(today, end_time_obj)).total_seconds() / 60)
                    
                    scheduler.add_job(send_lesson_reminder_task.send, trigger=DateTrigger(run_date=reminder_dt),
                                      args=(user_id, next_lesson, reminder_type, break_duration, None), id=f"reminder_{user_id}_{today.isoformat()}_{lesson['end_time_raw']}", replace_existing=True)
            except (ValueError, KeyError) as e:
                 logger.warning(f"Ошибка планирования напоминания в перерыве для user_id={user_id}: {e}")

async def monitor_schedule_changes(user_data_manager: UserDataManager, redis_client: Redis):
    logger.info("Проверка изменений в расписании...")
    old_hash = (await redis_client.get(REDIS_SCHEDULE_HASH_KEY) or b'').decode()
    new_schedule_data = await fetch_and_parse_all_schedules()

    if new_schedule_data is None:
        # 304 Not Modified / ошибка сети — ничего не делаем
        logger.info("Данные расписания не изменились или недоступны (условный запрос).")
        return

    if not new_schedule_data:
        logger.error("Не удалось получить расписание с сервера вуза.")
        return
        
    new_hash = new_schedule_data.get('__current_xml_hash__')
    if new_hash and old_hash != new_hash:
        logger.warning(f"ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ В РАСПИСАНИИ! Старый хеш: {old_hash}, Новый: {new_hash}")
        await redis_client.set(REDIS_SCHEDULE_HASH_KEY, new_hash)
        
        new_manager = TimetableManager(new_schedule_data, redis_client)
        await new_manager.save_to_cache()
        global global_timetable_manager_instance
        global_timetable_manager_instance = new_manager
        LAST_SCHEDULE_UPDATE_TS.set(_dt.now(MOSCOW_TZ).timestamp())
        
        all_users = await user_data_manager.get_all_user_ids()
        message_text = "❗️ <b>ВНИМАНИЕ! Обновление расписания!</b>\n\nРасписание в боте было обновлено. Пожалуйста, проверьте актуальное расписание своей группы."
        for user_id in all_users:
            send_message_task.send(user_id, message_text)
            TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()
    else:
        logger.info("Изменений в расписании не обнаружено.")


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

def setup_scheduler(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager, redis_client: Redis) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))
    
    global global_timetable_manager_instance
    global_timetable_manager_instance = manager

    scheduler.add_job(evening_broadcast, 'cron', hour=20, minute=0, args=[user_data_manager])
    scheduler.add_job(morning_summary_broadcast, 'cron', hour=8, minute=0, args=[user_data_manager])
    scheduler.add_job(lesson_reminders_planner, 'cron', hour=6, minute=0, args=[scheduler, user_data_manager])
    scheduler.add_job(monitor_schedule_changes, 'interval', minutes=CHECK_INTERVAL_MINUTES, args=[user_data_manager, redis_client])
    scheduler.add_job(collect_db_metrics, 'interval', minutes=1, args=[user_data_manager]) 
    # Периодические резервные копии каждые 6 часов
    scheduler.add_job(backup_current_schedule, 'cron', hour='*/6', args=[redis_client])
    
    return scheduler