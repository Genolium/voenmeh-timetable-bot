import logging
import os
from datetime import datetime, timedelta, time
from redis.asyncio.client import Redis
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from core.manager import TimetableManager
from core.user_data import UserDataManager
from core.config import (
    MOSCOW_TZ, CHECK_INTERVAL_MINUTES, DATABASE_FILENAME,
    REDIS_SCHEDULE_HASH_KEY, OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS
)
from bot.utils import format_schedule_text
from core.parser import fetch_and_parse_all_schedules
from core.weather_api import WeatherAPI

global_timetable_manager_instance = None

def generate_creative_weather_intro(weather_forecast: dict | None) -> str:
    """
    Генерирует креативную подводку к расписанию, включая краткую сводку погоды.
    """
    if not weather_forecast:
        return "🤷‍♀️ Не удалось получить прогноз погоды. Но расписание всегда под рукой!\n\n"

    temp = int(weather_forecast['temperature'])
    main_weather = weather_forecast.get('main_weather', '').lower()
    description = weather_forecast.get('description', '')
    emoji = weather_forecast.get('emoji', '')
    wind_speed = weather_forecast.get('wind_speed', 0)

    # --- Основная креативная фраза ---
    if 'rain' in main_weather or 'drizzle' in main_weather:
        base_phrase = f"☔️ Кажется, понадобятся зонты! Синоптики обещают дожди."
    elif 'thunderstorm' in main_weather:
        base_phrase = f"⛈️ Ого, возможна гроза! Лучше переждать непогоду в аудитории."
    elif 'snow' in main_weather:
        base_phrase = f"❄️ Зима на пороге! Готовьтесь к снегу и одевайтесь потеплее."
    elif 'clear' in main_weather:
        base_phrase = f"☀️ Отличные новости! Нас ждет ясный и солнечный день."
    elif 'clouds' in main_weather:
        base_phrase = f"☁️ На небе будут облака, но это не помешает нашим планам."
    else: # Для тумана, дымки и т.д.
        base_phrase = f"{emoji} Погода сегодня будет загадочной."

    # --- Совет по одежде ---
    advice = ""
    if temp <= 0:
        advice = "На улице мороз, не забудьте шапку и перчатки! 🧣"
    elif 0 < temp <= 10:
        advice = "Довольно прохладно, куртка — ваш лучший друг. 🧥"
    elif 10 < temp <= 18:
        advice = "Отличная погода для легкой куртки или толстовки."
    elif temp > 18:
        advice = "Наконец-то тепло! Можно одеться полегче. 😎"

    # --- Краткая, но информативная сводка ---
    summary = (
        f"<b>Прогноз на утро:</b> {emoji} {temp}°C, {description}.\n"
        f"Ветер: {wind_speed} м/с."
    )

    # --- Собираем все вместе ---
    return f"{base_phrase}\n{advice}\n\n{summary}\n\n"


async def evening_broadcast(bot: Bot, user_data_manager: UserDataManager):
    """(Запускается в 20:00) Рассылает расписание на завтра с креативной подводкой."""
    logging.info("Запуск вечерней рассылки...")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow_9am = datetime.combine(datetime.now(MOSCOW_TZ).date() + timedelta(days=1), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)
    
    weather_intro_text = generate_creative_weather_intro(weather_forecast)

    try:
        users_to_notify = await user_data_manager.get_users_for_evening_notify()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для вечерней рассылки из БД: {e}")
        return

    for user_id, group_name in users_to_notify:
        tomorrow = datetime.now(MOSCOW_TZ).date() + timedelta(days=1)
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=tomorrow)
        
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"👋 <b>Добрый вечер!</b>\n\n{weather_intro_text}Ваше расписание на завтра:\n\n{format_schedule_text(schedule_info)}"
            try:
                await bot.send_message(user_id, text, disable_web_page_preview=True)
            except Exception as e:
                logging.error(f"Ошибка отправки вечернего расписания для user_id={user_id}: {e}")
    
    logging.info(f"Вечерняя рассылка завершена. Обработано пользователей: {len(users_to_notify)}")


async def morning_summary_broadcast(bot: Bot, user_data_manager: UserDataManager):
    """(Запускается в 8:00) Рассылает расписание на сегодня с креативной подводкой."""
    logging.info("Запуск утренней рассылки-сводки...")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today_9am = datetime.combine(datetime.now(MOSCOW_TZ).date(), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)
    
    weather_intro_text = generate_creative_weather_intro(weather_forecast)

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
            except Exception as e:
                logging.error(f"Ошибка отправки утренней сводки для user_id={user_id}: {e}")

    logging.info(f"Утренняя рассылка-сводка завершена. Обработано пользователей: {len(users_to_notify)}")


async def lesson_reminders_planner(
    bot: Bot, scheduler: AsyncIOScheduler, user_data_manager: UserDataManager
):
    """(Запускается в 6:00) Планирует индивидуальные напоминания о парах."""
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
                
                lesson_start_datetime = datetime.combine(today, datetime.strptime(current_start_time_str, '%H:%M').time(), tzinfo=MOSCOW_TZ)
                
            except (ValueError, IndexError):
                logging.warning(f"Не удалось распарсить время для пары: {current_lesson.get('time', 'N/A')}. Пропускаем напоминание.")
                continue

            reminder_time = None
            if i == 0:
                reminder_time = lesson_start_datetime - timedelta(minutes=20)
            else:
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
        
        info_parts = []
        if lesson.get('room') and lesson['room'].strip() != 'N/A':
            info_parts.append(f"📍{lesson['room']}")
        if lesson.get('teachers'):
            info_parts.append(f"<i>{lesson['teachers']}</i>")
        
        if info_parts:
            text += " ".join(info_parts)
        
        if next_lesson:
             text += f"\n\n<i>Следующая пара в {next_lesson['time']}.</i>"
        else:
             text += f"\n\n<i>Это последняя пара сегодня!</i>"
             
        await bot.send_message(user_id, text, disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания о паре для user_id={user_id}: {e}")


async def monitor_schedule_changes(bot: Bot, user_data_manager: UserDataManager, redis_client: Redis):
    """
    (Запускается по интервалу)
    Проверяет, изменился ли XML-файл расписания на сервере.
    """
    logging.info("Проверка изменений в расписании...")
    
    old_hash_bytes = await redis_client.get(REDIS_SCHEDULE_HASH_KEY)
    old_hash = old_hash_bytes.decode() if old_hash_bytes else ""
    
    new_schedule_data = await fetch_and_parse_all_schedules()

    if not new_schedule_data:
        logging.error("Расписание недоступно на сервере вуза. Проверка изменений невозможна.")
        return
        
    new_hash = new_schedule_data.get('__current_xml_hash__')

    if new_hash and old_hash != new_hash:
        logging.info(f"Обнаружены изменения в расписании! Старый хеш: {old_hash}, Новый хеш: {new_hash}")
        
        await redis_client.set(REDIS_SCHEDULE_HASH_KEY, new_hash)
        logging.info(f"Новый хеш сохранен в Redis по ключу {REDIS_SCHEDULE_HASH_KEY}.")
        
        new_manager_instance = TimetableManager(new_schedule_data, redis_client)
        await new_manager_instance.save_to_cache()
        
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
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления об изменении расписания user_id={user_id}: {e}")
    else:
        logging.info("Изменений в расписании не обнаружено.")


def setup_scheduler(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager, redis_client: Redis) -> AsyncIOScheduler:
    """Настраивает и возвращает экземпляр планировщика с задачами."""
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))
    
    global global_timetable_manager_instance
    global_timetable_manager_instance = manager

    scheduler.add_job(evening_broadcast, 'cron', hour=20, args=(bot, user_data_manager))
    scheduler.add_job(morning_summary_broadcast, 'cron', hour=8, args=(bot, user_data_manager))
    scheduler.add_job(lesson_reminders_planner, 'cron', hour=6, args=(bot, scheduler, user_data_manager))
    
    scheduler.add_job(
        monitor_schedule_changes,
        trigger='interval',
        minutes=CHECK_INTERVAL_MINUTES,
        args=(bot, user_data_manager, redis_client)
    )
    
    return scheduler