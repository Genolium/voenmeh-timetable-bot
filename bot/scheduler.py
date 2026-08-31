import asyncio
import json
import logging
import os
from pathlib import Path
from datetime import datetime
from datetime import datetime as _dt
from datetime import time, timedelta

from aiogram import Bot
from aiogram.types import CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio.client import Redis

from bot.tasks import send_lesson_reminder_task, send_message_task
from bot.text_formatters import (
    format_schedule_text,
    format_teacher_schedule_text,
    generate_evening_intro,
    generate_morning_intro,
    get_footer_with_promo,
)
from core.i18n import i18n
from core.admin_reports import send_daily_reports, send_monthly_reports, send_weekly_reports
from core.config import (
    CHECK_INTERVAL_MINUTES,
    MOSCOW_TZ,
    OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID,
    OPENWEATHERMAP_UNITS,
    REDIS_SCHEDULE_CACHE_KEY,
    REDIS_SCHEDULE_HASH_KEY,
)
from core.db.backups import create_db_backup
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
from core.manager import TimetableManager
from core.metrics import ERRORS_TOTAL, LAST_SCHEDULE_UPDATE_TS, SUBSCRIBED_USERS, TASKS_SENT_TO_QUEUE, USERS_TOTAL
from core.parser import fetch_and_parse_all_schedules
from core.schedule_diff import ScheduleDiffDetector, ScheduleDiffFormatter
from core.semester_settings import SemesterSettingsManager
from core.user_data import UserDataManager
from core.weather_api import WeatherAPI

logger = logging.getLogger(__name__)


def print_progress_bar(
    current: int,
    total: int,
    prefix: str = "Прогресс",
    suffix: str = "",
    length: int = 50,
):
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
    bar = "█" * filled_length + "-" * (length - filled_length)
    percent = f"{100 * current // total}%"
    print(f"\r{prefix} |{bar}| {percent} {suffix}", end="", flush=True)
    if current == total:
        print()  # Новая строка в конце


async def evening_broadcast(user_data_manager: UserDataManager, timetable_manager: TimetableManager):
    tomorrow = datetime.now(MOSCOW_TZ) + timedelta(days=1)
    logger.info(f"Начинаю постановку задач на вечернюю рассылку для даты {tomorrow.date().isoformat()}")

    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    tomorrow_9am = MOSCOW_TZ.localize(datetime.combine(tomorrow.date(), time(9, 0)))
    weather_forecast = await weather_api.get_forecast_for_time(tomorrow_9am)

    current_hour = datetime.now(MOSCOW_TZ).hour
    users_to_notify = await user_data_manager.get_users_for_evening_notify(target_hour=current_hour)
    if not users_to_notify:
        logger.info(f"Вечерняя рассылка ({current_hour}:00): нет пользователей для уведомления.")
        return

    logger.info(f"Вечерняя рассылка ({current_hour}:00): обработка {len(users_to_notify)} пользователей")
    processed_count = 0

    for user_id, group_name, lang, user_type in users_to_notify:
        # user_type уже получен из базы в одном запросе
        if not user_type:
            user_type = "student"

        # Генерируем intro с учетом типа пользователя и языка
        intro_text = generate_evening_intro(weather_forecast, target_date=tomorrow, user_type=user_type, lang=lang)

        # Получаем расписание в зависимости от типа пользователя
        if user_type == "teacher":
            schedule_info = await timetable_manager.get_teacher_schedule(group_name, target_date=tomorrow.date())
        else:
            schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=tomorrow.date())

        has_lessons = bool(schedule_info and not schedule_info.get("error") and schedule_info.get("lessons"))

        # Форматируем расписание в зависимости от типа пользователя
        if has_lessons:
            if user_type == "teacher":
                schedule_text = format_teacher_schedule_text(schedule_info, lang=lang)
                text_body = f"<b>{i18n.get('schedule_teacher_label', lang)}</b>\n\n{schedule_text}" # TODO: Verify label usage or simply header
                # Wait, "Ваше расписание на завтра:" is hardcoded above.
                # I should replace "Ваше расписание на завтра:" with a localized string or just rely on the schedule header.
                # Actually, looking at original code: text_body = f"<b>Ваше расписание на завтра:</b>\n\n{schedule_text}"
                # I don't have a key for "Your schedule for tomorrow".
                # I will use "day_context" or just a generic header if I have one.
                # Let's add simple header or assume formatters handle it. 
                # Formatters return "Teacher Name\nDate..."
                # The text_body wrapper: "Ваше расписание на завтра:" is extra.
                # I will use a simple localized string or just remove it if redundant.
                # Let's check existing locales. I don't have a specific key for this phrase.
                # I'll use a hardcoded dict for now or add to i18n? 
                # Better to reuse existing mechanisms.
                # I'll just use the schedule text directly or add a new key quickly?
                # No, I should stick to what I have. 
                # I'll just remove the wrapper "Ваше расписание на завтра:" because `generate_evening_intro` + `schedule_text` (which has headers) is enough.
                # Or I can use a generic "Here is schedule".
                # Let's look at `evening_greetings_teacher`. It says "Informing about tomorrow schedule". So intro covers it.
                text_body = schedule_text
            else:
                schedule_text = format_schedule_text(schedule_info, lang=lang)
                text_body = schedule_text
        else:
            if user_type == "teacher":
                text_body = i18n.get("fmt_no_lessons", lang) # Using generic no lessons
            else:
                text_body = i18n.get("header_today_no_lessons", lang) # Actually for tomorrow...
                # "header_today_no_lessons" is "No lessons today".
                # I need "No lessons tomorrow". I'll use "fmt_no_lessons" which is "No lessons!".
                text_body = i18n.get("fmt_no_lessons", lang)

        text = f"{intro_text}{text_body}{get_footer_with_promo(lang)}"

        await asyncio.to_thread(send_message_task.send, user_id, text)
        TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
        processed_count += 1

        # Периодически освобождаем event loop для обработки других событий
        if processed_count % 50 == 0:
            await asyncio.sleep(0)
            logger.debug(f"Вечерняя рассылка: обработано {processed_count}/{len(users_to_notify)} пользователей")

    logger.info(f"Вечерняя рассылка: завершено. Обработано {processed_count} пользователей")


async def morning_summary_broadcast(user_data_manager: UserDataManager, timetable_manager: TimetableManager):
    today = datetime.now(MOSCOW_TZ)
    logger.info(f"Начинаю постановку задач на утреннюю рассылку для даты {today.date().isoformat()}")

    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)
    today_9am = MOSCOW_TZ.localize(datetime.combine(today.date(), time(9, 0)))
    weather_forecast = await weather_api.get_forecast_for_time(today_9am)

    current_hour = datetime.now(MOSCOW_TZ).hour
    users_to_notify = await user_data_manager.get_users_for_morning_summary(target_hour=current_hour)
    if not users_to_notify:
        logger.info(f"Утренняя рассылка ({current_hour}:00): нет пользователей для уведомления.")
        return

    logger.info(f"Утренняя рассылка ({current_hour}:00): обработка {len(users_to_notify)} пользователей")
    processed_count = 0

    for user_id, group_name, lang, user_type in users_to_notify:
        # user_type уже получен из базы в одном запросе
        if not user_type:
            user_type = "student"

        # Генерируем intro с учетом типа пользователя и языка
        intro_text = generate_morning_intro(weather_forecast, user_type=user_type, lang=lang)

        # Получаем расписание в зависимости от типа пользователя
        if user_type == "teacher":
            schedule_info = await timetable_manager.get_teacher_schedule(group_name, target_date=today.date())
        else:
            schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=today.date())

        if schedule_info and not schedule_info.get("error") and schedule_info.get("lessons"):
            # Форматируем расписание в зависимости от типа пользователя
            if user_type == "teacher":
                schedule_text = format_teacher_schedule_text(schedule_info, lang=lang)
            else:
                schedule_text = format_schedule_text(schedule_info, lang=lang)

            # "Ваше расписание на сегодня" -> removed or handled by header/intro
            text = f"{intro_text}\n{schedule_text}{get_footer_with_promo(lang)}"
            await asyncio.to_thread(send_message_task.send, user_id, text)
            TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
            processed_count += 1

        # Периодически освобождаем event loop для обработки других событий
        if processed_count % 50 == 0:
            await asyncio.sleep(0)
            logger.debug(f"Утренняя рассылка: обработано {processed_count}/{len(users_to_notify)} пользователей")

    logger.info(f"Утренняя рассылка: завершено. Отправлено сообщений: {processed_count}")


async def minutely_reminders_checker(
    user_data_manager: UserDataManager,
    timetable_manager: TimetableManager,
):
    """
    Проверяет каждую минуту, кому из пользователей нужно отправить напоминание.
    Вместо создания тысяч DateTrigger'ов, просто проверяем каждую минуту актуальное состояние.
    """
    now = datetime.now(MOSCOW_TZ)
    today = now.date()
    
    try:
        users = await user_data_manager.get_users_for_lesson_reminders()
        if not users:
            return

        for user_id, group_name, reminder_time, lang in users:
            try:
                schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=today)
                if not (schedule_info and not schedule_info.get("error") and schedule_info.get("lessons")):
                    continue

                try:
                    lessons = sorted(
                        schedule_info["lessons"],
                        key=lambda x: datetime.strptime(x["start_time_raw"], "%H:%M").time(),
                    )
                except (ValueError, KeyError):
                    continue

                if not lessons:
                    continue

                # 1. Проверяем напоминание перед началом первой пары
                try:
                    start_time_obj = datetime.strptime(lessons[0]["start_time_raw"], "%H:%M").time()
                    start_dt = MOSCOW_TZ.localize(datetime.combine(today, start_time_obj))
                    reminder_dt = start_dt - timedelta(minutes=reminder_time)
                    
                    # Если наступила ровно та самая минута (проверяем часы и минуты)
                    if reminder_dt.hour == now.hour and reminder_dt.minute == now.minute:
                        await asyncio.to_thread(
                            send_lesson_reminder_task.send,
                            user_id, lessons[0], "first", None, reminder_time, lang
                        )
                        logger.info(f"Отправлено напоминание о первой паре для {user_id}")
                except (ValueError, KeyError):
                    pass

                # 2. Проверяем напоминания об окончании пар и перерывах
                for i, lesson in enumerate(lessons):
                    try:
                        end_time_obj = datetime.strptime(lesson["end_time_raw"], "%H:%M").time()
                        end_dt = MOSCOW_TZ.localize(datetime.combine(today, end_time_obj))
                        
                        # Если наступило время конца пары
                        if end_dt.hour == now.hour and end_dt.minute == now.minute:
                            is_last = i == len(lessons) - 1
                            next_lesson = lessons[i + 1] if not is_last else None
                            break_duration = None
                            
                            if next_lesson:
                                next_start_time_obj = datetime.strptime(next_lesson["start_time_raw"], "%H:%M").time()
                                break_duration = int(
                                    (datetime.combine(today, next_start_time_obj) - datetime.combine(today, end_time_obj)).total_seconds() / 60
                                )
                            
                            await asyncio.to_thread(
                                send_lesson_reminder_task.send,
                                user_id, next_lesson, "final" if is_last else "break", break_duration, None, lang
                            )
                            logger.info(f"Отправлено напоминание об окончании пары для {user_id}")
                    except (ValueError, KeyError):
                        pass
                        
            except Exception as e:
                logger.error(f"Ошибка проверки напоминаний для {user_id}: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка в minutely_reminders_checker: {e}")

async def monitor_schedule_changes(user_data_manager: UserDataManager, redis_client: Redis, bot: Bot):
    logger.info("Проверка изменений в расписании...")

    global global_timetable_manager_instance  # Оставляем для возможности переприсвоения

    old_hash = (await redis_client.get(REDIS_SCHEDULE_HASH_KEY) or b"").decode()
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

        new_hash = new_schedule_data.get("__current_xml_hash__")
        if new_hash != old_hash:
            logger.warning(f"ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ В РАСПИСАНИИ! Старый хеш: {old_hash}, Новый: {new_hash}")
            await redis_client.set(REDIS_SCHEDULE_HASH_KEY, new_hash)

            # Создаем новый менеджер с обновленными данными
            new_manager = TimetableManager(new_schedule_data, redis_client)
            new_manager.set_semester_settings_manager(SemesterSettingsManager(user_data_manager.async_session_maker))
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
                    await asyncio.to_thread(send_message_task.send, admin_id, admin_message)
                    TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
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
            ts = _dt.now(MOSCOW_TZ).strftime("%Y%m%d_%H%M%S")
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
        try:
            backup_path = await create_db_backup(output_dir=Path("/app/data"))
            logger.info("Database backup created: %s", backup_path)
            # Clean up tmp file after verifying creation
            if backup_path.exists():
                backup_path.unlink()
                logger.info("Temporary database backup file deleted: %s", backup_path)
        except Exception as exc:
            logger.error("Exception during database backup: %s", exc)
            ERRORS_TOTAL.labels(source="db_backup").inc()

        # Backup schedules from Redis
        from core.config import REDIS_SCHEDULE_CACHE_KEY

        schedules = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)

        if schedules:
            try:
                # Try to decompress the data (it's likely gzip-compressed)
                import gzip
                import json
                import pickle

                try:
                    # First try to decompress as gzip + pickle (the format used by TimetableManager)
                    decompressed = gzip.decompress(schedules)
                    schedule_data = pickle.loads(decompressed)
                    logger.info("Successfully decompressed schedule data using gzip+pickle")
                except (gzip.BadGzipFile, pickle.UnpicklingError):
                    try:
                        # Fallback: try as plain JSON
                        schedule_data = json.loads(schedules.decode("utf-8"))
                        logger.info("Successfully parsed schedule data as plain JSON")
                    except UnicodeDecodeError:
                        # Last resort: try to decode with error handling
                        schedule_data = json.loads(schedules.decode("utf-8", errors="replace"))
                        logger.warning("Successfully parsed schedule data with error replacement")

                # Write the decompressed data as readable JSON
                backup_file = f"schedules_backup_{datetime.now(MOSCOW_TZ).strftime('%Y%m%d_%H%M%S')}.json"
                with open(backup_file, "w", encoding="utf-8") as f:
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
    new_manager: TimetableManager,
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
        from core.audit_manager import AuditManager

        audit_manager = AuditManager(user_data_manager.session_factory)
        today = date.today()
        dates_to_check = [today + timedelta(days=i) for i in range(7)]  # Проверяем неделю вперед

        total_notifications_sent = 0
        groups_with_changes = set()  # Отслеживаем группы с изменениями
        processed_groups_count = 0

        for group_name, user_ids in groups_to_users.items():
            processed_groups_count += 1
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
                        new_schedule_data=new_schedule,
                    )

                    # Если есть изменения, отправляем уведомления
                    if diff.has_changes():
                        group_has_changes = True
                        groups_with_changes.add(group_name)  # Добавляем группу в список с изменениями
                        
                        # Сохраняем аудит-лог изменений в базе данных
                        await audit_manager.record_changes(
                            target_name=group_name,
                            schedule_date=check_date,
                            changes=diff.changes,
                            target_type="group",
                        )

                        message = ScheduleDiffFormatter.format_group_diff(diff)

                        if message:
                            # Отправляем уведомление всем пользователям группы
                            for user_id in user_ids:
                                await asyncio.to_thread(send_message_task.send, user_id, message)
                                TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
                                total_notifications_sent += 1

                            logger.info(
                                f"Отправлены дифф-уведомления для группы {group_name} на {check_date}: {len(user_ids)} пользователей"
                            )

                except Exception as e:
                    logger.error(f"Ошибка при сравнении расписания группы {group_name} на {check_date}: {e}")
                    continue

            if not group_has_changes:
                logger.debug(f"Нет изменений в расписании группы {group_name}")

            # Периодически освобождаем event loop для обработки других событий
            if processed_groups_count % 10 == 0:
                await asyncio.sleep(0)
                logger.debug(f"Обработка diff: проверено {processed_groups_count}/{len(groups_to_users)} групп")

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
                    await asyncio.to_thread(send_message_task.send, admin_id, admin_message)
                    TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
            except Exception as e:
                logger.warning(f"Не удалось уведомить администраторов о дифф-уведомлениях: {e}")

    except Exception as e:
        logger.error(f"Критическая ошибка при отправке дифф-уведомлений: {e}")
        import traceback

        logger.error(traceback.format_exc())


async def handle_graduated_groups(
    user_data_manager: UserDataManager,
    timetable_manager: TimetableManager,
    redis_client: Redis,
):
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
        current_groups = {g for g in current_groups if not g.startswith("__")}

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

                # Используем фоновую задачу для отправки уведомлений
                await asyncio.to_thread(send_message_task.send, user_id, message_text)
                TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()

                # Очищаем группу пользователя
                await user_data_manager.set_user_group(user_id, None)

                notified_count += 1
                logger.info(f"✅ Запланировано уведомление пользователя {user_id} о выпуске группы {old_group}")

                # Периодически освобождаем event loop
                if notified_count % 50 == 0:
                    await asyncio.sleep(0)
                    logger.debug(f"Обработка выпускных групп: обработано {notified_count}/{len(affected_users)} пользователей")

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
                "notified_users": notified_count,
            }
            await redis_client.set(stats_key, json.dumps(stats_data, ensure_ascii=False), ex=86400)
        except Exception as e:
            logger.warning(f"Не удалось сохранить статистику: {e}")

    except Exception as e:
        logger.error(f"❌ Ошибка при обработке выпустившихся групп: {e}")
        import traceback

        logger.error(traceback.format_exc())


def setup_scheduler(
    bot: Bot,
    manager: TimetableManager,
    user_data_manager: UserDataManager,
    redis_client: Redis,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=str(MOSCOW_TZ))

    global global_timetable_manager_instance
    global_timetable_manager_instance = manager

    scheduler.add_job(evening_broadcast, "cron", hour="16-23", minute=0, args=[user_data_manager, manager])
    scheduler.add_job(
        morning_summary_broadcast,
        "cron",
        hour="6-12",
        minute=0,
        args=[user_data_manager, manager],
    )
    scheduler.add_job(
        minutely_reminders_checker,
        "cron",
        minute="*",  # Запускать каждую минуту
        args=[user_data_manager, manager],
    )
    scheduler.add_job(
        monitor_schedule_changes,
        "interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[user_data_manager, redis_client, bot],
    )
    scheduler.add_job(collect_db_metrics, "interval", minutes=5, args=[user_data_manager])
    scheduler.add_job(backup_current_schedule, "cron", hour="*/6", args=[redis_client])
    scheduler.add_job(auto_backup, "cron", hour=2, args=[redis_client])
    scheduler.add_job(
        handle_graduated_groups,
        "cron",
        hour=3,
        args=[user_data_manager, manager, redis_client],
    )

    # Задачи для отправки отчётов администраторам
    scheduler.add_job(send_daily_reports, "cron", hour=9, minute=0, args=[bot, user_data_manager])  # Ежедневно в 9:00
    scheduler.add_job(
        send_weekly_reports,
        "cron",
        hour=9,
        minute=0,
        day_of_week="mon",
        args=[bot, user_data_manager],
    )  # Еженедельно по понедельникам в 9:00
    scheduler.add_job(
        send_monthly_reports,
        "cron",
        hour=9,
        minute=0,
        day=1,
        args=[bot, user_data_manager],
    )  # Ежемесячно 1-го числа в 9:00

    # Дополнительные задания можно включать через флаг окружения (по умолчанию выключены для облегчения нагрузки и совместимости с тестами)
    if os.getenv("ENABLE_IMAGE_CACHE_JOBS", "0") in ("1", "true", "True"):
        # Ежечасная очистка устаревших изображений из кэша
        scheduler.add_job(cleanup_image_cache, "cron", minute=5, args=[redis_client])

    return scheduler
