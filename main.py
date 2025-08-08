import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.types import Message, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from aiogram_dialog import setup_dialogs, StartMode, DialogManager
from dotenv import load_dotenv
from prometheus_client import start_http_server 
from pythonjsonlogger.json import JsonFormatter
from redis.asyncio.client import Redis

# --- Импорты ядра ---
from core.config import ADMIN_IDS
from core.alert_webhook import run_alert_webhook_server
from core.manager import TimetableManager
from core.user_data import UserDataManager

# --- Импорты бота ---
from bot.handlers.inline_handlers import inline_query_handler
from bot.middlewares.logging_middleware import LoggingMiddleware
from bot.middlewares.manager_middleware import ManagerMiddleware
from bot.middlewares.user_data_middleware import UserDataMiddleware
from bot.scheduler import setup_scheduler

# --- Импорты диалогов ---
from bot.dialogs.about_menu import about_dialog
from bot.dialogs.admin_menu import admin_dialog
from bot.dialogs.feedback_menu import feedback_dialog
from bot.dialogs.find_menu import find_dialog
from bot.dialogs.main_menu import dialog as main_menu_dialog
from bot.dialogs.schedule_view import schedule_dialog
from bot.dialogs.settings_menu import settings_dialog

# --- Импорты состояний ---
from bot.dialogs.states import About, Admin, Feedback, MainMenu, Schedule

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
def setup_logging():
    """Настраивает JSON логирование."""
    logHandler = logging.StreamHandler()
    # Используем JsonFormatter для вывода логов в формате JSON
    formatter = JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s %(user_id)s %(event_type)s'
    )
    logHandler.setFormatter(formatter)
    
    # Устанавливаем обработчик для корневого логгера
    logging.basicConfig(level=logging.INFO, handlers=[logHandler])
    # Устанавливаем уровень для логгера aiogram, чтобы не видеть слишком много системных сообщений
    logging.getLogger('aiogram').setLevel(logging.WARNING)

async def set_bot_commands(bot: Bot):
    """Устанавливает меню команд для пользователей и администраторов."""
    user_commands = [
        BotCommand(command="start", description="🤖 Главное меню"),
        BotCommand(command="about", description="📒 О боте"),
        BotCommand(command="feedback", description="🤝 Обратная связь"),
    ]
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    logging.info("Установлены стандартные команды для всех пользователей.")

    if ADMIN_IDS:
        admin_commands = user_commands + [
            BotCommand(command="admin", description="👑 Админ-панель")
        ]
        for admin_id in ADMIN_IDS:
            try:
                await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception as e:
                logging.error(f"Не удалось установить команды для админа {admin_id}: {e}")
        logging.info(f"Установлены расширенные команды для администраторов: {ADMIN_IDS}")

# --- Обработчики команд ---
async def start_command_handler(message: Message, dialog_manager: DialogManager):
    user_data_manager = dialog_manager.middleware_data.get("user_data_manager")
    if not isinstance(user_data_manager, UserDataManager):
        logging.error("UserDataManager is not available or has an invalid type in middleware data.")
        return

    if not message.from_user or not hasattr(message.from_user, "id"):
        logging.error("Message.from_user is None or does not have an 'id' attribute.")
        return

    saved_group = await user_data_manager.get_user_group(message.from_user.id)
    if saved_group:
        await dialog_manager.start(Schedule.view, data={"group": saved_group}, mode=StartMode.RESET_STACK)
    else:
        await dialog_manager.start(MainMenu.enter_group, mode=StartMode.RESET_STACK)

async def about_command_handler(message: Message, dialog_manager: DialogManager):
    await dialog_manager.start(About.page_1, mode=StartMode.RESET_STACK)

async def feedback_command_handler(message: Message, dialog_manager: DialogManager):
    await dialog_manager.start(Feedback.enter_feedback, mode=StartMode.RESET_STACK)

async def admin_command_handler(message: Message, dialog_manager: DialogManager):
    await dialog_manager.start(Admin.menu, mode=StartMode.RESET_STACK)

# --- Функции для запуска сервисов ---
async def run_metrics_server(port: int = 8000):
    """Запускает HTTP-сервер для Prometheus в отдельном потоке, чтобы не блокировать основную логику."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, start_http_server, port)
    logging.info(f"Prometheus metrics server started on http://localhost:{port}")

# --- Основная функция запуска ---
async def main():
    setup_logging()  # Вызываем настройку логирования
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN")
    redis_url = os.getenv("REDIS_URL")
    db_url = os.getenv("DATABASE_URL")

    if not all([bot_token, redis_url, db_url]):
        logging.critical("Одна из критически важных переменных окружения не найдена (BOT_TOKEN, REDIS_URL, DATABASE_URL).")
        return

    redis_client = Redis.from_url(redis_url or "")
    
    timetable_manager = await TimetableManager.create(redis_client=redis_client)
    if not timetable_manager:
        logging.critical("Критическая ошибка: не удалось инициализировать TimetableManager. Запуск отменен.")
        return
    
    user_data_manager = UserDataManager(db_url=db_url or "")
    logging.info("Менеджеры данных инициализированы.")

    storage = RedisStorage(redis=redis_client, key_builder=DefaultKeyBuilder(with_destiny=True))
    default_properties = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token=bot_token or "", default=default_properties)
    dp = Dispatcher(storage=storage)

    scheduler = setup_scheduler(
        bot=bot,
        manager=timetable_manager,
        user_data_manager=user_data_manager,
        redis_client=redis_client
    )

    # Подключение Middleware
    dp.update.middleware(ManagerMiddleware(timetable_manager))
    dp.update.middleware(UserDataMiddleware(user_data_manager))
    dp.update.middleware(LoggingMiddleware()) # Middleware для сбора метрик и логов
    dp.update.middleware(lambda handler, event, data: handler(event, {**data, 'bot': bot}))

    # Регистрация диалогов и хэндлеров
    all_dialogs = [
        main_menu_dialog, schedule_dialog, settings_dialog, find_dialog,
        about_dialog, feedback_dialog, admin_dialog
    ]
    for dialog in all_dialogs:
        dp.include_router(dialog)
    setup_dialogs(dp)

    dp.message.register(start_command_handler, CommandStart())
    dp.message.register(about_command_handler, Command("about"))
    dp.message.register(feedback_command_handler, Command("feedback"))
    if ADMIN_IDS:
        dp.message.register(admin_command_handler, Command("admin"), F.from_user.id.in_(ADMIN_IDS))
    dp.inline_query.register(inline_query_handler)

    logging.info("Запуск бота, планировщика и сервера метрик и webhooks Alertmanager...")
    try:
        await set_bot_commands(bot)
        scheduler.start()
        
        await asyncio.gather(
            dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
            run_metrics_server(),
            run_alert_webhook_server(bot, ADMIN_IDS)
        )
    finally:
        scheduler.shutdown()
        await dp.storage.close()
        await bot.session.close()
        logging.info("Планировщик и бот остановлены.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Приложение остановлено вручную.")