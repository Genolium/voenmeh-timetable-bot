import logging
import random
from datetime import datetime, time, timedelta
from typing import Dict, Any, List 

from aiogram import Bot 
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from redis.asyncio.client import Redis

from bot.tasks import send_lesson_reminder_task, send_message_task 
from bot.utils import format_schedule_text
from core.config import (
    CHECK_INTERVAL_MINUTES, MOSCOW_TZ, OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS, REDIS_SCHEDULE_HASH_KEY
)
from core.manager import TimetableManager

from core.metrics import SUBSCRIBED_USERS, TASKS_SENT_TO_QUEUE, USERS_TOTAL
from core.parser import fetch_and_parse_all_schedules
from core.user_data import UserDataManager
from core.weather_api import WeatherAPI

# Глобальный экземпляр для обновления в реальном времени при мониторинге
global_timetable_manager_instance = None

UNSUBSCRIBE_FOOTER = "\n\n<tg-spoiler><i>Отключить эту рассылку можно в «⚙️ Настройки»</i></tg-spoiler>"

# --- БЛОКИ КОНТЕНТА ДЛЯ ГЕНЕРАТОРА ---
EVENING_GREETINGS = ["Добрый вечер! 👋", "Привет! Готовимся к завтрашнему дню.", "Вечерняя сводка на подходе.", "Как прошел день?", "Время планировать завтра."]
MORNING_GREETINGS = ["Доброе утро! ☀️", "Утро доброе! Учеба ждет.", "Утренняя сводка готова!", "Начинаем новый день!", "Всем продуктивного утра!"]

DAY_OF_WEEK_CONTEXT = {
    0: ["Завтра понедельник — начинаем неделю с чистого листа!", "Готовимся к началу новой недели.", "Завтра снова в бой! 💪"],
    1: ["Завтра вторник, втягиваемся в ритм.", "Планируем продуктивный вторник.", "Завтра второй день недели, полет нормальный."],
    2: ["Завтра среда — экватор недели!", "Середина недели уже завтра. Держимся!", "Завтра — маленькая пятница."],
    3: ["Завтра четверг, финишная прямая близко.", "Еще один рывок до конца недели!", "Готовимся к продуктивному четвергу."],
    4: ["Завтра пятница! Впереди заслуженный отдых.", "Последний рывок перед чиллом!", "Какие планы на завтрашний вечер пятницы?"],
    5: ["Завтра учебная суббота — для самых стойких.", "Еще один день знаний, а потом отдых.", "Готовимся к учебной субботе."],
    6: ["Завтра воскресенье — можно выспаться!", "Впереди выходной, но не забудьте про домашку 😉", "Завтра день отдыха!"]
}

WEATHER_OBSERVATIONS = {
    "clear": ["Нас ждет ясное небо. ☀️", "Прогноз обещает солнце!", "Похоже, завтра будет отличная погода."],
    "rain": ["Ожидается дождь. Не забудьте зонт! 🌧️", "Питерская классика: завтра {description}.", "Дождь — не помеха для великих дел."],
    "snow": ["Готовимся к снегу! ❄️", "Завтра может быть скользко, будьте осторожны.", "Нас ждет зимняя сказка."],
    "clouds": ["Завтра будет облачно, но без осадков. ☁️", "Солнце будет играть в прятки за тучами.", "Ожидается переменная облачность."],
    "default": ["Прогноз на завтра: {description}.", "Синоптики обещают на завтра {description}."]
}

CLOTHING_ADVICES = {
    "cold": ["Завтра будет морозно, не забудьте шапку и перчатки!", "Советуем одеться потеплее. Лучше снять лишнее, чем замерзнуть.", "Ощущаться будет как все {temp_feels_like}°C, утепляйтесь!"],
    "cool": ["Завтра утром будет прохладно, легкая куртка или свитер будут в самый раз.", "Осенняя прохлада требует уютного шарфа.", "Не дайте завтрашней погоде застать вас врасплох!"],
    "warm": ["Завтра обещают тепло, можно одеться полегче.", "Отличная погода, чтобы насладиться городом после учебы.", "Наконец-то можно будет оставить тяжелые куртки дома."],
    "hot": ["Завтра будет жарко! Пейте больше воды.", "Настоящее лето! Идеально для легкой одежды и солнечных очков.", "Готовьтесь к теплому дню!"],
}

EVENING_ENGAGEMENT_BLOCKS = {
    "prep_tip": [
        "💡 Совет на вечер: Соберите рюкзак с вечера, чтобы утром было меньше суеты.",
        "💡 Совет на вечер: Хороший сон — залог продуктивного дня. Постарайтесь лечь пораньше!",
        "💡 Совет на вечер: Просмотрите конспекты сегодняшних лекций, чтобы лучше их запомнить."
    ],
    "planning_question": [
        "🤔 Вопрос на вечер: Какая пара завтра кажется самой сложной?",
        "🤔 Вопрос на вечер: Какие цели ставите на завтрашний день, кроме учебы?",
        "🤔 Вопрос на вечер: Уже решили, где будете обедать завтра?"
    ],
    "quote": [
        "📖 Цитата вечера: «Успех — это успеть». Готовимся успеть все запланированное!",
        "📖 Цитата вечера: «Лучшее время, чтобы посадить дерево, было 20 лет назад. Следующее лучшее время — сегодня». — Китайская пословица",
        "📖 Цитата вечера: «Планы — ничто, планирование — всё». — Дуайт Эйзенхауэр"
    ]
}


def generate_evening_intro(weather_forecast: Dict[str, Any] | None, target_date: datetime) -> str:
    """Генерирует уникальную вечернюю подводку с фокусом на завтрашнем дне."""
    weekday = target_date.weekday()
    greeting_line = random.choice(EVENING_GREETINGS)
    day_context_line = random.choice(DAY_OF_WEEK_CONTEXT.get(weekday, [""]))
    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast['temperature'])
        description = weather_forecast.get('description', '').lower()
        advice_line = ""
        if temp <= 0: advice_line = random.choice(CLOTHING_ADVICES["cold"]).format(temp_feels_like=temp - 5)
        elif 0 < temp <= 12: advice_line = random.choice(CLOTHING_ADVICES["cool"])
        elif 12 < temp <= 20: advice_line = random.choice(CLOTHING_ADVICES["warm"])
        else: advice_line = random.choice(CLOTHING_ADVICES["hot"])
        weather_block = f"{weather_forecast.get('emoji', '')} Прогноз на завтра: {description.capitalize()}, {temp}°C.\n<i>{advice_line}</i>"
    engagement_type = random.choice(list(EVENING_ENGAGEMENT_BLOCKS.keys()))
    engagement_line = random.choice(EVENING_ENGAGEMENT_BLOCKS[engagement_type])
    parts = [day_context_line, weather_block, engagement_line]
    random.shuffle(parts)
    return "\n\n".join(filter(None, [greeting_line] + parts)) + "\n\n"


async def evening_broadcast(user_data_manager: UserDataManager):
    """(Запускается в 20:00) Ставит в очередь задачи на рассылку на завтра."""
    logging.info("Постановка задач на вечернюю рассылку в очередь...")
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow = datetime.now(MOSCOW_TZ) + timedelta(days=1)
    tomorrow_9am = datetime.combine(tomorrow.date(), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)
    intro_text = generate_evening_intro(weather_forecast, target_date=tomorrow)
    
    users_to_notify = await user_data_manager.get_users_for_evening_notify()
    for user_id, group_name in users_to_notify:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=tomorrow.date())
        text_body = "🎉 <b>Завтра занятий нет!</b> Можно как следует отдохнуть."
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text_body = f"<b>Ваше расписание на завтра:</b>\n\n{format_schedule_text(schedule_info)}"
        text = f"{intro_text}{text_body}{UNSUBSCRIBE_FOOTER}"
        send_message_task.send(user_id, text) 
        TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc() 
    logging.info(f"Задачи на вечернюю рассылку для {len(users_to_notify)} пользователей поставлены в очередь.")


async def morning_summary_broadcast(user_data_manager: UserDataManager):
    """(Запускается в 8:00) Ставит в очередь задачи на утреннюю рассылку."""
    logging.info("Постановка задач на утреннюю рассылку в очередь...")
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today = datetime.now(MOSCOW_TZ)
    today_9am = datetime.combine(today.date(), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)
    
    greeting_line = random.choice(MORNING_GREETINGS)
    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast['temperature'])
        description = weather_forecast.get('description', '').lower()
        weather_block = f"За окном сейчас {description.capitalize()}, {temp}°C."

    intro_text = f"{greeting_line}\n{weather_block}\n"
    
    users_to_notify = await user_data_manager.get_users_for_morning_summary()
    for user_id, group_name in users_to_notify:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today.date())
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"{intro_text}\n<b>Ваше расписание на сегодня:</b>\n\n{format_schedule_text(schedule_info)}{UNSUBSCRIBE_FOOTER}"
            send_message_task.send(user_id, text) 
            TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc() 
    logging.info(f"Задачи на утреннюю рассылку для {len(users_to_notify)} пользователей поставлены в очередь.")


async def lesson_reminders_planner(scheduler: AsyncIOScheduler, user_data_manager: UserDataManager):
    """(Запускается в 6:00) Планирует отложенные задачи для напоминаний о парах, используя DateTrigger."""
    logging.info("Запуск планировщика напоминаний о парах...")
    today = datetime.now(MOSCOW_TZ).date()
    users_to_plan = await user_data_manager.get_users_for_lesson_reminders()

    if not users_to_plan:
        logging.info("Планировщик напоминаний: нет пользователей для планирования.")
        return

    for user_id, group_name in users_to_plan:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today)
        if not (schedule_info and not schedule_info.get('error') and schedule_info.get('lessons')):
            continue
        
        try:
            lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        except (ValueError, KeyError) as e:
            logging.warning(f"Пропуск планирования для {group_name} из-за ошибки времени: {e}")
            continue
        
        # Напоминание за 20 минут до первой пары
        if lessons:
            first_lesson = lessons[0]
            try:
                start_time_obj = datetime.strptime(first_lesson['start_time_raw'], '%H:%M').time()
                naive_dt = datetime.combine(today, start_time_obj)
                reminder_dt = MOSCOW_TZ.localize(naive_dt) - timedelta(minutes=20)
                if reminder_dt > datetime.now(MOSCOW_TZ):
                    job_id = f"reminder_{user_id}_{today.isoformat()}_first"
                    scheduler.add_job(send_lesson_reminder_task.send, trigger=DateTrigger(run_date=reminder_dt),
                                      args=(user_id, first_lesson, "first", None), id=job_id, replace_existing=True)
            except (ValueError, KeyError) as e:
                logging.warning(f"Ошибка планирования напоминания о первой паре для user_id={user_id}: {e}")

        # Напоминания в начале каждого перерыва
        for i, lesson in enumerate(lessons):
            try:
                end_time_obj = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                naive_dt = datetime.combine(today, end_time_obj)
                reminder_dt = MOSCOW_TZ.localize(naive_dt)
                
                if reminder_dt > datetime.now(MOSCOW_TZ):
                    is_last_lesson = (i == len(lessons) - 1)
                    reminder_type = "final" if is_last_lesson else "break"
                    next_lesson = lessons[i+1] if not is_last_lesson else None
                    break_duration = None
                    if next_lesson:
                        next_start_time_obj = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
                        break_duration = int((datetime.combine(today, next_start_time_obj) - datetime.combine(today, end_time_obj)).total_seconds() / 60)
                    
                    job_id = f"reminder_{user_id}_{today.isoformat()}_{lesson['end_time_raw']}"
                    scheduler.add_job(send_lesson_reminder_task.send, trigger=DateTrigger(run_date=reminder_dt),
                                      args=(user_id, next_lesson, reminder_type, break_duration), id=job_id, replace_existing=True)
            except (ValueError, KeyError) as e:
                 logging.warning(f"Ошибка планирования напоминания в перерыве для user_id={user_id}: {e}")
    
    logging.info(f"Планирование напоминаний завершено для {len(users_to_plan)} пользователей.")
    
    
async def monitor_schedule_changes(user_data_manager: UserDataManager, redis_client: Redis):
    """Проверяет изменения в расписании и ставит в очередь уведомления."""
    logging.info("Проверка изменений в расписании...")
    old_hash_bytes = await redis_client.get(REDIS_SCHEDULE_HASH_KEY)
    old_hash = old_hash_bytes.decode() if old_hash_bytes else ""
    new_schedule_data = await fetch_and_parse_all_schedules()

    if not new_schedule_data:
        logging.error("Не удалось получить расписание с сервера вуза.")
        return
        
    new_hash = new_schedule_data.get('__current_xml_hash__')
    if new_hash and old_hash != new_hash:
        logging.info(f"Обнаружены изменения в расписании! Старый хеш: {old_hash}, Новый: {new_hash}")
        await redis_client.set(REDIS_SCHEDULE_HASH_KEY, new_hash)
        
        new_manager = TimetableManager(new_schedule_data, redis_client)
        await new_manager.save_to_cache()
        global global_timetable_manager_instance
        global_timetable_manager_instance = new_manager
        
        all_users = await user_data_manager.get_all_user_ids()
        message_text = "❗️ <b>ВНИМАНИЕ! Обновление расписания!</b>\n\nРасписание в боте было обновлено. Пожалуйста, проверьте актуальное расписание своей группы."
        for user_id in all_users:
            send_message_task.send(user_id, message_text)
            TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc() 
    else:
        logging.info("Изменений в расписании не обнаружено.")

async def collect_db_metrics(user_data_manager: UserDataManager):
    """Периодически собирает метрики из БД и обновляет Prometheus Gauges."""
    try:
        logging.info("Сбор метрик из базы данных...")
        total_users = await user_data_manager.get_total_users_count()
        subscribed_users = await user_data_manager.get_subscribed_users_count()
        
        USERS_TOTAL.set(total_users)
        SUBSCRIBED_USERS.set(subscribed_users)
        logging.info(f"Метрики обновлены: total_users={total_users}, subscribed_users={subscribed_users}")
    except Exception as e:
        logging.error(f"Ошибка при сборе метрик из БД: {e}")


def setup_scheduler(bot: Bot, manager: TimetableManager, user_data_manager: UserDataManager, redis_client: Redis) -> AsyncIOScheduler:
    """Настраивает и возвращает экземпляр планировщика с задачами."""
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))
    
    global global_timetable_manager_instance
    global_timetable_manager_instance = manager

    scheduler.add_job(evening_broadcast, 'cron', hour=20, minute=0, args=[user_data_manager])
    scheduler.add_job(morning_summary_broadcast, 'cron', hour=8, minute=0, args=[user_data_manager])
    
    scheduler.add_job(lesson_reminders_planner, 'cron', hour=6, minute=0, args=[scheduler, user_data_manager])
    
    # Служебные задачи
    scheduler.add_job(monitor_schedule_changes, 'interval', minutes=CHECK_INTERVAL_MINUTES, args=[user_data_manager, redis_client])
    scheduler.add_job(collect_db_metrics, 'interval', minutes=1, args=[user_data_manager]) 
    
    return scheduler