import logging
import random
from datetime import datetime, timedelta, time
from redis.asyncio.client import Redis
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from core.manager import TimetableManager
from core.user_data import UserDataManager
from core.config import (
    MOSCOW_TZ, CHECK_INTERVAL_MINUTES,
    REDIS_SCHEDULE_HASH_KEY, OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS
)
from bot.utils import format_schedule_text
from core.parser import fetch_and_parse_all_schedules
from core.weather_api import WeatherAPI

# Глобальный экземпляр для обновления в реальном времени при мониторинге
global_timetable_manager_instance = None

UNSUBSCRIBE_FOOTER = "\n\n<tg-spoiler><i>Отключить эту рассылку можно в «⚙️ Настройки»</i></tg-spoiler>"


def generate_creative_weather_intro(weather_forecast: dict | None, forecast_for: str) -> str:
    """
    Генерирует умную, максимально разнообразную и не повторяющуюся подводку к расписанию,
    включая краткую сводку погоды.
    """
    if not weather_forecast:
        return f"🤷‍♀️ К сожалению, не удалось получить прогноз погоды на {forecast_for}. Но расписание всегда под рукой!\n\n"

    temp = int(weather_forecast['temperature'])
    description = weather_forecast.get('description', '').lower()
    wind_speed = round(weather_forecast.get('wind_speed', 0))
    main_weather_key = weather_forecast.get('main_weather_key', 'default')

    # --- 1. Формируем список советов и наблюдений ---
    advices = []
    
    # --- Основные наблюдения о погоде (самый большой блок фраз) ---
    observations = {
        "clear": [
            "☀️ Отличные новости! {forecast_for_capital} нас ждет ясный и солнечный день.",
            "☀️ Похоже, {forecast_for} будет прекрасная погода! Не забудьте насладиться солнцем.",
            "☀️ Идеальный день для прогулки после пар! {forecast_for_capital} будет солнечно.",
            "☀️ Редкое явление для наших широт! {forecast_for_capital} обещает быть солнечным, ловите момент!"
        ],
        "rain": [
            "🌧️ Кажется, {forecast_for} понадобятся зонты! Синоптики обещают {description}.",
            "🌧️ Питер покажет свой классический характер: {forecast_for} будет дождливо. Не забудьте зонтик!",
            "🌧️ За окном будет {description}. Самое время для горячего чая между парами!",
        ],
        "snow": [
            "❄️ {forecast_for_capital} ожидается снег! Одевайтесь теплее и наслаждайтесь волшебной атмосферой.",
            "❄️ Нас заметает! {forecast_for_capital} будет {description}, готовьтесь к хрусту под ногами.",
            "❄️ Настоящая зимняя сказка! Осторожнее на ступеньках, может быть скользко.",
        ],
        "clouds": [
            "☁️ На небе {forecast_for} будут облака, но это не помешает нашим планам.",
            "☁️ Солнце {forecast_for} будет играть в прятки за облаками. Вполне комфортно!",
            "☁️ Ожидается переменная облачность, без погодных сюрпризов.",
        ],
        "overcast": [
            "🌥️ Нас ждет пасмурный день. Хороший повод взять с собой термос с чем-нибудь горячим!",
            "🌥️ Небо {forecast_for} будет затянуто тучами, но осадков не обещают. Просто серый, но атмосферный день.",
            "🌥️ Солнце решило взять выходной. {forecast_for_capital} будет пасмурно.",
        ],
        "thunderstorm": [
            "⛈️ Ого, {forecast_for} возможна гроза! Лучше переждать непогоду в стенах вуза.",
            "⛈️ Будьте осторожны: {forecast_for} прогнозируют грозу. Зарядите пауэрбанк на всякий случай!",
        ],
        "fog": [
            "🌫️ Город утонет в тумане, смотрите под ноги по дороге на пары!",
            "🌫️ Видимость {forecast_for} будет так себе — синоптики передают густой туман.",
        ]
    }
    
    observation_templates = observations.get(main_weather_key)
    if observation_templates:
        advices.append(random.choice(observation_templates).format(
            forecast_for=forecast_for, 
            forecast_for_capital=forecast_for.capitalize(),
            description=description
        ))

    # --- Дополнительные контекстные советы ---
    
    # Совет по одежде, зависящий от погоды и температуры
    clothing_advices = []
    if temp <= 0:
        clothing_advices.append(random.choice(["не забудьте шапку и перчатки", "наденьте дополнительный слой одежды", "шарф сегодня точно пригодится"]))
    elif 0 < temp <= 10:
        if main_weather_key == "rain":
            clothing_advices.append(random.choice(["лучше выбрать непромокаемую куртку", "водонепроницаемая обувь будет очень кстати"]))
        else:
            clothing_advices.append(random.choice(["куртка или толстовка — ваш лучший друг", "свитер под куртку — отличный выбор"]))
    elif 10 < temp <= 18:
        clothing_advices.append("можно одеться полегче")
    elif temp > 18:
        if main_weather_key == "clear":
            clothing_advices.append("одевайтесь как можно легче и пейте больше воды")
        else:
            clothing_advices.append("на улице тепло, но может быть душно")
            
    # Совет по аксессуарам (очки/зонт)
    if main_weather_key == "clear" and temp > 15:
        clothing_advices.append("и захватите солнечные очки 😎")

    if clothing_advices:
        # Добавляем случайную фразу-связку
        connector = random.choice(["Кстати,", "Небольшой совет:"])
        full_clothing_advice = f"{connector} {', '.join(clothing_advices)}."
        advices.append(full_clothing_advice)

    # Совет про ветер (только если он заметный)
    if wind_speed >= 10:
        advices.append("🌬️ Осторожно, ожидается сильный ветер!")
    elif wind_speed >= 5:
        advices.append("💨 Будет ветрено, держите конспекты крепче!")

    # --- 2. Собираем интро-блок ---
    intro_block = ""
    if advices:
        intro_block = "\n".join(advices)
    else:
        # Запасной вариант, если не нашлось ВООБЩЕ ничего (маловероятно)
        neutral_wishes = [
            "Желаем продуктивного дня!",
            "Удачного учебного дня!",
            "Пусть все пары пройдут легко!",
            "Отличного настроения и легких пар!"
        ]
        intro_block = random.choice(neutral_wishes)
        
    # --- 3. Формируем отдельную строку с точным прогнозом ---
    summary_header = "Прогноз на утро:" if forecast_for == "завтра" else "Прогноз на сегодня:"
    summary = (f"<b>{summary_header}</b> {description.capitalize()}, {temp}°C, ветер {wind_speed} м/с.")

    # --- 4. Собираем финальное сообщение ---
    final_text = f"{intro_block}\n\n{summary}\n\n"
    
    return final_text


async def evening_broadcast(bot: Bot, user_data_manager: UserDataManager):
    """(Запускается в 20:00) Рассылает расписание на завтра."""
    logging.info("Запуск вечерней рассылки...")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow_9am = datetime.combine(datetime.now(MOSCOW_TZ).date() + timedelta(days=1), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)
    
    weather_intro_text = generate_creative_weather_intro(weather_forecast, forecast_for="завтра")

    try:
        users_to_notify = await user_data_manager.get_users_for_evening_notify()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для вечерней рассылки из БД: {e}")
        return

    if not users_to_notify:
        logging.info("Вечерняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        tomorrow = datetime.now(MOSCOW_TZ).date() + timedelta(days=1)
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=tomorrow)
        
        # Отправляем только если есть пары
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"👋 <b>Добрый вечер!</b>\n\n{weather_intro_text}<b>Ваше расписание на завтра:</b>\n\n{format_schedule_text(schedule_info)}{UNSUBSCRIBE_FOOTER}"
            try:
                await bot.send_message(user_id, text, disable_web_page_preview=True)
            except Exception as e:
                logging.error(f"Ошибка отправки вечернего расписания для user_id={user_id}: {e}")
    
    logging.info(f"Вечерняя рассылка завершена. Обработано пользователей: {len(users_to_notify)}")


async def morning_summary_broadcast(bot: Bot, user_data_manager: UserDataManager):
    """(Запускается в 8:00) Рассылает расписание на сегодня."""
    logging.info("Запуск утренней рассылки-сводки...")
    
    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today_9am = datetime.combine(datetime.now(MOSCOW_TZ).date(), time(9, 0), tzinfo=MOSCOW_TZ)
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)
    
    weather_intro_text = generate_creative_weather_intro(weather_forecast, forecast_for="сегодня")

    try:
        users_to_notify = await user_data_manager.get_users_for_morning_summary()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей для утренней сводки из БД: {e}")
        return

    if not users_to_notify:
        logging.info("Утренняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        today = datetime.now(MOSCOW_TZ).date()
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today)
        
        # Отправляем только если есть пары
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"☀️ <b>Доброе утро!</b>\n\n{weather_intro_text}<b>Ваше расписание на сегодня:</b>\n\n{format_schedule_text(schedule_info)}{UNSUBSCRIBE_FOOTER}"
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

    if not users_to_plan:
        logging.info("Планировщик напоминаний: нет пользователей для планирования.")
        return

    for user_id, group_name in users_to_plan:
        schedule_info = global_timetable_manager_instance.get_schedule_for_day(group_name, target_date=today)
        if not schedule_info or 'error' in schedule_info or not schedule_info.get('lessons'):
            continue
        
        try:
            lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        except (ValueError, KeyError) as e:
            logging.warning(f"Некорректный формат времени в расписании для группы {group_name}. Пропуск планирования. Ошибка: {e}")
            continue
        
        # Напоминание за 20 минут до первой пары
        if lessons:
            first_lesson = lessons[0]
            try:
                start_time_obj = datetime.strptime(first_lesson['start_time_raw'], '%H:%M').time()
                reminder_datetime = datetime.combine(today, start_time_obj, MOSCOW_TZ) - timedelta(minutes=20)
                if reminder_datetime > datetime.now(MOSCOW_TZ):
                    job_id = f"lesson_reminder_{user_id}_{today.isoformat()}_first"
                    scheduler.add_job(
                        send_lesson_reminder,
                        trigger=DateTrigger(run_date=reminder_datetime),
                        args=(bot, user_id, first_lesson, "first"),
                        id=job_id,
                        replace_existing=True
                    )
            except (ValueError, KeyError) as e:
                logging.warning(f"Ошибка планирования напоминания о первой паре для user_id={user_id}: {e}")

        # Напоминания в начале каждого перерыва
        for i in range(len(lessons) - 1):
            current_lesson = lessons[i]
            next_lesson = lessons[i+1]
            try:
                end_time_obj = datetime.strptime(current_lesson['end_time_raw'], '%H:%M').time()
                reminder_datetime = datetime.combine(today, end_time_obj, MOSCOW_TZ)
                
                if reminder_datetime > datetime.now(MOSCOW_TZ):
                    job_id = f"lesson_reminder_{user_id}_{today.isoformat()}_{next_lesson['start_time_raw']}"
                    scheduler.add_job(
                        send_lesson_reminder,
                        trigger=DateTrigger(run_date=reminder_datetime),
                        args=(bot, user_id, next_lesson, "break"),
                        id=job_id,
                        replace_existing=True
                    )
            except (ValueError, KeyError) as e:
                logging.warning(f"Ошибка планирования напоминания в перерыве для user_id={user_id}: {e}")
    
    logging.info(f"Планирование напоминаний о парах завершено. Обработано пользователей: {len(users_to_plan)}")


async def send_lesson_reminder(bot: Bot, user_id: int, lesson: dict | None, reminder_type: str, break_duration: int | None):
    """Отправляет индивидуальное напоминание о начале перерыва или об окончании дня."""
    try:
        if reminder_type == "first":
            # Это напоминание приходит за 20 минут до начала, с ним все в порядке.
            text = f"🔔 <b>Первая пара через 20 минут!</b>\n\n"
        
        elif reminder_type == "break":
            # Это напоминание приходит в момент окончания предыдущей пары.
            # Формируем текст о НАЧАЛЕ перерыва.
            next_lesson_time = lesson.get('time', 'N/A').split('-')[0].strip()
            
            text = f"✅ <b>Пара закончилась!</b>\n"
            if break_duration and break_duration > 0:
                 text += f"У вас перерыв {break_duration} минут до {next_lesson_time}.\n\n"
            else:
                 text += "\n" # Если не удалось посчитать длительность, просто делаем отступ.

            text += f"☕️ <b>Следующая пара:</b>\n"

        elif reminder_type == "final":
            # Это напоминание приходит в момент окончания последней пары.
            text = "✅ <b>Пары на сегодня закончились!</b>\n\nМожно отдыхать. Хорошего вечера!"
            text += UNSUBSCRIBE_FOOTER
            await bot.send_message(user_id, text, disable_web_page_preview=True)
            return # Выходим, так как информация об уроке не нужна
        
        else:
            return # Неизвестный тип напоминания

        if lesson:
            text += f"<b>{lesson.get('subject', 'N/A')}</b> ({lesson.get('type', 'N/A')}) в <b>{lesson.get('time', 'N/A')}</b>\n"
            
            info_parts = []
            room = lesson.get('room')
            if room and room.strip() != 'N/A':
                info_parts.append(f"📍 {room}")
            teachers = lesson.get('teachers')
            if teachers:
                info_parts.append(f"<i>с {teachers}</i>")
            
            if info_parts:
                text += " ".join(info_parts)
        
        text += UNSUBSCRIBE_FOOTER
             
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
        
        # Создаем новый менеджер с актуальным расписанием
        new_manager_instance = TimetableManager(redis_client=redis_client) # Инициализируем без данных, он сам загрузит из кэша
        await new_manager_instance.load_schedule(force_reload=True, schedule_data=new_schedule_data)
        
        # Обновляем глобальный экземпляр
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
    """Настраивает и возвращает экземпляр планировщика с задачами."""
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