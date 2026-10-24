import asyncio
import html
import logging
import os
import sys
import time
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Message
from aiogram_dialog import DialogManager, StartMode, setup_dialogs
from aiogram_dialog.api.exceptions import UnknownIntent
from aiohttp import ClientTimeout
from dotenv import load_dotenv
from prometheus_client import start_http_server
from pythonjsonlogger.json import JsonFormatter
from redis.asyncio.client import Redis

# --- Импорты диалогов ---
from bot.dialogs.about_menu import about_dialog
from bot.dialogs.admin_menu import admin_dialog, on_cancel_generation
from bot.dialogs.feedback_menu import feedback_dialog
from bot.dialogs.find_menu import find_dialog
from bot.dialogs.main_menu import dialog as main_menu_dialog
from bot.dialogs.schedule_view import (
    on_check_subscription_callback,
    on_inline_back,
    on_send_original_file_callback,
    schedule_dialog,
)
from bot.dialogs.settings_menu import settings_dialog

# --- Импорты состояний ---
from bot.dialogs.states import About, Admin, Feedback, MainMenu, Schedule, SettingsMenu
from bot.handlers.feedback_reply_handler import feedback_reply_router

# --- Импорты бота ---
from bot.handlers.inline_handlers import inline_query_handler
from bot.middlewares.i18n_middleware import I18nMiddleware
from bot.middlewares.logging_middleware import LoggingMiddleware
from bot.middlewares.manager_middleware import ManagerMiddleware
from bot.middlewares.rate_limit_middleware import RateLimitMiddleware
from bot.middlewares.session_middleware import SessionMiddleware
from bot.middlewares.subscription_filter_middleware import SubscriptionFilterMiddleware
from bot.middlewares.user_data_middleware import UserDataMiddleware
from bot.scheduler import setup_scheduler
from core.alert_webhook import run_alert_webhook_server

# --- Импорты ядра ---
from core.bot_identity import set_bot_username
from core.business_alerts import start_business_monitoring
from core.config import ADMIN_IDS, settings
from core.image_generator import shutdown_image_generator
from core.manager import TimetableManager
from core.telemetry import TelemetryHandles, setup_telemetry, shutdown_telemetry
from core.text_utils import normalize_group_name
from core.user_data import UserDataManager


# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
def setup_logging() -> TelemetryHandles | None:
    """Настраивает логирование и подключает OpenTelemetry обработчики при необходимости."""
    telemetry_handles: TelemetryHandles | None = None
    if settings.OTEL_ENABLED:
        telemetry_handles = setup_telemetry(
            service_name=settings.OTEL_SERVICE_NAME,
            environment=settings.OTEL_ENVIRONMENT,
            endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
            headers=settings.OTEL_EXPORTER_OTLP_HEADERS,
        )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s", json_default=str))

    handlers = [stream_handler]
    if telemetry_handles and telemetry_handles.log_handler:
        handlers.append(telemetry_handles.log_handler)

    logging.basicConfig(level=logging.INFO, handlers=handlers)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    return telemetry_handles


async def configure_bot_profile(bot: Bot) -> None:
    """Cache live @username and set Telegram profile descriptions."""
    try:
        me = await bot.get_me()
        set_bot_username(getattr(me, "username", None))
    except Exception:
        logging.exception("Failed to fetch bot username via get_me()")
    ru_description = "Расписание Военмеха в Telegram: пары на сегодня, напоминания и поиск группы."
    ru_short = "Расписание Военмеха в Telegram"
    try:
        await bot.set_my_description(description=ru_description, language_code="ru")
        await bot.set_my_description(description=ru_description)
        await bot.set_my_short_description(short_description=ru_short, language_code="ru")
        await bot.set_my_short_description(short_description=ru_short)
    except Exception:
        logging.exception("Failed to set bot description")


class AdminDialogGuardMiddleware(BaseMiddleware):
    """Block Admin dialog callbacks/messages for anyone outside ADMIN_IDS."""

    async def __call__(self, handler, event, data):
        from bot.dialogs.states import Admin as AdminSG

        user = getattr(event, "from_user", None)
        if user and ADMIN_IDS and user.id in ADMIN_IDS:
            return await handler(event, data)
        dialog_manager = data.get("dialog_manager")
        if dialog_manager is None:
            return await handler(event, data)
        try:
            ctx = dialog_manager.current_context()
        except Exception:
            return await handler(event, data)
        state = getattr(ctx, "state", None) if ctx is not None else None
        group = getattr(state, "group", None)
        if group is AdminSG or (state is not None and str(getattr(state, "state", "")).startswith("Admin")):
            if hasattr(event, "answer"):
                try:
                    await event.answer()
                except Exception:
                    pass
            return None
        return await handler(event, data)


async def set_bot_commands(bot: Bot) -> None:
    """Устанавливает меню команд для пользователей и администраторов на разных языках."""
    from core.i18n import i18n

    await configure_bot_profile(bot)

    # Supported languages
    languages = ["ru", "en", "zh"]
    default_lang = "ru"

    for lang in languages:
        user_commands = [
            BotCommand(command="start", description=i18n.get("cmd_start_desc", lang=lang)),
            BotCommand(command="about", description=i18n.get("cmd_about_desc", lang=lang)),
            BotCommand(command="feedback", description=i18n.get("cmd_feedback_desc", lang=lang)),
        ]

        try:
            # Установка команд для конкретного языка
            await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault(), language_code=lang)

            # Если это дефолтный язык, устанавливаем его как fallback (без language_code)
            if lang == default_lang:
                await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
                logging.info(f"Установлены стандартные команды (fallback и {lang}).")
        except Exception as e:
            logging.error(f"Не удалось установить команды для языка {lang}: {e}")

    if ADMIN_IDS:
        # Для админов используем дефолтный язык (ru)
        lang = default_lang
        admin_commands = [
            BotCommand(command="start", description=i18n.get("cmd_start_desc", lang=lang)),
            BotCommand(command="about", description=i18n.get("cmd_about_desc", lang=lang)),
            BotCommand(command="feedback", description=i18n.get("cmd_feedback_desc", lang=lang)),
            BotCommand(command="admin", description=i18n.get("cmd_admin_desc", lang=lang)),
        ]
        for admin_id in ADMIN_IDS:
            try:
                await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as e:
                logging.error(f"Не удалось установить команды для админа {admin_id}: {e}")
        logging.info(f"Установлены расширенные команды для администраторов: {ADMIN_IDS}")


# --- Обработчики команд ---
async def start_command_handler(message: Message, dialog_manager: DialogManager, command: CommandObject | None = None) -> None:
    user_data_manager = dialog_manager.middleware_data.get("user_data_manager")
    if not isinstance(user_data_manager, UserDataManager):
        logging.error("UserDataManager is not available or has an invalid type in middleware data.")
        return

    if not message.from_user or not hasattr(message.from_user, "id"):
        logging.error("Message.from_user is None or does not have an 'id' attribute.")
        return

    try:
        # Deep link: t.me/bot?start=group_О735Б — предпросмотр без смены сохранённой группы.
        if command and command.args and command.args.startswith("group_"):
            deep_group = normalize_group_name(command.args[len("group_") :])
            timetable_manager = dialog_manager.middleware_data.get("manager")
            if deep_group and timetable_manager and deep_group in timetable_manager._schedules:
                await dialog_manager.start(
                    Schedule.view,
                    data={"group": deep_group},
                    mode=StartMode.RESET_STACK,
                )
                return

        saved_group = await user_data_manager.get_user_group(message.from_user.id)
        if saved_group:
            await dialog_manager.start(Schedule.view, data={"group": saved_group}, mode=StartMode.RESET_STACK)
        else:
            lang_code = str(getattr(message.from_user, "language_code", "") or "").lower()
            if lang_code.startswith("ru"):
                try:
                    await user_data_manager.register_user(
                        user_id=message.from_user.id,
                        username=getattr(message.from_user, "username", None),
                    )
                    await user_data_manager.set_user_language(message.from_user.id, "ru")
                except Exception:
                    logging.exception("Failed to persist ru language during onboarding skip")
                await dialog_manager.start(MainMenu.choose_user_type, mode=StartMode.RESET_STACK)
            else:
                await dialog_manager.start(MainMenu.select_language, mode=StartMode.RESET_STACK)
    except Exception as e:
        logging.error(f"Ошибка при запуске команды /start: {e}", exc_info=True)
        try:
            await message.answer(
                "👋 <b>Добро пожаловать в бот расписания БГТУ «ВОЕНМЕХ»!</b>\n\n"
                "Для начала работы выберите группу через /start или воспользуйтесь поиском.",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def about_command_handler(message: Message, dialog_manager: DialogManager) -> None:
    try:
        await dialog_manager.start(About.page_1, mode=StartMode.RESET_STACK)
    except Exception as e:
        logging.error(f"Не удалось открыть раздел 'О боте': {e}")
        try:
            await message.answer(
                "ℹ️ Раздел 'О боте' временно недоступен из-за сетевой ошибки отправки медиа.\n"
                "Повторите попытку позже или посетите канал: https://t.me/voenmeh404"
            )
        except Exception:
            pass


async def feedback_command_handler(message: Message, dialog_manager: DialogManager) -> None:
    await dialog_manager.start(Feedback.enter_feedback, mode=StartMode.RESET_STACK)


async def override_command_handler(message: Message, command: CommandObject, dialog_manager: DialogManager) -> None:
    """Admin-only correction: /override group|YYYY-MM-DD|HH:MM-HH:MM|subject|room|type."""
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        return
    raw = (command.args or "").strip()
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) != 6:
        await message.answer(
            "Формат: <code>/override группа|YYYY-MM-DD|HH:MM-HH:MM|предмет|аудитория|тип</code>",
            parse_mode="HTML",
        )
        return
    group, date_str, time_str, subject, room, lesson_type = parts
    subject = html.escape(subject, quote=False)
    room = html.escape(room, quote=False)
    lesson_type = html.escape(lesson_type, quote=False)
    try:
        from datetime import datetime as _datetime

        target_date = _datetime.strptime(date_str, "%Y-%m-%d").date()
        start_raw, end_raw = [part.strip() for part in time_str.replace("–", "-").split("-", 1)]
        _datetime.strptime(start_raw, "%H:%M")
        _datetime.strptime(end_raw, "%H:%M")
    except ValueError:
        await message.answer("❌ Неверная дата или время. Нужно YYYY-MM-DD и HH:MM-HH:MM.")
        return
    lesson = {
        "time": f"{start_raw}-{end_raw}",
        "start_time_raw": start_raw,
        "end_time_raw": end_raw,
        "subject": subject,
        "type": lesson_type,
        "room": room,
        "teachers": "",
        "groups": [group.upper()],
    }
    redis_client = dialog_manager.middleware_data.get("redis_client")
    if not redis_client:
        await message.answer("❌ Redis недоступен.")
        return
    from core.schedule_overrides import set_group_override

    await set_group_override(redis_client, group, target_date, [lesson])
    await message.answer(
        f"✅ Коррекция сохранена для <b>{html.escape(group.upper(), quote=False)}</b> на {html.escape(date_str, quote=False)}.",
        parse_mode="HTML",
    )


async def admin_command_handler(message: Message, dialog_manager: DialogManager) -> None:
    await dialog_manager.start(Admin.menu, mode=StartMode.RESET_STACK)


# --- Функции для запуска сервисов ---
async def run_metrics_server(port: int = 8000) -> None:
    """Запускает HTTP-сервер для Prometheus в отдельном потоке, чтобы не блокировать основную логику."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, start_http_server, port)
    logging.info(f"Prometheus metrics server started on http://localhost:{port}")


# --- Основная функция запуска ---
# Простой входной рейт-лимит через throttling middleware
# SimpleRateLimiter удален в пользу core/middlewares/rate_limit_middleware.py


async def error_handler(event: Any = None, exception: Exception | None = None, *args: Any, **kwargs: Any) -> Any:
    """Глобальный обработчик ошибок. Совместим с разными сигнатурами aiogram.
    Тихо обрабатывает устаревшие колбэки диалогов (UnknownIntent)."""
    exc = exception
    if exc is None and hasattr(event, "exception"):
        exc = getattr(event, "exception", None)
    try:
        # Специальная обработка: устаревший intent у aiogram-dialog
        if isinstance(exc, UnknownIntent):
            update = getattr(event, "update", None)
            cq = getattr(update, "callback_query", None)
            cb = getattr(cq, "message", None)
            # Пытаемся вежливо ответить на колбэк и предложить открыть меню
            try:
                if cq is not None and hasattr(cq, "answer"):
                    await cq.answer(
                        "Эта кнопка больше неактуальна. Откройте меню заново.",
                        show_alert=False,
                    )
            except Exception:
                pass
            try:
                bot: Bot = getattr(event, "bot", None)
                if cb is not None and bot is not None:
                    await bot.send_message(cb.chat.id, "Меню обновлено. Нажмите /start")
            except Exception:
                pass
            # Подавляем дальнейшее логирование этой ошибки
            return True

        # Остальные ошибки — логируем в JSON
        logging.error("Ошибка aiogram: %s", exc, exc_info=True)
        try:
            from core.metrics import ERRORS_TOTAL

            ERRORS_TOTAL.labels(source="handler").inc()
        except Exception:
            pass
    except Exception:
        # Никогда не падаем из обработчика ошибок
        pass
    return True


async def main():
    print("🚀 Starting bot...")
    try:
        from core.metrics import RESTART_COUNT

        RESTART_COUNT.inc()
    except Exception:
        pass
    telemetry_handles = setup_logging()  # Вызываем настройку логирования
    print("📝 Logging configured")
    load_dotenv()
    print("🔧 Environment loaded")

    bot_token = os.getenv("BOT_TOKEN")
    redis_url = os.getenv("REDIS_URL")
    db_url = os.getenv("DATABASE_URL")
    print("🔑 Environment variables checked")

    if not all([bot_token, redis_url, db_url]):
        print("❌ Missing environment variables")
        logging.critical("Одна из критически важных переменных окружения не найдена (BOT_TOKEN, REDIS_URL, DATABASE_URL).")
        return
    print("✅ All environment variables found")

    redis_client = Redis.from_url(redis_url or "")

    timetable_manager = await TimetableManager.create(redis_client=redis_client)
    if not timetable_manager:
        logging.critical("Критическая ошибка: не удалось инициализировать TimetableManager. Запуск отменен.")
        return

    user_data_manager = UserDataManager(db_url=db_url or "", redis_url=redis_url)
    await user_data_manager.init_db()

    from core.semester_settings import SemesterSettingsManager

    semester_settings_manager = SemesterSettingsManager(user_data_manager.async_session_maker)
    timetable_manager.set_semester_settings_manager(semester_settings_manager)

    logging.info("Менеджеры данных инициализированы.")

    storage = RedisStorage(redis=redis_client, key_builder=DefaultKeyBuilder(with_destiny=True))
    default_properties = DefaultBotProperties(parse_mode="HTML")
    # Увеличиваем таймаут HTTP-запросов к Telegram API (число секунд)
    # Важно: aiogram ожидает, что session.timeout будет числом, а не ClientTimeout
    http_session = AiohttpSession(timeout=180)
    bot = Bot(
        token=bot_token or "",
        default=default_properties,
        session=http_session,
    )
    dp = Dispatcher(storage=storage)

    scheduler = setup_scheduler(
        bot=bot,
        manager=timetable_manager,
        user_data_manager=user_data_manager,
        redis_client=redis_client,
    )

    # Подключение Middleware
    dp.update.middleware(ManagerMiddleware(timetable_manager))
    dp.update.middleware(UserDataMiddleware(user_data_manager))
    dp.update.middleware(SessionMiddleware(user_data_manager.async_session_maker))
    from bot.middlewares.activity_logging_middleware import ActivityLoggingMiddleware

    dp.update.middleware(ActivityLoggingMiddleware())  # Middleware для логирования активности пользователей
    dp.update.middleware(LoggingMiddleware())  # Middleware для сбора метрик и логов
    # Автоочистка чатов полностью отключена
    dp.update.middleware(I18nMiddleware())  # Интернационализация (после UserData!)
    dp.update.middleware(RateLimitMiddleware(redis_client=redis_client))  # Улучшенный анти-флуд
    dp.update.middleware(
        lambda handler, event, data: handler(
            event,
            {**data, "bot": bot, "scheduler": scheduler, "redis_client": redis_client},
        )
    )
    dp.update.middleware(
        SubscriptionFilterMiddleware(
            session_factory=user_data_manager.async_session_maker,
            redis_client=redis_client,
        )
    )
    dp.errors.register(error_handler)

    # Регистрация диалогов и хэндлеров
    # Регистрируем feedback_reply_router первым, чтобы он обрабатывал сообщения в FEEDBACK_CHAT_ID
    dp.include_router(feedback_reply_router)

    all_dialogs = [
        main_menu_dialog,
        schedule_dialog,
        settings_dialog,
        find_dialog,
        about_dialog,
        feedback_dialog,
        admin_dialog,
    ]
    for dialog in all_dialogs:
        dp.include_router(dialog)
    setup_dialogs(dp)
    dp.update.middleware(AdminDialogGuardMiddleware())

    dp.message.register(start_command_handler, CommandStart())
    dp.message.register(about_command_handler, Command("about"))
    dp.message.register(feedback_command_handler, Command("feedback"))
    if ADMIN_IDS:
        dp.message.register(admin_command_handler, Command("admin"), F.from_user.id.in_(ADMIN_IDS))
        dp.message.register(override_command_handler, Command("override"), F.from_user.id.in_(ADMIN_IDS))
    dp.inline_query.register(inline_query_handler)
    # Обработчик inline-кнопки "Назад" на медиа-сообщениях
    dp.callback_query.register(on_inline_back, F.data == "back_to_day_img")
    # Обработчики кнопок оригинала и проверки подписки
    dp.callback_query.register(on_send_original_file_callback, F.data == "send_original_file")
    dp.callback_query.register(on_check_subscription_callback, F.data == "check_sub")
    # Обработчик отмены генерации изображений
    dp.callback_query.register(on_cancel_generation, F.data == "cancel_generation")
    # Времено отключено: кнопки "полное качество" и проверка подписки

    logging.info("Запуск бота, планировщика и сервера метрик и webhooks Alertmanager...")
    try:
        # На всякий случай удаляем webhook, чтобы polling получал апдейты
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logging.info("Webhook удален (drop_pending_updates=True)")
        except Exception as e:
            logging.error(f"Не удалось удалить webhook: {e}")

        await set_bot_commands(bot)
        scheduler.start()

        async def _notify_admins_start():
            if ADMIN_IDS:
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, "✅ Бот запущен и готов к работе")
                    except Exception:
                        pass

        # Сначала запускаем фоновые сервисы, затем начинаем polling
        logging.info("Starting background services...")
        asyncio.create_task(run_metrics_server())
        asyncio.create_task(
            run_alert_webhook_server(
                bot,
                ADMIN_IDS,
                redis_client=redis_client,
                db_session_maker=user_data_manager.async_session_maker,
            )
        )
        asyncio.create_task(start_business_monitoring())
        asyncio.create_task(_notify_admins_start())

        # Запускаем бота отдельно для лучшего контроля
        logging.info("Starting bot polling...")
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        except Exception as e:
            logging.error(f"Bot polling failed: {e}")
            raise
    finally:
        # Корректно останавливаем планировщик и ресурсы
        try:
            scheduler.shutdown()
        except Exception:
            pass
        try:
            await dp.storage.close()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass
        # Закрываем Playwright/Chromium, чтобы избежать утечек
        try:
            await shutdown_image_generator()
        except Exception:
            pass
        logging.info("Планировщик, бот и ресурсы рендеринга изображений остановлены.")
        shutdown_telemetry(telemetry_handles)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Приложение остановлено вручную.")
