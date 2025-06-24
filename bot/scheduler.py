import logging
from datetime import datetime, timedelta, time

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from core.manager import TimetableManager
from core.user_data import UserDataManager
from core.config import MOSCOW_TZ
from bot.utils import format_schedule_text


async def evening_broadcast(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager):
    """
    (Запускается в 20:00)
    Рассылает полное расписание на завтра тем пользователям,
    у которых включена опция `evening_notify`.
    """
    logging.info("Запуск вечерней рассылки...")
    try:
        users_to_notify = await user_data_manager.get_users_for_evening_notify()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для вечерней рассылки из БД: {e}")
        return

    for user_id, group_name in users_to_notify:
        tomorrow = datetime.now(MOSCOW_TZ).date() + timedelta(days=1)
        schedule_info = manager.get_schedule_for_day(group_name, target_date=tomorrow)
        
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"👋 <b>Твоё расписание на завтра:</b>\n\n{format_schedule_text(schedule_info)}"
            try:
                await bot.send_message(user_id, text)
                logging.info(f"Отправлено вечернее расписание для user_id={user_id}")
            except Exception as e:
                logging.error(f"Ошибка отправки вечернего расписания для user_id={user_id}: {e}")
    
    logging.info(f"Вечерняя рассылка завершена. Обработано пользователей: {len(users_to_notify)}")


async def morning_summary_broadcast(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager):
    """
    (Запускается в 8:00)
    Рассылает полное расписание на сегодня тем пользователям,
    у которых включена опция `morning_summary`.
    """
    logging.info("Запуск утренней рассылки-сводки...")
    try:
        users_to_notify = await user_data_manager.get_users_for_morning_summary()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для утренней сводки из БД: {e}")
        return

    for user_id, group_name in users_to_notify:
        today = datetime.now(MOSCOW_TZ).date()
        schedule_info = manager.get_schedule_for_day(group_name, target_date=today)
        
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"☀️ <b>Доброе утро! Расписание на сегодня:</b>\n\n{format_schedule_text(schedule_info)}"
            try:
                await bot.send_message(user_id, text)
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
        schedule_info = manager.get_schedule_for_day(group_name, target_date=today)
        if not schedule_info or 'error' in schedule_info or not schedule_info.get('lessons'):
            continue
        
        lessons = sorted(schedule_info['lessons'], key=lambda x: x['time'])
        
        for i, current_lesson in enumerate(lessons):
            try:
                hour, minute = map(int, current_lesson['time'].split(':'))
                lesson_start_time = datetime.combine(today, time(hour, minute), tzinfo=MOSCOW_TZ)
            except (ValueError, IndexError):
                logging.warning(f"Не удалось распарсить время для пары: {current_lesson['time']}. Пропускаем напоминание.")
                continue

            reminder_time = None
            if i == 0:
                reminder_time = lesson_start_time - timedelta(minutes=20)
            else:
                prev_lesson = lessons[i-1]
                prev_hour, prev_minute = map(int, prev_lesson['time'].split(':'))
                end_of_prev_lesson = datetime.combine(today, time(prev_hour, prev_minute), tzinfo=MOSCOW_TZ) + timedelta(minutes=90)
                reminder_time = end_of_prev_lesson
            
            if reminder_time > datetime.now(MOSCOW_TZ):
                job_id = f"lesson_{user_id}_{today.isoformat()}_{current_lesson['time']}"
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
             
        await bot.send_message(user_id, text)
        logging.info(f"Отправлено напоминание о паре для user_id={user_id}")
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания о паре для user_id={user_id}: {e}")


def setup_scheduler(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager) -> AsyncIOScheduler:
    """Настраивает и возвращает экземпляр планировщика с тремя основными задачами."""
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))
    
    scheduler.add_job(evening_broadcast, 'cron', hour=20, args=(bot, manager, user_data_manager))
    scheduler.add_job(morning_summary_broadcast, 'cron', hour=8, args=(bot, manager, user_data_manager))
    scheduler.add_job(lesson_reminders_planner, 'cron', hour=6, args=(bot, scheduler, manager, user_data_manager))
    
    return scheduler