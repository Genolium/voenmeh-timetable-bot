import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
from aiogram.types import Message
from aiogram_dialog import setup_dialogs, StartMode, DialogManager
from dotenv import load_dotenv
from redis.asyncio.client import Redis

# --- Импорты ядра ---
from core.config import DATABASE_FILENAME, ADMIN_ID
from core.manager import TimetableManager
from core.user_data import UserDataManager

# --- Импорты бота ---
from bot.middlewares.manager_middleware import ManagerMiddleware
from bot.middlewares.user_data_middleware import UserDataMiddleware
from bot.scheduler import setup_scheduler
from bot.handlers.inline_handlers import inline_query_handler

# --- Импорты диалогов ---
from bot.dialogs.main_menu import dialog as main_menu_dialog
from bot.dialogs.schedule_view import schedule_dialog
from bot.dialogs.settings_menu import settings_dialog
from bot.dialogs.find_menu import find_dialog
from bot.dialogs.about_menu import about_dialog
from bot.dialogs.feedback_menu import feedback_dialog
from bot.dialogs.admin_menu import admin_dialog

# --- Импорты состояний ---
from bot.dialogs.states import MainMenu, Schedule, About, Feedback, Admin

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# --- Обработчики команд ---

async def start_command_handler(message: Message, dialog_manager: DialogManager):
    """
    Обрабатывает команду /start.
    Проверяет, есть ли у пользователя сохраненная группа, и направляет его
    либо к расписанию, либо к экрану ввода группы.
    """
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = message.from_user.id
    saved_group = await user_data_manager.get_user_group(user_id)

    if saved_group:
        await dialog_manager.start(Schedule.view, data={"group": saved_group}, mode=StartMode.RESET_STACK)
    else:
        await dialog_manager.start(MainMenu.enter_group, mode=StartMode.RESET_STACK)

async def about_command_handler(message: Message, dialog_manager: DialogManager):
    """Запускает диалог-инструкцию о боте."""
    await dialog_manager.start(About.page_1, mode=StartMode.RESET_STACK)

async def feedback_command_handler(message: Message, dialog_manager: DialogManager):
    """Запускает диалог для сбора обратной связи."""
    await dialog_manager.start(Feedback.enter_feedback, mode=StartMode.RESET_STACK)

async def admin_command_handler(message: Message, dialog_manager: DialogManager):
    """Запускает диалог админ-панели."""
    await dialog_manager.start(Admin.menu, mode=StartMode.RESET_STACK)


# --- Основная функция запуска ---

async def main():
    """Основная асинхронная функция для запуска бота."""
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")
    redis_url = os.getenv("REDIS_URL")

    if not bot_token:
        logging.error("Токен бота не найден! Укажите его в .env файле.")
        return
    if not redis_url:
        logging.error("URL для Redis не найден! Укажите REDIS_URL в .env файле.")
        return

    redis_client = Redis.from_url(redis_url)
    
    timetable_manager = await TimetableManager.create(redis_client)
    if not timetable_manager:
        logging.error("Критическая ошибка: не удалось инициализировать TimetableManager. Запуск отменен.")
        return
    
    user_data_manager = UserDataManager(db_path=DATABASE_FILENAME)
    await user_data_manager.init_db()
    logging.info("Менеджеры данных инициализированы.")

    storage = RedisStorage(redis=redis_client, key_builder=DefaultKeyBuilder(with_destiny=True))
    default_properties = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token=bot_token, default=default_properties)
    dp = Dispatcher(storage=storage)

    scheduler = setup_scheduler(
        bot=bot,
        manager=timetable_manager,
        user_data_manager=user_data_manager,
        redis_client=redis_client
    )

    # Подключаем Middleware
    dp.update.middleware(ManagerMiddleware(timetable_manager))
    dp.update.middleware(UserDataMiddleware(user_data_manager))
    # Пробрасываем экземпляр бота в диалоги через middleware
    dp.update.middleware(lambda handler, event, data: handler(event, {**data, 'bot': bot}))


    # --- Регистрация роутеров и диалогов ---
    dp.include_router(main_menu_dialog)
    dp.include_router(schedule_dialog)
    dp.include_router(settings_dialog)
    dp.include_router(find_dialog)
    dp.include_router(about_dialog)
    dp.include_router(feedback_dialog)
    dp.include_router(admin_dialog)
    setup_dialogs(dp)

    # --- Регистрация хэндлеров ---
    dp.message.register(start_command_handler, CommandStart())
    dp.message.register(about_command_handler, Command("about"))
    dp.message.register(feedback_command_handler, Command("feedback"))

    # Админские команды (с фильтром для проверки ID админа)
    if ADMIN_ID:
        try:
            is_admin_filter = F.from_user.id == int(ADMIN_ID)
            dp.message.register(admin_command_handler, Command("admin"), is_admin_filter)
        except (ValueError, TypeError):
            logging.warning("ADMIN_ID не является корректным числом. Админские команды не будут зарегистрированы.")
    
    dp.inline_query.register(inline_query_handler)

    logging.info("Запуск бота и планировщика...")
    try:
        scheduler.start()
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
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