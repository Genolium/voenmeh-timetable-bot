import asyncio
import html
import json
import logging
import os
from datetime import datetime
from datetime import datetime as _dt
from datetime import time, timedelta
from pathlib import Path
from typing import Any, cast

from aiogram import Bot
from aiogram.types import CallbackQuery, FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio.client import Redis

from bot.tasks import send_lesson_reminder_task, send_message_task
from bot.text_formatters import (
    format_schedule_text,
    format_teacher_schedule_text,
    generate_evening_intro,
    generate_morning_intro,
    get_footer_with_promo,
    has_reportable_lessons,
)
from core.admin_reports import send_daily_reports, send_monthly_reports, send_weekly_reports
from core.config import (
    ADMIN_IDS,
    CHECK_INTERVAL_MINUTES,
    MOSCOW_TZ,
    OPENWEATHERMAP_API_KEY,
    OPENWEATHERMAP_CITY_ID,
    OPENWEATHERMAP_UNITS,
    REDIS_LAST_UPDATE_KEY,
    REDIS_SCHEDULE_CACHE_KEY,
    REDIS_SCHEDULE_HASH_KEY,
)
from core.db.backups import compress_file_gzip, create_db_backup
from core.i18n import i18n
from core.image_cache_manager import ImageCacheManager
from core.manager import TimetableManager, refresh_schedule_cache_ttl
from core.metrics import (
    ERRORS_TOTAL,
    LAST_SCHEDULE_UPDATE_TS,
    SUBSCRIBED_USERS,
    TASKS_SENT_TO_QUEUE,
    USERS_TOTAL,
    collect_system_metrics,
)
from core.parser import fetch_and_parse_all_schedules, is_unchanged_result
from core.schedule_diff import ScheduleDiffDetector, ScheduleDiffFormatter
from core.semester_settings import SemesterSettingsManager
from core.user_data import UserDataManager
from core.weather_api import WeatherAPI

logger = logging.getLogger(__name__)

GRADUATED_MISS_PREFIX = "graduated_miss:"
GRADUATED_MISS_THRESHOLD = 2
GRADUATED_MISS_TTL_SECONDS = 7 * 86400

GOST_LAB_PROMO_PREFIX = "promo:gost_lab_footer:"
GOST_LAB_PROMO_COOLDOWN_SECONDS = 8 * 86400


class TimetableManagerRef:
    """Mutable reference to the active TimetableManager.

    Scheduler jobs receive this reference instead of a concrete manager instance
    so that schedule updates are visible to notifications without mutating the
    previous manager object.
    """

    def __init__(self, manager: TimetableManager):
        self._manager = manager

    def get(self) -> TimetableManager:
        return self._manager

    def set(self, manager: TimetableManager) -> None:
        self._manager = manager


def _resolve_manager(manager_or_ref: Any) -> TimetableManager:
    """Accept either a TimetableManager or a TimetableManagerRef."""
    if isinstance(manager_or_ref, TimetableManagerRef):
        return manager_or_ref.get()
    return cast(TimetableManager, manager_or_ref)


manager_ref: TimetableManagerRef | None = None
global_timetable_manager_instance: TimetableManager | None = None


def print_progress_bar(
    current: int,
    total: int,
    prefix: str = "Прогресс",
    suffix: str = "",
    length: int = 50,
) -> None:
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


async def _safe_group_chat_rows(user_data_manager: Any, method_name: str, *args: Any) -> list[Any]:
    session_factory = getattr(user_data_manager, "async_session_maker", None)
    if not session_factory or not hasattr(session_factory, "__call__"):
        return []
    try:
        from core.group_chat_manager import GroupChatManager

        gcm = GroupChatManager(session_factory)
        rows = await getattr(gcm, method_name)(*args)
        return list(rows) if isinstance(rows, (list, tuple)) else []
    except Exception as e:
        logger.debug("GroupChatManager.%s skipped: %s", method_name, e)
        return []


async def _safe_apply_user_rules(
    user_data_manager: Any,
    timetable_manager: Any,
    user_id: int,
    group_name: str,
    schedule_info: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if user_id < 0 or not schedule_info or schedule_info.get("error"):
        return schedule_info
    session_factory = getattr(user_data_manager, "async_session_maker", None)
    if not session_factory:
        return schedule_info
    try:
        from core.user_schedule_rules import UserScheduleRulesManager, apply_rules_to_lessons

        redis_client = getattr(timetable_manager, "redis", None)
        rules = await UserScheduleRulesManager(session_factory, redis_client).get_rules(user_id, group_name)
        if not rules:
            return schedule_info
        updated = dict(schedule_info)
        week_key = str(updated.get("week_key") or ("odd" if "Неч" in str(updated.get("week_name", "")) else "even"))
        updated["lessons"] = apply_rules_to_lessons(
            list(updated.get("lessons") or []),
            rules,
            day_name=str(updated.get("day_name") or ""),
            week_key=week_key,
        )
        return updated
    except Exception:
        return schedule_info


async def _fetch_window_weather(
    weather_api: WeatherAPI,
    target_dt: datetime,
    lessons: list[dict[str, Any]],
    lang: str,
    cache: dict[tuple[int, int, str], dict[str, Any] | None],
) -> dict[str, Any] | None:
    from core.summary_advisor import analyze_lessons_window

    win = analyze_lessons_window(lessons)
    start_h = int(win["start_hour"])
    end_h = int(win["end_hour"])
    key = (start_h // 3, end_h // 3, lang)
    if key in cache:
        return cache[key]
    try:
        if type(weather_api).__name__ == "WeatherAPI" and hasattr(weather_api, "get_day_weather_window"):
            result = await weather_api.get_day_weather_window(
                target_dt.date(),
                start_hour=start_h,
                end_hour=end_h,
                lang=lang,
            )
        else:
            fallback_dt = MOSCOW_TZ.localize(datetime.combine(target_dt.date(), time(start_h, 0)))
            result = await weather_api.get_forecast_for_time(fallback_dt, lang=lang)
    except Exception as e:
        logger.warning(f"Weather forecast failed ({lang}, {start_h}-{end_h}): {e}")
        result = None
    cache[key] = result
    return result


async def _should_show_gost_lab_promo(
    timetable_manager: Any,
    user_id: int,
    user_type: str,
    lessons: list[dict[str, Any]],
) -> bool:
    """Показывает футер про ГОСТ-отчёт студентам в ЛС при наличии лаб/практ/курс не чаще 1 раза в 8 дней."""
    if user_id <= 0 or user_type == "teacher" or not has_reportable_lessons(lessons):
        return False
    redis_client = getattr(timetable_manager, "redis", None)
    if redis_client is None:
        return False
    try:
        claimed = await redis_client.set(
            f"{GOST_LAB_PROMO_PREFIX}{user_id}",
            "1",
            nx=True,
            ex=GOST_LAB_PROMO_COOLDOWN_SECONDS,
        )
        return bool(claimed)
    except Exception:
        return False


async def evening_broadcast(user_data_manager: UserDataManager, timetable_manager: TimetableManager) -> None:
    timetable_manager = _resolve_manager(timetable_manager)
    tomorrow = datetime.now(MOSCOW_TZ) + timedelta(days=1)
    logger.info(f"Начинаю постановку задач на вечернюю рассылку для даты {tomorrow.date().isoformat()}")

    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)

    now = datetime.now(MOSCOW_TZ)
    target_time = now.strftime("%H:%M")
    users_to_notify = list(await user_data_manager.get_users_for_evening_notify(target_time=target_time) or [])
    group_chats = await _safe_group_chat_rows(user_data_manager, "get_chats_for_evening", target_time)
    targets = [*users_to_notify, *group_chats]
    if not targets:
        logger.info(f"Вечерняя рассылка ({target_time}): нет пользователей для уведомления.")
        return

    weather_cache: dict[tuple[int, int, str], dict[str, Any] | None] = {}
    logger.info(f"Вечерняя рассылка ({target_time}): обработка {len(targets)} получателей")
    processed_count = 0

    for user_id, group_name, lang, user_type in targets:
        if not user_type:
            user_type = "student"

        if user_type == "teacher":
            schedule_info = await timetable_manager.get_teacher_schedule(group_name, target_date=tomorrow.date())
        else:
            schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=tomorrow.date())

        schedule_info = await _safe_apply_user_rules(user_data_manager, timetable_manager, user_id, group_name, schedule_info)
        lessons = list((schedule_info or {}).get("lessons") or [])
        has_lessons = bool(schedule_info and not schedule_info.get("error") and lessons)

        user_forecast = await _fetch_window_weather(weather_api, tomorrow, lessons, lang or "ru", weather_cache)
        intro_text = generate_evening_intro(
            user_forecast,
            target_date=tomorrow,
            user_type=user_type,
            lang=lang,
            lessons=lessons if has_lessons else None,
        )

        if has_lessons and schedule_info:
            if user_type == "teacher":
                text_body = format_teacher_schedule_text(schedule_info, lang=lang)
            else:
                text_body = format_schedule_text(schedule_info, lang=lang)
        else:
            text_body = i18n.get("fmt_no_lessons", lang)

        show_gost_promo = await _should_show_gost_lab_promo(
            timetable_manager, user_id, user_type, lessons if has_lessons else []
        )
        footer = (
            "\n\n<tg-spoiler><i>Настроить рассылку чата: /group</i></tg-spoiler>"
            if user_id < 0
            else get_footer_with_promo(lang, show_gost_lab_promo=show_gost_promo)
        )
        text = f"{intro_text}{text_body}{footer}"

        await asyncio.to_thread(send_message_task.send, user_id, text)
        TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
        processed_count += 1

        if processed_count % 50 == 0:
            await asyncio.sleep(0)
            logger.debug(f"Вечерняя рассылка: обработано {processed_count}/{len(targets)} получателей")

    logger.info(f"Вечерняя рассылка: завершено. Обработано {processed_count} получателей")


async def morning_summary_broadcast(user_data_manager: UserDataManager, timetable_manager: TimetableManager) -> None:
    timetable_manager = _resolve_manager(timetable_manager)
    today = datetime.now(MOSCOW_TZ)
    logger.info(f"Начинаю постановку задач на утреннюю рассылку для даты {today.date().isoformat()}")

    weather_api = WeatherAPI(OPENWEATHERMAP_API_KEY, OPENWEATHERMAP_CITY_ID, OPENWEATHERMAP_UNITS)

    now = datetime.now(MOSCOW_TZ)
    target_time = now.strftime("%H:%M")
    users_to_notify = list(await user_data_manager.get_users_for_morning_summary(target_time=target_time) or [])
    group_chats = await _safe_group_chat_rows(user_data_manager, "get_chats_for_morning", target_time)
    targets = [*users_to_notify, *group_chats]
    if not targets:
        logger.info(f"Утренняя рассылка ({target_time}): нет пользователей для уведомления.")
        return

    weather_cache: dict[tuple[int, int, str], dict[str, Any] | None] = {}
    logger.info(f"Утренняя рассылка ({target_time}): обработка {len(targets)} получателей")
    processed_count = 0

    for user_id, group_name, lang, user_type in targets:
        if not user_type:
            user_type = "student"

        if user_type == "teacher":
            schedule_info = await timetable_manager.get_teacher_schedule(group_name, target_date=today.date())
        else:
            schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=today.date())

        schedule_info = await _safe_apply_user_rules(user_data_manager, timetable_manager, user_id, group_name, schedule_info)
        lessons = list((schedule_info or {}).get("lessons") or [])
        user_forecast = await _fetch_window_weather(weather_api, today, lessons, lang or "ru", weather_cache)
        intro_text = generate_morning_intro(
            user_forecast,
            user_type=user_type,
            lang=lang,
            lessons=lessons or None,
            target_date=today.date(),
        )

        if schedule_info and not schedule_info.get("error"):
            if user_type == "teacher":
                schedule_text = format_teacher_schedule_text(schedule_info, lang=lang)
            else:
                schedule_text = format_schedule_text(schedule_info, lang=lang)
        else:
            schedule_text = i18n.get("fmt_no_lessons", lang)

        footer = (
            "\n\n<tg-spoiler><i>Настроить рассылку чата: /group</i></tg-spoiler>"
            if user_id < 0
            else get_footer_with_promo(lang)
        )
        text = f"{intro_text}\n{schedule_text}{footer}"
        await asyncio.to_thread(send_message_task.send, user_id, text)
        TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
        processed_count += 1

        if processed_count % 50 == 0:
            await asyncio.sleep(0)
            logger.debug(f"Утренняя рассылка: обработано {processed_count}/{len(targets)} получателей")

    logger.info(f"Утренняя рассылка: завершено. Отправлено сообщений: {processed_count}")


async def minutely_reminders_checker(
    user_data_manager: UserDataManager,
    timetable_manager: TimetableManager,
) -> None:
    """
    Проверяет каждую минуту, кому из пользователей нужно отправить напоминание.
    Вместо создания тысяч DateTrigger'ов, просто проверяем каждую минуту актуальное состояние.
    """
    timetable_manager = _resolve_manager(timetable_manager)
    now = datetime.now(MOSCOW_TZ)
    today = now.date()

    try:
        getter = getattr(user_data_manager, "get_users_for_lesson_reminders_with_type", None)
        try:
            users = await getter() if getter else []
            if not isinstance(users, (list, tuple)):
                raise TypeError("typed reminder getter returned a non-sequence")
            users = list(users)
        except (AttributeError, TypeError):
            # Старые/мок-реализации могут не поддерживать расширенный метод.
            users = await user_data_manager.get_users_for_lesson_reminders()
        if not users:
            users = []
        group_chats = await _safe_group_chat_rows(user_data_manager, "get_chats_for_reminders")
        all_targets = [*users, *group_chats]
        if not all_targets:
            return

        # Группируем по (цель, тип), чтобы преподаватель и группа с одинаковым
        # именем не смешивались, а расписание считалось один раз на цель.
        groups_users: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
        for row in all_targets:
            user_id, group_name, reminder_time, lang, *rest = row
            user_type = rest[0] if rest else "student"
            if group_name:
                groups_users.setdefault((group_name, user_type), []).append((user_id, reminder_time, lang))

        for (group_name, user_type), group_user_list in groups_users.items():
            try:
                if user_type == "teacher":
                    schedule_info = await timetable_manager.get_teacher_schedule(group_name, target_date=today)
                else:
                    schedule_info = await timetable_manager.get_schedule_for_day(group_name, target_date=today)
                if not (schedule_info and not schedule_info.get("error") and schedule_info.get("lessons")):
                    continue

                lessons = schedule_info["lessons"]
                if not lessons:
                    continue

                # Сортируем уроки по времени начала (HH:MM сортируется лексикографически)
                sorted_lessons = sorted(lessons, key=lambda x: x.get("start_time_raw", ""))
                first_lesson = sorted_lessons[0]

                # Время начала первой пары
                first_start_raw = first_lesson.get("start_time_raw", "")
                if not first_start_raw or first_start_raw == "N/A":
                    continue
                first_start_time = datetime.strptime(first_start_raw, "%H:%M").time()
                first_start_dt = MOSCOW_TZ.localize(datetime.combine(today, first_start_time))

                # Вычисляем события окончания пар для текущей минуты
                end_events = []
                for i, lesson in enumerate(sorted_lessons):
                    end_raw = lesson.get("end_time_raw", "")
                    if not end_raw or end_raw == "N/A":
                        continue
                    try:
                        end_time = datetime.strptime(end_raw, "%H:%M").time()
                        end_dt = MOSCOW_TZ.localize(datetime.combine(today, end_time))
                        if end_dt.hour == now.hour and end_dt.minute == now.minute:
                            is_last = i == len(sorted_lessons) - 1
                            next_lesson = sorted_lessons[i + 1] if not is_last else None
                            break_dur = None
                            if next_lesson:
                                n_start_raw = next_lesson.get("start_time_raw", "")
                                if n_start_raw and n_start_raw != "N/A":
                                    n_start = datetime.strptime(n_start_raw, "%H:%M").time()
                                    break_dur = int(
                                        (datetime.combine(today, n_start) - datetime.combine(today, end_time)).total_seconds()
                                        / 60
                                    )
                            end_events.append((next_lesson, "final" if is_last else "break", break_dur))
                    except (ValueError, KeyError):
                        pass

                # Отправляем напоминания пользователям группы
                for user_id, reminder_time, lang in group_user_list:
                    # 1. Напоминание перед началом первой пары
                    reminder_dt = first_start_dt - timedelta(minutes=reminder_time)
                    if reminder_dt.hour == now.hour and reminder_dt.minute == now.minute:
                        await asyncio.to_thread(
                            send_lesson_reminder_task.send, user_id, first_lesson, "first", None, reminder_time, lang
                        )
                        logger.info(f"Отправлено напоминание о первой паре для {user_id}")

                    # 2. Напоминания об окончании пар и перерывах
                    for next_lesson, rem_type, break_dur in end_events:
                        await asyncio.to_thread(
                            send_lesson_reminder_task.send, user_id, next_lesson, rem_type, break_dur, None, lang
                        )
                        logger.info(f"Отправлено напоминание об окончании пары для {user_id}")

            except Exception as grp_err:
                logger.error(f"Ошибка проверки напоминаний для группы {group_name}: {grp_err}")

    except Exception as e:
        logger.error(f"Ошибка в minutely_reminders_checker: {e}")


async def monitor_schedule_changes(user_data_manager: UserDataManager, redis_client: Redis, bot: Bot) -> None:
    logger.info("Проверка изменений в расписании...")

    global global_timetable_manager_instance  # Оставляем для возможности переприсвоения

    # Add retries
    attempts = 0
    new_schedule_data = None
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

    if new_schedule_data is None:
        # Бюджет попыток исчерпан: сохраняем прежнее расписание.
        logger.critical("Schedule parse failed after retries; keeping the previous schedule.")
        return

    # For race condition: Use Redis lock
    async with redis_client.lock("timetable_manager_update_lock", timeout=300, blocking_timeout=120):
        old_hash = (await redis_client.get(REDIS_SCHEDULE_HASH_KEY) or b"").decode()
        if is_unchanged_result(new_schedule_data):
            logger.info("Расписание не изменилось (304). Продлеваем TTL кэша.")
            await refresh_schedule_cache_ttl(redis_client)
            updated_at = _dt.now(MOSCOW_TZ).timestamp()
            LAST_SCHEDULE_UPDATE_TS.set(updated_at)
            try:
                await redis_client.set(REDIS_LAST_UPDATE_KEY, str(int(updated_at)))
            except Exception:
                pass
            return

        if not new_schedule_data:
            logger.error("Не удалось получить расписание с сервера вуза.")
            # Проверяем, есть ли актуальные данные в основном кэше
            cached_data = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)
            if not cached_data:
                logger.warning("Основной кэш также пуст. Система работает на резервных копиях.")
                # Здесь можно добавить дополнительные действия при работе на резервных копиях
            return

        assert isinstance(new_schedule_data, dict)
        new_hash = new_schedule_data.get("__current_xml_hash__")
        if new_hash != old_hash:
            logger.warning(f"ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ В РАСПИСАНИИ! Старый хеш: {old_hash}, Новый: {new_hash}")

            old_manager = global_timetable_manager_instance
            # Создаем новый менеджер с обновленными данными
            new_manager = TimetableManager(new_schedule_data, redis_client)
            new_manager.set_semester_settings_manager(SemesterSettingsManager(user_data_manager.async_session_maker))
            await new_manager.save_to_cache()
            if new_hash:
                await redis_client.set(REDIS_SCHEDULE_HASH_KEY, new_hash)

            # Атомарно переключаем активный менеджер: диалоги, планировщик и middleware
            # читают его через TimetableManagerRef/глобальную ссылку.
            global_timetable_manager_instance = new_manager
            if manager_ref is not None:
                manager_ref.set(new_manager)
            changed_groups = None
            if old_manager is not None and old_manager is not new_manager:
                try:
                    changed_groups = await send_schedule_diff_notifications(user_data_manager, old_manager, new_manager)
                except Exception as e:
                    logger.warning(f"Не удалось отправить diff-уведомления: {e}")
            try:
                from core.metrics import LAST_DATA_UPDATE

                LAST_DATA_UPDATE.labels(data_type="schedule").set(datetime.now(MOSCOW_TZ).timestamp())
            except Exception:
                pass

            # Инвалидируем кэш изображений точечно по изменившимся группам.
            try:
                from core.image_cache_manager import invalidate_all_generated_images, invalidate_generated_images_for_groups

                if changed_groups:
                    await invalidate_generated_images_for_groups(redis_client, changed_groups)
                elif changed_groups is None:
                    # Diff не удался — безопаснее очистить всё, чем отдать старое.
                    await invalidate_all_generated_images(redis_client)
            except Exception as e:
                logger.warning(f"Не удалось очистить кэш изображений: {e}")

            # Уведомляем администраторов о обновлении
            try:
                admin_users = await user_data_manager.get_admin_users()
                admin_message = (
                    "🔄 <b>Расписание обновлено</b>\n\n"
                    "Обнаружены изменения в расписании.\n"
                    "Кэш изображений сброшен, новые картинки будут сгенерированы по запросу."
                )
                for admin_id in admin_users:
                    await asyncio.to_thread(send_message_task.send, admin_id, admin_message)
                    TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
            except Exception as e:
                logger.warning(f"Не удалось уведомить администраторов: {e}")
        else:
            logger.info("Изменений в расписании не обнаружено.")
            await refresh_schedule_cache_ttl(redis_client)

        # Успешный парсинг (с изменениями или без) — только теперь freshness честный.
        updated_at = _dt.now(MOSCOW_TZ).timestamp()
        LAST_SCHEDULE_UPDATE_TS.set(updated_at)
        try:
            await redis_client.set(REDIS_LAST_UPDATE_KEY, str(int(updated_at)))
        except Exception:
            pass


# --- Резервные копии расписания ---
BACKUP_PREFIX = "timetable:backup:"
BACKUP_RETENTION_DAYS = 30  # Срок хранения резервных копий (1 месяц)
BACKUP_TTL_SECONDS = BACKUP_RETENTION_DAYS * 86400


async def backup_current_schedule(redis_client: Redis) -> None:
    try:
        await redis_client.get(REDIS_SCHEDULE_HASH_KEY)
        # Сохраняем «снапшот» ключа хеша и дамп данных кэша расписания
        from core.config import REDIS_SCHEDULE_CACHE_KEY

        cached_json = await redis_client.get(REDIS_SCHEDULE_CACHE_KEY)
        if cached_json:
            ts = _dt.now(MOSCOW_TZ).strftime("%Y%m%d_%H%M%S")
            await redis_client.set(f"{BACKUP_PREFIX}{ts}", cached_json, ex=BACKUP_TTL_SECONDS)
            logger.info("Создана резервная копия расписания в Redis: %s (срок хранения: %s дн.)", ts, BACKUP_RETENTION_DAYS)
    except Exception as e:
        logger.error("Ошибка при создании резервной копии расписания: %s", e)


async def collect_db_metrics(user_data_manager: UserDataManager) -> None:
    try:
        total_users = await user_data_manager.get_total_users_count()
        subscribed_users = await user_data_manager.get_subscribed_users_count()
        USERS_TOTAL.set(total_users)
        SUBSCRIBED_USERS.set(subscribed_users)
    except Exception as e:
        logger.error(f"Ошибка при сборе метрик из БД: {e}")


async def cleanup_image_cache(redis_client: Redis) -> None:
    try:
        cache = ImageCacheManager(redis_client, cache_ttl_hours=192)
        await cache.cleanup_expired_cache()
    except Exception as e:
        logger.error(f"Ошибка при плановой очистке кэша изображений: {e}")


# Массовые функции генерации изображений удалены: теплый прогрев, полная генерация, pre-cache helper


async def _send_backup_to_telegram(bot: Bot, backup_path: Path) -> None:
    """Сжимает дамп БД в .sql.gz и отправляет его в BACKUP_CHAT_ID или ADMIN_IDS."""
    backup_chat = (os.getenv("BACKUP_CHAT_ID") or "").strip()
    recipients: list[int | str] = [backup_chat] if backup_chat else list(ADMIN_IDS)
    if not recipients:
        return
    gz_path = compress_file_gzip(backup_path)
    size_kb = max(1, gz_path.stat().st_size // 1024)
    caption = f"📎 Резервная копия БД: <code>{gz_path.name}</code> ({size_kb} KB)"
    for chat_id in recipients:
        try:
            await bot.send_document(chat_id=chat_id, document=FSInputFile(gz_path), caption=caption, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Не удалось отправить бэкап БД в чат %s: %s", chat_id, exc)


async def auto_backup(redis_client: Redis, bot: Bot | None = None) -> None:
    try:
        backup_dir = Path("/app/data/backups") if Path("/app/data").exists() else Path("data/backups")
        backup_dir.mkdir(parents=True, exist_ok=True)

        try:
            backup_path = await create_db_backup(output_dir=backup_dir)
            logger.info("Database backup created: %s (сохраняется на %s дней)", backup_path, BACKUP_RETENTION_DAYS)
            if bot is not None:
                await _send_backup_to_telegram(bot, backup_path)
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

                try:
                    decompressed = gzip.decompress(schedules)
                    schedule_data = json.loads(decompressed.decode("utf-8"))
                    logger.info("Successfully decompressed schedule data using gzip+json")
                except (OSError, gzip.BadGzipFile, json.JSONDecodeError, UnicodeDecodeError):
                    try:
                        schedule_data = json.loads(schedules.decode("utf-8"))
                        logger.info("Successfully parsed schedule data as plain JSON")
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        schedule_data = json.loads(schedules.decode("utf-8", errors="replace"))
                        logger.info("Successfully parsed schedule data as JSON with replacement")

                # Write the decompressed data as readable JSON in backup_dir
                ts = _dt.now(MOSCOW_TZ).strftime("%Y%m%d_%H%M%S")
                backup_file = backup_dir / f"schedules_backup_{ts}.json"
                with open(backup_file, "w", encoding="utf-8") as f:
                    json.dump(schedule_data, f, ensure_ascii=False, indent=2)
                logger.info(f"Schedule backup created: {backup_file}")

            except Exception as e:
                logger.error(f"Failed to process schedule data for backup: {e}")
                # Fallback: save raw data as binary in backup_dir
                ts = _dt.now(MOSCOW_TZ).strftime("%Y%m%d_%H%M%S")
                backup_file = backup_dir / f"schedules_backup_raw_{ts}.bin"
                with open(backup_file, "wb") as f:
                    f.write(schedules)
                logger.info(f"Raw schedule backup created: {backup_file}")
        else:
            logger.info("No schedule data found in Redis cache")

        # Очистка файлов резервных копий старше 30 дней (1 месяц)
        cutoff_ts = _dt.now(MOSCOW_TZ).timestamp() - (BACKUP_RETENTION_DAYS * 86400)
        for old_file in backup_dir.iterdir():
            if old_file.is_file() and old_file.stat().st_mtime < cutoff_ts:
                try:
                    old_file.unlink()
                    logger.info("Удален устаревший файл бэкапа (> 30 дней): %s", old_file)
                except Exception as cleanup_err:
                    logger.warning("Не удалось удалить устаревший бэкап %s: %s", old_file, cleanup_err)

        logger.info("Auto-backup completed successfully")

    except Exception as e:
        logger.error(f"Auto-backup failed with error: {e}")
        import traceback

        logger.error(traceback.format_exc())


async def send_schedule_diff_notifications(
    user_data_manager: UserDataManager,
    old_manager: TimetableManager,
    new_manager: TimetableManager,
) -> Any:
    """
    Отправляет пользователям уведомления только о реальных изменениях в расписании.
    """
    try:
        # Только студенты: у преподавателей в group лежит ФИО, а не группа.
        getter = getattr(user_data_manager, "get_students_with_groups_and_language", None)
        try:
            users_with_groups = list(await getter()) if getter else []
        except TypeError:
            users_with_groups = []
        if not users_with_groups:
            users_with_groups = await user_data_manager.get_all_users_with_groups()
        if not users_with_groups:
            users_with_groups = []
        group_chats = await _safe_group_chat_rows(user_data_manager, "get_chats_for_diff")
        all_diff_targets = [*users_with_groups, *group_chats]
        if not all_diff_targets:
            logger.info("Нет пользователей для отправки дифф-уведомлений")
            return set()

        # Группируем по (группа, язык), чтобы отправить локализованный текст.
        groups_to_users: dict[tuple[str, str], list[int]] = {}
        for row in all_diff_targets:
            user_id, group_name, *rest = row
            lang = rest[0] if rest else "ru"
            if group_name:
                groups_to_users.setdefault((group_name, lang), []).append(user_id)

        # Проверяем изменения для каждой группы на следующие несколько дней
        from datetime import date, timedelta

        from core.audit_manager import AuditManager

        audit_manager = AuditManager(user_data_manager.async_session_maker)
        today = datetime.now(MOSCOW_TZ).date()
        dates_to_check = [today + timedelta(days=i) for i in range(7)]  # Проверяем неделю вперед

        total_notifications_sent = 0
        groups_with_changes = set()  # Отслеживаем группы с изменениями
        processed_groups_count = 0

        for (group_name, lang), user_ids in groups_to_users.items():
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

                        message = ScheduleDiffFormatter.format_group_diff(diff, lang=lang)

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

        return groups_with_changes

    except Exception as e:
        logger.error(f"Критическая ошибка при отправке дифф-уведомлений: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return None


async def handle_graduated_groups(
    user_data_manager: UserDataManager,
    timetable_manager: TimetableManager,
    redis_client: Redis,
) -> None:
    """
    Обрабатывает ситуации, когда группы выпустились и больше не существуют в расписании.
    Уведомляет пользователей и предлагает выбрать новую группу.
    """
    timetable_manager = _resolve_manager(timetable_manager)
    try:
        logger.info("🔍 Проверяю наличие выпустившихся групп...")

        # Получаем только студентов: ФИО преподавателей хранится в том же поле,
        # поэтому использовать get_all_users_with_groups здесь нельзя.
        users_with_groups = await user_data_manager.get_students_with_groups()
        if not users_with_groups:
            logger.info("Нет студентов для проверки")
            return

        # Получаем актуальные группы из расписания
        current_groups = set(timetable_manager._schedules.keys())
        # Исключаем служебные ключи
        current_groups = {g for g in current_groups if not g.startswith("__")}
        if not current_groups:
            logger.warning("Список текущих групп пуст — пропускаем проверку выпустившихся групп.")
            return

        graduated_groups = set()
        affected_users = []

        # Проверяем каждую группу студентов. Одного пропуска недостаточно:
        # источник может вернуть частичный снапшот, поэтому группа считается
        # выпустившейся только после нескольких подтверждённых пропусков.
        for user_id, group_name in users_with_groups:
            miss_key = f"{GRADUATED_MISS_PREFIX}{user_id}:{(group_name or '').upper()}"
            if group_name and group_name.upper() not in current_groups:
                try:
                    raw_misses = await redis_client.incr(miss_key)
                    misses = int(raw_misses)
                    if misses == 1:
                        await redis_client.expire(miss_key, GRADUATED_MISS_TTL_SECONDS)
                except Exception as e:
                    # При сбое Redis безопаснее не трогать профиль пользователя.
                    logger.warning(f"Не удалось проверить пропуск группы для {user_id}: {e}")
                    continue

                if misses < GRADUATED_MISS_THRESHOLD:
                    logger.warning(
                        f"Группа {group_name} отсутствует в расписании (пропуск {misses}/{GRADUATED_MISS_THRESHOLD}); "
                        f"профиль пользователя {user_id} сохранён"
                    )
                    continue

                graduated_groups.add(group_name.upper())
                affected_users.append((user_id, group_name))
            else:
                try:
                    await redis_client.delete(miss_key)
                except Exception:
                    pass

        if not affected_users:
            logger.info("✅ Все группы пользователей актуальны")
            return

        logger.warning(f"⚠️ Обнаружены выпустившиеся группы: {', '.join(graduated_groups)}")
        logger.info(f"📊 Затронуто пользователей: {len(affected_users)}")

        # Получаем список доступных групп для предложения
        available_groups = sorted(list(current_groups))

        notified_count = 0
        for user_id, old_group in affected_users:
            try:
                # Формируем сообщение (экранируем динамические имена групп для HTML)
                safe_old_group = html.escape(str(old_group), quote=False)
                safe_available = ", ".join(html.escape(str(g), quote=False) for g in available_groups[:10])
                message_text = (
                    f"⚠️ <b>Внимание!</b>\n\n"
                    f"Группа <b>{safe_old_group}</b> больше не существует в расписании.\n"
                    f"Возможно, группа выпустилась или была переименована.\n\n"
                    f"Пожалуйста, выберите новую группу:\n"
                    f"<code>/start</code> - для выбора группы\n\n"
                    f"Доступные группы: {safe_available}"
                    + (f"\n... и еще {len(available_groups) - 10} групп" if len(available_groups) > 10 else "")
                )

                # Используем фоновую задачу для отправки уведомлений
                await asyncio.to_thread(send_message_task.send, user_id, message_text)
                TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()

                # Очищаем группу пользователя и счётчик подтверждённых пропусков
                await user_data_manager.set_user_group(user_id, None)
                try:
                    await redis_client.delete(f"{GRADUATED_MISS_PREFIX}{user_id}:{old_group.upper()}")
                except Exception:
                    pass

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
    scheduler = AsyncIOScheduler(
        timezone=str(MOSCOW_TZ), job_defaults={"coalesce": True, "misfire_grace_time": 60, "max_instances": 1}
    )

    global global_timetable_manager_instance, manager_ref
    global_timetable_manager_instance = manager
    manager_ref = TimetableManagerRef(manager)

    scheduler.add_job(evening_broadcast, "cron", hour="16-23", minute="*", args=[user_data_manager, manager_ref])
    scheduler.add_job(
        morning_summary_broadcast,
        "cron",
        hour="6-12",
        minute="*",
        args=[user_data_manager, manager_ref],
    )
    scheduler.add_job(
        minutely_reminders_checker,
        "cron",
        minute="*",  # Запускать каждую минуту
        args=[user_data_manager, manager_ref],
    )
    scheduler.add_job(
        monitor_schedule_changes,
        "interval",
        minutes=CHECK_INTERVAL_MINUTES,
        args=[user_data_manager, redis_client, bot],
    )
    scheduler.add_job(collect_db_metrics, "interval", minutes=5, args=[user_data_manager])
    scheduler.add_job(collect_system_metrics, "interval", minutes=1)
    scheduler.add_job(backup_current_schedule, "cron", hour="*/6", args=[redis_client])
    scheduler.add_job(auto_backup, "cron", hour=2, args=[redis_client, bot])
    scheduler.add_job(
        handle_graduated_groups,
        "cron",
        hour=3,
        args=[user_data_manager, manager_ref, redis_client],
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

    # Фоновый janitor кэша изображений: не блокирует горячую запись.
    scheduler.add_job(cleanup_image_cache, "cron", hour="*/6", minute=5, args=[redis_client])

    return scheduler
