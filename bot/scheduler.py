import logging
import random
from datetime import datetime, timedelta, time
from typing import Dict, Any, List

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from redis.asyncio.client import Redis

from bot.utils import format_schedule_text
from core.config import (
    MOSCOW_TZ, CHECK_INTERVAL_MINUTES,
    REDIS_SCHEDULE_HASH_KEY, OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS
)
from core.manager import TimetableManager
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
    # Контекст для "завтра"
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


def generate_evening_intro(
    weather_forecast: Dict[str, Any] | None, 
    target_date: datetime
) -> str:
    """Генерирует уникальную вечернюю подводку с фокусом на завтрашнем дне."""
    weekday = target_date.weekday()
    
    greeting_line = random.choice(EVENING_GREETINGS)
    day_context_line = random.choice(DAY_OF_WEEK_CONTEXT.get(weekday, [""]))
    
    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast['temperature'])
        description = weather_forecast.get('description', '').lower()
        emoji = weather_forecast.get("emoji", "")
        main_weather_key = weather_forecast.get('main_weather_key', 'default')

        observation_line = random.choice(WEATHER_OBSERVATIONS.get(main_weather_key, WEATHER_OBSERVATIONS["default"])).format(description=description)
        
        advice_line = ""
        if temp <= 0: advice_line = random.choice(CLOTHING_ADVICES["cold"]).format(temp_feels_like=temp-5)
        elif 0 < temp <= 12: advice_line = random.choice(CLOTHING_ADVICES["cool"])
        elif 12 < temp <= 20: advice_line = random.choice(CLOTHING_ADVICES["warm"])
        else: advice_line = random.choice(CLOTHING_ADVICES["hot"])

        weather_block = f"{emoji} {observation_line}\n<i>{advice_line}</i>"
        weather_block += f"\n\n<b>Прогноз на утро:</b> {description.capitalize()}, {temp}°C."

    engagement_type = random.choice(list(EVENING_ENGAGEMENT_BLOCKS.keys()))
    engagement_line = random.choice(EVENING_ENGAGEMENT_BLOCKS[engagement_type])
    
    parts = [day_context_line, weather_block, engagement_line]
    random.shuffle(parts)
    
    final_intro = "\n\n".join(filter(None, [greeting_line] + parts))
    
    return f"{final_intro}\n\n"


async def evening_broadcast(bot: Bot, user_data_manager: UserDataManager):
    """(Запускается в 20:00) Рассылает расписание на завтра."""
    logging.info("Запуск вечерней рассылки...")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow = datetime.now(MOSCOW_TZ) + timedelta(days=1)
    tomorrow_9am = datetime.combine(tomorrow.date(), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)
    
    intro_text = generate_evening_intro(weather_forecast, target_date=tomorrow)

    users_to_notify = await user_data_manager.get_users_for_evening_notify()
    if not users_to_notify:
        logging.info("Вечерняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=tomorrow.date())
        
        # Отправляем, даже если пар нет, чтобы сообщить об этом
        text_body = ""
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
             text_body = f"<b>Ваше расписание на завтра:</b>\n\n{format_schedule_text(schedule_info)}"
        else:
             text_body = "🎉 <b>Завтра занятий нет!</b> Можно как следует отдохнуть."

        text = f"{intro_text}{text_body}{UNSUBSCRIBE_FOOTER}"
        try:
            await bot.send_message(user_id, text, disable_web_page_preview=True)
        except Exception as e:
            logging.error(f"Ошибка отправки вечернего расписания для user_id={user_id}: {e}")
    
    logging.info(f"Вечерняя рассылка завершена. Обработано пользователей: {len(users_to_notify)}")

async def morning_summary_broadcast(bot: Bot, user_data_manager: UserDataManager):
    """(Запускается в 8:00) Рассылает расписание на сегодня."""
    logging.info("Запуск утренней рассылки-сводки...")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today = datetime.now(MOSCOW_TZ)
    today_9am = datetime.combine(today.date(), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)
    
    # Можно создать отдельную функцию для утреннего интро или использовать общую
    greeting_line = random.choice(MORNING_GREETINGS)
    weather_block = ""
    if weather_forecast:
        temp = int(weather_forecast['temperature'])
        description = weather_forecast.get('description', '').lower()
        emoji = weather_forecast.get("emoji", "")
        weather_block = f"{emoji} За окном сейчас {description}, {temp}°C."

    intro_text = f"{greeting_line}\n{weather_block}\n"

    users_to_notify = await user_data_manager.get_users_for_morning_summary()
    if not users_to_notify:
        logging.info("Утренняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today.date())
        
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"{intro_text}\n<b>Ваше расписание на сегодня:</b>\n\n{format_schedule_text(schedule_info)}{UNSUBSCRIBE_FOOTER}"
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
    users_to_plan = await user_data_manager.get_users_for_lesson_reminders()

    if not users_to_plan:
        logging.info("Планировщик напоминаний: нет пользователей для планирования.")
        return
        
    logging.info(f"Найдено {len(users_to_plan)} пользователей для планирования напоминаний.")

    for user_id, group_name in users_to_plan:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today)
        if not schedule_info or 'error' in schedule_info or not schedule_info.get('lessons'):
            continue
        
        try:
            lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        except (ValueError, KeyError) as e:
            logging.warning(f"Некорректный формат времени в расписании для группы {group_name}. Пропуск планирования. Ошибка: {e}")
            continue
        
        if lessons:
            first_lesson = lessons[0]
            try:
                start_time_obj = datetime.strptime(first_lesson['start_time_raw'], '%H:%M').time()
                reminder_datetime = datetime.combine(today, start_time_obj, MOSCOW_TZ) - timedelta(minutes=20)
                if reminder_datetime > datetime.now(MOSCOW_TZ):
                    scheduler.add_job(send_lesson_reminder, trigger=DateTrigger(run_date=reminder_datetime), args=(bot, user_id, first_lesson, "first", None), id=f"lr_{user_id}_{today}_first", replace_existing=True)
            except (ValueError, KeyError) as e:
                logging.warning(f"Ошибка планирования напоминания о первой паре для user_id={user_id}: {e}")

        for i, lesson in enumerate(lessons):
            try:
                end_time_obj = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                reminder_datetime = datetime.combine(today, end_time_obj, MOSCOW_TZ)
                
                is_last_lesson = (i == len(lessons) - 1)
                reminder_type = "final" if is_last_lesson else "break"
                next_lesson = lessons[i+1] if not is_last_lesson else None
                break_duration = None
                if not is_last_lesson:
                    next_start_time_obj = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
                    break_duration = int((datetime.combine(today, next_start_time_obj) - datetime.combine(today, end_time_obj)).total_seconds() / 60)
                
                if reminder_datetime > datetime.now(MOSCOW_TZ):
                    job_id = f"lr_{user_id}_{today}_{lesson['end_time_raw']}"
                    scheduler.add_job(send_lesson_reminder, trigger=DateTrigger(run_date=reminder_datetime), args=(bot, user_id, next_lesson, reminder_type, break_duration), id=job_id, replace_existing=True)
            except (ValueError, KeyError) as e:
                 logging.warning(f"Ошибка планирования напоминания в перерыве для user_id={user_id}: {e}")
    
    logging.info(f"Планирование напоминаний о парах завершено.")


async def send_lesson_reminder(bot: Bot, user_id: int, lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None):
    try:
        text = ""
        if reminder_type == "first" and lesson:
            greetings = ["Первая пара через 20 минут!", "Скоро начало, не опаздывайте!", "Готовимся к первой паре!", "Через 20 минут начинается магия знаний... или просто первая пара."]
            text = f"🔔 <b>{random.choice(greetings)}</b>\n\n"
        elif reminder_type == "break" and lesson:
            next_lesson_time = lesson.get('time', 'N/A').split('-')[0].strip()
            
            if break_duration and break_duration >= 40:
                break_ideas = ["Можно успеть полноценно пообедать!", "Отличный шанс сходить в столовую или выпить кофе не спеша.", "Время для большого отдыха. Можно даже успеть подготовиться к следующей паре."]
                break_text = f"У вас большой перерыв {break_duration} минут до {next_lesson_time}. {random.choice(break_ideas)}"
            elif break_duration and break_duration >= 15:
                break_ideas = ["Время выпить чаю.", "Можно немного размяться и проветрить голову.", "Передышка перед следующим рывком. Успехов!"]
                break_text = f"Перерыв {break_duration} минут до {next_lesson_time}. {random.choice(break_ideas)}"
            else:
                break_ideas = ["Успейте дойти до следующей аудитории.", "Короткая передышка, чтобы собраться с мыслями."]
                break_text = random.choice(break_ideas)
            
            text = f"✅ <b>Пара закончилась!</b>\n{break_text}\n\n"
            text += f"☕️ <b>Следующая пара:</b>\n"
        elif reminder_type == "final":
            final_phrases = ["Пары на сегодня всё! Можно отдыхать.", "Учебный день окончен. Хорошего вечера!", "Наконец-то свобода! Увидимся завтра.", "Вы великолепны! Пары закончились."]
            text = f"🎉 <b>{random.choice(final_phrases)}</b>"
            text += UNSUBSCRIBE_FOOTER
            await bot.send_message(user_id, text, disable_web_page_preview=True)
            return
        else:
            return

        if lesson:
            text += f"<b>{lesson.get('subject', 'N/A')}</b> ({lesson.get('type', 'N/A')}) в <b>{lesson.get('time', 'N/A')}</b>\n"
            info_parts = []
            if room := lesson.get('room'): info_parts.append(f"📍 {room}")
            if teachers := lesson.get('teachers'): info_parts.append(f"<i>с {teachers}</i>")
            if info_parts: text += " ".join(info_parts)
        
        text += UNSUBSCRIBE_FOOTER
        await bot.send_message(user_id, text, disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания о паре для user_id={user_id}: {e}")


async def monitor_schedule_changes(bot: Bot, user_data_manager: UserDataManager, redis_client: Redis):
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
        
        new_manager_instance = TimetableManager(new_schedule_data, redis_client)
        await new_manager_instance.save_to_cache()
        
        global global_timetable_manager_instance
        global_timetable_manager_instance = new_manager_instance
        logging.info("Глобальный TimetableManager успешно обновлен новым расписанием.")
        
        all_users = await user_data_manager.get_all_user_ids()
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
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))
    
    global global_timetable_manager_instance
    global_timetable_manager_instance = manager

    scheduler.add_job(evening_broadcast, 'cron', hour=20, minute=0, args=[bot, user_data_manager])
    scheduler.add_job(morning_summary_broadcast, 'cron', hour=8, minute=0, args=[bot, user_data_manager])
    scheduler.add_job(lesson_reminders_planner, 'cron', hour=6, minute=0, args=[bot, scheduler, user_data_manager])
    
    scheduler.add_job(
        monitor_schedule_changes,
        trigger='interval',
        minutes=CHECK_INTERVAL_MINUTES,
        args=[bot, user_data_manager, redis_client]
    )
    
    return scheduler