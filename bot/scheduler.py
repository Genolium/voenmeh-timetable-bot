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
    format_schedule_text, generate_evening_intro, generate_morning_intro, get_footer_with_promo
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
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
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
    intro_text = generate_evening_intro(weather_forecast, target_date=tomorrow)
    
    users_to_notify = await user_data_manager.get_users_for_evening_notify()
    if not users_to_notify:
        logger.info("Вечерняя рассылка: нет пользователей для уведомления.")
        return

    for user_id, group_name in users_to_notify:
        schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=tomorrow.date())
        has_lessons = bool(schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'))
        
        text_body = f"<b>Ваше расписание на завтра:</b>\n\n{format_schedule_text(schedule_info)}" if has_lessons else "🎉 <b>Завтра занятий нет!</b>"
        text = f"{intro_text}{text_body}{get_footer_with_promo()}"
        
        send_message_task.send(user_id, text)
        TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()

async def morning_summary_broadcast(user_data_manager: UserDataManager, timetable_manager: TimetableManager):
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
        schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=today.date())
        if schedule_info and not schedule_info.get('error') and schedule_info.get('lessons'):
            text = f"{intro_text}\n<b>Ваше расписание на сегодня:</b>\n\n{format_schedule_text(schedule_info)}{get_footer_with_promo()}"
            send_message_task.send(user_id, text)
            TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()

async def lesson_reminders_planner(scheduler: AsyncIOScheduler, user_data_manager: UserDataManager, timetable_manager: TimetableManager):
    now_in_moscow = datetime.now(MOSCOW_TZ)
    today = now_in_moscow.date()
    logger.info(f"Запуск планировщика напоминаний о парах для даты {today.isoformat()}")

    users_to_plan = await user_data_manager.get_users_for_lesson_reminders()
    if not users_to_plan:
        return

    for user_id, group_name, reminder_time in users_to_plan:
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
                
                # Планируем всегда: если время прошло, ставим на ближайшую секунду
                run_at = reminder_dt if reminder_dt >= now_in_moscow else now_in_moscow + timedelta(seconds=1)
                scheduler.add_job(
                    send_lesson_reminder_task.send,
                    trigger=DateTrigger(run_date=run_at),
                    args=(user_id, lessons[0], "first", None, reminder_time),
                    id=f"reminder_{user_id}_{today.isoformat()}_first",
                    replace_existing=True,
                )
            except (ValueError, KeyError) as e:
                logger.warning(f"Ошибка планирования напоминания о первой паре для user_id={user_id}: {e}")

        for i, lesson in enumerate(lessons):
            try:
                end_time_obj = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                reminder_dt = MOSCOW_TZ.localize(datetime.combine(today, end_time_obj))
                
                run_at = reminder_dt if reminder_dt >= now_in_moscow else now_in_moscow + timedelta(seconds=1)
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
        if not user or not user.group or not user.lesson_reminders:
            return
        today = datetime.now(MOSCOW_TZ).date()
        schedule_info = await timetable_manager.get_schedule_for_day(user.group, target_date=today)
        if not (schedule_info and not schedule_info.get('error') and schedule_info.get('lessons')):
            return
        # Сортируем пары
        try:
            lessons = sorted(schedule_info['lessons'], key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        except (ValueError, KeyError):
            return
        now_in_moscow = datetime.now(MOSCOW_TZ)
        # Первая пара с учётом времени напоминания
        if lessons:
            try:
                start_time_obj = datetime.strptime(lessons[0]['start_time_raw'], '%H:%M').time()
                start_dt = MOSCOW_TZ.localize(datetime.combine(today, start_time_obj))
                reminder_dt = start_dt - timedelta(minutes=(user.reminder_time_minutes or 20))
                run_at = reminder_dt if reminder_dt >= now_in_moscow else now_in_moscow + timedelta(seconds=1)
                scheduler.add_job(
                    send_lesson_reminder_task.send,
                    trigger=DateTrigger(run_date=run_at),
                    args=(user_id, lessons[0], "first", None, user.reminder_time_minutes),
                    id=f"reminder_{user_id}_{today.isoformat()}_first",
                    replace_existing=True,
                )
            except Exception:
                pass
        # Перерывы/конец
        for i, lesson in enumerate(lessons):
            try:
                end_time_obj = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()
                reminder_dt = MOSCOW_TZ.localize(datetime.combine(today, end_time_obj))
                run_at = reminder_dt if reminder_dt >= now_in_moscow else now_in_moscow + timedelta(seconds=1)
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

async def warm_top_groups_images(user_data_manager: UserDataManager, timetable_manager: TimetableManager, redis_client: Redis):
    try:
        cache = ImageCacheManager(redis_client, cache_ttl_hours=24)
        # Топ-10 групп по пользователям
        try:
            top = await user_data_manager.get_top_groups(limit=10)
            top_groups = [g for g, _ in top if g]
        except Exception:
            top_groups = []
        # Если БД не дала топ, возьмём первые 10 ключей расписаний
        if not top_groups:
            top_groups = list(k for k in timetable_manager._schedules.keys())[:10]
        if not top_groups:
            return
        today = datetime.now(MOSCOW_TZ).date()
        week_key_name = timetable_manager.get_week_type(today)
        if not week_key_name:
            return
        week_key, week_name = week_key_name
        
        # Подготавливаем список групп для генерации
        groups_to_generate = []
        for group in top_groups:
            cache_key = f"{group}_{week_key}"
            if await cache.is_cached(cache_key):
                continue
            # Redis-лок на генерацию конкретного ключа, чтобы избежать дубликатов
            gen_lock_key = f"image_gen_lock:warm:{cache_key}"
            lock_acquired = False
            try:
                lock_acquired = await redis_client.set(gen_lock_key, "1", nx=True, ex=120)
            except Exception:
                pass
            if not lock_acquired:
                continue
            full_schedule = timetable_manager._schedules.get(group.upper(), {})
            week_schedule = full_schedule.get(week_key, {})
            # Путь для временного файла
            from core.config import MEDIA_PATH
            output_dir = MEDIA_PATH / "generated"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{cache_key}.png"
            groups_to_generate.append((cache_key, week_schedule, week_name, group, redis_client))
        
        if not groups_to_generate:
            logger.info("Все изображения уже в кэше, генерация не требуется")
            return
        
        total_groups = len(groups_to_generate)
        logger.info(f"Начинаю генерацию изображений для {total_groups} групп")
        print_progress_bar(0, total_groups, "Генерация изображений", f"0/{total_groups} групп")
        
        # Генерируем изображения с прогресс-баром
        completed = 0
        for i, (cache_key, week_schedule, week_name, group, redis_client) in enumerate(groups_to_generate):
            try:
                await generate_and_cache(cache_key, week_schedule, week_name, group, redis_client)
                completed += 1
                print_progress_bar(completed, total_groups, "Генерация изображений", f"{completed}/{total_groups} групп")
            except Exception as e:
                logger.warning(f"Ошибка генерации для группы {group}: {e}")
                print_progress_bar(completed, total_groups, "Генерация изображений", f"{completed}/{total_groups} групп (ошибка)")
        
        logger.info(f"Генерация изображений завершена: {completed}/{total_groups} групп")
    except Exception as e:
        logger.warning(f"warm_top_groups_images failed: {e}")

async def generate_full_schedule_images(user_data_manager: UserDataManager, timetable_manager: TimetableManager, redis_client: Redis):
    """
    Генерирует изображения расписания для всех групп раз в неделю.
    Запускается в 4 утра в ночь с субботы на воскресенье.
    Генерирует расписание для обеих недель (чётной и нечётной).
    """
    try:
        logger.info("🔄 Начинаю генерацию полного расписания для всех групп")
        
        # Получаем все доступные группы
        all_groups = list(timetable_manager._schedules.keys())
        if not all_groups:
            logger.warning("⚠️ Нет доступных групп для генерации расписания")
            return
        
        # Создаем директорию для изображений
        from core.config import MEDIA_PATH
        output_dir = MEDIA_PATH / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        successful = 0
        failed = 0
        total_size_mb = 0
        
        # Генерируем для обеих недель
        week_types = [
            ("odd", "Нечётная неделя"),
            ("even", "Чётная неделя")
        ]
        
        logger.info(f"📊 Генерирую расписание для {len(all_groups)} групп, обеих недель")
        
        for week_key, week_name in week_types:
            logger.info(f"📅 Генерация для {week_name}")
            
            for i, group in enumerate(all_groups, 1):
                try:
                    # Получаем расписание для группы
                    full_schedule = timetable_manager._schedules.get(group, {})
                    week_schedule = full_schedule.get(week_key, {})
                    
                    if not week_schedule:
                        logger.warning(f"⚠️ Нет расписания для группы {group} на неделю {week_key}")
                        failed += 1
                        continue
                    
                    # Формируем имя файла
                    cache_key = f"{group}_{week_key}"
                    output_path = output_dir / f"{cache_key}.png"
                    
                    # Генерируем изображение
                    success = await generate_schedule_image(
                        schedule_data=week_schedule,
                        week_type=week_name,
                        group=group,
                        output_path=str(output_path)
                    )
                    
                    if success and os.path.exists(output_path):
                        successful += 1
                        file_size = os.path.getsize(output_path) / (1024 * 1024)  # Размер в МБ
                        total_size_mb += file_size
                        
                        # Сохраняем в кэш Redis
                        try:
                            with open(output_path, 'rb') as f:
                                image_bytes = f.read()
                            await redis_client.set(cache_key, image_bytes, ex=86400)  # Кэш на 24 часа
                            logger.info(f"✅ {group} ({week_name}): изображение сгенерировано ({file_size:.2f} МБ)")
                        except Exception as e:
                            logger.warning(f"⚠️ {group} ({week_name}): не удалось сохранить в кэш: {e}")
                    else:
                        failed += 1
                        logger.error(f"❌ {group} ({week_name}): ошибка генерации изображения")
                        
                except Exception as e:
                    failed += 1
                    logger.error(f"❌ {group} ({week_name}): исключение при генерации: {e}")
        
        # Итоговая статистика
        logger.info("=" * 60)
        logger.info("📊 ИТОГОВАЯ СТАТИСТИКА ГЕНЕРАЦИИ")
        logger.info("=" * 60)
        logger.info(f"✅ Успешно сгенерировано: {successful} изображений")
        logger.info(f"❌ Ошибок: {failed} изображений")
        logger.info(f"📁 Общий размер: {total_size_mb:.2f} МБ")
        if successful > 0:
            logger.info(f"💾 Средний размер изображения: {total_size_mb/successful:.2f} МБ")
        logger.info("🎉 Генерация полного расписания завершена")
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при генерации полного расписания: {e}")
        import traceback
        logger.error(traceback.format_exc())

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
            return
            
        new_hash = new_schedule_data.get('__current_xml_hash__')
        if new_hash != old_hash:
            # Detect changed groups
            changed_groups = []  # Compare schedules
            for group in changed_groups:
                users = await user_data_manager.get_users_by_group(group)
                for user in users:
                    send_message_task.send(user, "Schedule changed for your group!")
            logger.warning(f"ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ В РАСПИСАНИИ! Старый хеш: {old_hash}, Новый: {new_hash}")
            await redis_client.set(REDIS_SCHEDULE_HASH_KEY, new_hash)
            
            new_manager = TimetableManager(new_schedule_data, redis_client)
            await new_manager.save_to_cache()
            
            # Переприсваиваем глобальный экземпляр
            global_timetable_manager_instance = new_manager
            
            # Предпрогрев картинок по топ-группам (в фоне)
            try:
                await warm_top_groups_images(user_data_manager, new_manager, redis_client)
            except Exception:
                pass
            
            all_users = await user_data_manager.get_all_user_ids()
            message_text = "❗️ <b>ВНИМАНИЕ! Обновление расписания!</b>\n\nРасписание в боте было обновлено. Пожалуйста, проверьте актуальное расписание своей группы."
            for user_id in all_users:
                send_message_task.send(user_id, message_text)
                TASKS_SENT_TO_QUEUE.labels(actor_name='send_message_task').inc()
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
        cache = ImageCacheManager(redis_client, cache_ttl_hours=24)
        await cache.cleanup_expired_cache()
    except Exception as e:
        logger.error(f"Ошибка при плановой очистке кэша изображений: {e}")

async def generate_and_cache(cache_key: str, week_schedule: dict, week_name: str, group: str, redis_client: Redis):
    try:
        # Путь для временного файла
        from core.config import MEDIA_PATH
        output_dir = MEDIA_PATH / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{cache_key}.png"

        # Генерируем изображение
        ok = await generate_schedule_image(week_schedule, week_name.split(" ")[0], group, str(output_path))
        if ok and os.path.exists(output_path):
            with open(output_path, 'rb') as f:
                image_bytes = f.read()
            await redis_client.set(cache_key, image_bytes, ex=3600) # Cache for 1 hour
            logger.info(f"Image for {cache_key} generated and cached.")
        else:
            logger.warning(f"Image for {cache_key} generation failed or file not found.")
    except Exception as e:
        logger.warning(f"generate_and_cache failed for {cache_key}: {e}")

async def auto_backup(redis_client: Redis):
    # Backup DB (assume PostgreSQL)
    db_url = os.getenv("DATABASE_URL")
    backup_file = f"db_backup_{datetime.now().strftime('%Y%m%d')}.sql"
    os.system(f"pg_dump {db_url} > {backup_file}")
    
    # Backup schedules from Redis
    from core.config import REDIS_SCHEDULE_CACHE_KEY
    schedules = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)
    with open("schedules_backup.json", "w") as f:
        f.write(schedules.decode() if schedules else "{}")
    logger.info("Auto-backup completed.")

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
    scheduler.add_job(handle_graduated_groups, 'interval', minutes=10, args=[user_data_manager, manager, redis_client]) # Добавляем задачу для обработки выпустившихся групп
    
    # Генерация полного расписания раз в неделю в 4 утра в воскресенье
    scheduler.add_job(generate_full_schedule_images, 'cron', day_of_week='sun', hour=4, minute=0, args=[user_data_manager, manager, redis_client])
    
    # Дополнительные задания можно включать через флаг окружения (по умолчанию выключены для облегчения нагрузки и совместимости с тестами)
    if os.getenv('ENABLE_IMAGE_CACHE_JOBS', '0') in ('1', 'true', 'True'):
        # Ежечасная очистка устаревших изображений из кэша
        scheduler.add_job(cleanup_image_cache, 'cron', minute=5, args=[redis_client])
        # Предпрогрев картинок раз в сутки ночью
        scheduler.add_job(warm_top_groups_images, 'cron', hour=3, minute=15, args=[user_data_manager, manager, redis_client])
    
    return scheduler