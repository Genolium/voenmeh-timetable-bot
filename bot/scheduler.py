import logging
import os
from datetime import datetime, timedelta, time, timezone 

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from core.manager import TimetableManager
from core.user_data import UserDataManager
from core.config import MOSCOW_TZ, SCHEDULE_HASH_FILE, CHECK_INTERVAL_MINUTES, DATABASE_FILENAME, CACHE_FILENAME, OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS
from bot.utils import format_schedule_text
from core.parser import fetch_and_parse_all_schedules
from core.weather_api import WeatherAPI


global_timetable_manager_instance = None


async def evening_broadcast(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager):
    """
    (Запускается в 20:00)
    Рассылает полное расписание на завтра тем пользователям,
    у которых включена опция `evening_notify`, с прогнозом погоды на завтрашнее утро.
    """
    logging.info("Запуск вечерней рассылки...")
    
    # Погода на завтрашнее утро (к 9:00)
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow_9am = datetime.combine(datetime.now(MOSCOW_TZ).date() + timedelta(days=1), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)
    
    weather_intro_text = ""
    if weather_forecast:
        weather_intro_text = (
            f"☀️ Прогноз на завтра, к {weather_forecast['forecast_time']}: "
            f"{weather_forecast['emoji']} {weather_forecast['temperature']}°C, {weather_forecast['description']}.\n"
            f"Влажность: {weather_forecast['humidity']}%, Ветер: {weather_forecast['wind_speed']} м/с.\n\n"
        )
    else:
        weather_intro_text = "🤷‍♀️ Не удалось получить прогноз погоды на завтра.\n\n"

    try:
        users_to_notify = await user_data_manager.get_users_for_evening_notify()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для вечерней рассылки из БД: {e}")
        return

    for user_id, group_name in users_to_notify:
        tomorrow = datetime.now(MOSCOW_TZ).date() + timedelta(days=1)
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=tomorrow)
        
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"👋 <b>Твоё расписание на завтра:</b>\n\n{weather_intro_text}{format_schedule_text(schedule_info)}"
            try:
                await bot.send_message(user_id, text, disable_web_page_preview=True)
                logging.info(f"Отправлено вечернее расписание для user_id={user_id}")
            except Exception as e:
                logging.error(f"Ошибка отправки вечернего расписания для user_id={user_id}: {e}")
    
    logging.info(f"Вечерняя рассылка завершена. Обработано пользователей: {len(users_to_notify)}")


async def morning_summary_broadcast(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager):
    """
    (Запускается в 8:00)
    Рассылает полное расписание на сегодня тем пользователям,
    у которых включена опция `morning_summary`, с прогнозом погоды на сегодня.
    """
    logging.info("Запуск утренней рассылки-сводки...")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today_9am = datetime.combine(datetime.now(MOSCOW_TZ).date(), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)
    
    weather_intro_text = ""
    if weather_forecast:
        weather_intro_text = (
            f"☀️ Прогноз на сегодня, к {weather_forecast['forecast_time']}: "
            f"{weather_forecast['emoji']} {weather_forecast['temperature']}°C, {weather_forecast['description']}.\n"
            f"Влажность: {weather_forecast['humidity']}%, Ветер: {weather_forecast['wind_speed']} м/с.\n\n"
        )
    else:
        weather_intro_text = "🤷‍♀️ Не удалось получить прогноз погоды на сегодня.\n\n"

    try:
        users_to_notify = await user_data_manager.get_users_for_morning_summary()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для утренней сводки из БД: {e}")
        return

    for user_id, group_name in users_to_notify:
        today = datetime.now(MOSCOW_TZ).date()
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today)
        
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"☀️ <b>Доброе утро!</b>\n\n{weather_intro_text}Ваше расписание на сегодня:\n\n{format_schedule_text(schedule_info)}"
            try:
                await bot.send_message(user_id, text, disable_web_page_preview=True)
                logging.info(f"Отправлена утренняя сводка для user_id={user_id}")
            except Exception as e:
                logging.error(f"Ошибка отправки утренней сводки для user_id={user_id}: {e}")

    logging.info(f"Утренняя рассылка-сводка завершена. Обработано пользователей: {len(users_to_notify)}")


async def lesson_reminders_planner(
    bot: Bot, scheduler: AsyncIOScheduler, manager: TimetableManager, user_data_manager: UserDataManager
):
    """
    (Запускается в 6:00)
    Планирует индивидуальные напоминания о парах на весь день для тех,
    у кого включена опция `lesson_reminders`.
    """
    logging.info("Запуск планировщика напоминаний о парах...")
    today = datetime.now(MOSCOW_TZ).date()
    try:
        users_to_plan = await user_data_manager.get_users_for_lesson_reminders()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для планирования напоминаний из БД: {e}")
        return

    for user_id, group_name in users_to_plan:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today)
        if not schedule_info or 'error' in schedule_info or not schedule_info.get('lessons'):
            continue
        
        lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        
        for i, current_lesson in enumerate(lessons):
            try:
                current_start_time_str = current_lesson['start_time_raw']
                current_end_time_str = current_lesson['end_time_raw']
                
                lesson_start_datetime = datetime.combine(today, datetime.strptime(current_start_time_str, '%H:%M').time(), tzinfo=MOSCOW_TZ)
                
            except (ValueError, IndexError):
                logging.warning(f"Не удалось распарсить время для пары: {current_lesson.get('time', 'N/A')}. Пропускаем напоминание.")
                continue

            reminder_time = None
            if i == 0:
                # Для первой пары - за 20 минут до начала
                reminder_time = lesson_start_datetime - timedelta(minutes=20)
            else:
                # Для последующих - в момент окончания предыдущей
                prev_lesson = lessons[i-1]
                prev_end_time_str = prev_lesson['end_time_raw']
                prev_end_time_obj = datetime.strptime(prev_end_time_str, '%H:%M').time()
                reminder_time = datetime.combine(today, prev_end_time_obj, tzinfo=MOSCOW_TZ)
            
            if reminder_time and reminder_time > datetime.now(MOSCOW_TZ):
                job_id = f"lesson_{user_id}_{today.isoformat()}_{current_start_time_str}"
                next_lesson = lessons[i+1] if i + 1 < len(lessons) else None
                
                scheduler.add_job(
                    send_lesson_reminder,
                    trigger=DateTrigger(run_date=reminder_time),
                    args=(bot, user_id, current_lesson, next_lesson),
                    id=job_id,
                    replace_existing=True
                )
    
    logging.info(f"Планирование напоминаний о парах завершено. Обработано пользователей: {len(users_to_plan)}")


async def send_lesson_reminder(bot: Bot, user_id: int, lesson: dict, next_lesson: dict | None):
    """Отправляет индивидуальное напоминание о конкретной паре."""
    try:
        text = f"🔔 <b>Скоро пара: {lesson['time']}</b>\n\n"
        text += f"<b>{lesson['subject']}</b> ({lesson['type']})\n"
        text += f"📍 {lesson['room']}, <i>{lesson['teachers']}</i>"
        
        if next_lesson:
             text += f"\n\n<i>Следующая пара в {next_lesson['time']}.</i>"
        else:
             text += f"\n\n<i>Это последняя пара сегодня!</i>"
             
        await bot.send_message(user_id, text, disable_web_page_preview=True)
        logging.info(f"Отправлено напоминание о паре для user_id={user_id}")
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания о паре для user_id={user_id}: {e}")


async def monitor_schedule_changes(bot: Bot, user_data_manager: UserDataManager):
    """
    (Запускается по интервалу)
    Проверяет, изменился ли XML-файл расписания на сервере.
    Если изменился, уведомляет всех пользователей и обновляет глобальный TimetableManager.
    """
    logging.info("Проверка изменений в расписании...")
    
    hash_file_path = os.path.join(DATABASE_FILENAME.parent, SCHEDULE_HASH_FILE) 

    old_hash = ""
    try:
        with open(hash_file_path, 'r', encoding='utf-8') as f:
            old_hash = f.read().strip()
    except FileNotFoundError:
        logging.info(f"Файл хеша {hash_file_path} не найден. Это первый запуск или сброс кэша.")
    except Exception as e:
        logging.error(f"Ошибка чтения файла хеша {hash_file_path}: {e}")

    new_schedule_data_result = fetch_and_parse_all_schedules()

    if not new_schedule_data_result:
        logging.error("Расписание недоступно на сервере вуза. Проверка изменений невозможна.")
        return
        
    new_hash = new_schedule_data_result.get('__current_xml_hash__')

    if new_hash and old_hash != new_hash:
        logging.info(f"Обнаружены изменения в расписании! Старый хеш: {old_hash}, Новый хеш: {new_hash}")
        
        try:
            with open(hash_file_path, 'w', encoding='utf-8') as f:
                f.write(new_hash)
            logging.info(f"Новый хеш сохранен в {hash_file_path}.")
        except Exception as e:
            logging.error(f"Ошибка записи хеша в файл {hash_file_path}: {e}")
        
        new_manager_instance = TimetableManager(new_schedule_data_result)
        new_manager_instance.save_to_cache() 
        
        global global_timetable_manager_instance
        global_timetable_manager_instance = new_manager_instance
        logging.info("Глобальный TimetableManager успешно обновлен новым расписанием.")
        
        user_data_manager_for_broadcast = UserDataManager(db_path=DATABASE_FILENAME)
        all_users = await user_data_manager_for_broadcast.get_all_user_ids()
        
        message_text = (
            "❗️ <b>ВНИМАНИЕ! Обновление расписания!</b>\n\n"
            "На сайте Военмеха обнаружены изменения в расписании.\n"
            "Расписание в боте обновлено. Пожалуйста, проверьте актуальное расписание своей группы."
        )

        for user_id in all_users:
            try:
                await bot.send_message(user_id, message_text, disable_web_page_preview=True)
                logging.info(f"Отправлено уведомление об изменении расписания user_id={user_id}")
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления об изменении расписания user_id={user_id}: {e}")
    else:
        logging.info("Изменений в расписании не обнаружено.")


def setup_scheduler(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager) -> AsyncIOScheduler:
    """Настраивает и возвращает экземпляр планировщика с тремя основными задачами."""
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))
    
    global global_timetable_manager_instance
    global_timetable_manager_instance = manager

    scheduler.add_job(evening_broadcast, 'cron', hour=20, args=(bot, manager, user_data_manager))
    scheduler.add_job(morning_summary_broadcast, 'cron', hour=8, args=(bot, manager, user_data_manager))
    scheduler.add_job(lesson_reminders_planner, 'cron', hour=6, args=(bot, scheduler, manager, user_data_manager))
    
    scheduler.add_job(
        monitor_schedule_changes,
        trigger='interval',
        minutes=CHECK_INTERVAL_MINUTES,
        args=(bot, user_data_manager)
    )
    
    return scheduler