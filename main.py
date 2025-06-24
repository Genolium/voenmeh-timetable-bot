import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder 
from aiogram.types import Message
from aiogram_dialog import setup_dialogs, StartMode, DialogManager
from dotenv import load_dotenv
from redis.asyncio.client import Redis

# Импорты ядра
from core.config import DATABASE_FILENAME, CACHE_FILENAME 
from core.parser import fetch_and_parse_all_schedules
from core.manager import TimetableManager
from core.user_data import UserDataManager

# Импорты бота
from bot.middlewares.manager_middleware import ManagerMiddleware
from bot.middlewares.user_data_middleware import UserDataMiddleware
from bot.dialogs import main_menu
from bot.dialogs.schedule_view import schedule_dialog
from bot.dialogs.settings_menu import settings_dialog
from bot.dialogs.find_menu import find_dialog
from bot.dialogs.states import MainMenu, Schedule
from bot.scheduler import setup_scheduler

# Настройка логирования для вывода информации в консоль
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

timetable_manager = None

async def start_command_handler(message: Message, dialog_manager: DialogManager):
    """
    Обработчик команды /start.
    Проверяет, есть ли у пользователя сохраненная группа, и направляет его
    либо к расписанию, либо к экрану ввода группы.
    """
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = message.from_user.id
    saved_group = await user_data_manager.get_user_group(user_id)

    if saved_group:
        await message.answer(f"Привет! Я помню, что твоя группа - <b>{saved_group}</b>.")
        await dialog_manager.start(Schedule.view, data={"group": saved_group}, mode=StartMode.RESET_STACK)
    else:
        await dialog_manager.start(MainMenu.enter_group, mode=StartMode.RESET_STACK)


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
        
    global timetable_manager

    timetable_manager = TimetableManager.load_from_cache()
    if not timetable_manager:
        all_schedules = fetch_and_parse_all_schedules()
        if all_schedules:
            timetable_manager = TimetableManager(all_schedules)
            timetable_manager.save_to_cache()
        else:
            logging.error("Не удалось инициализировать менеджер расписаний. Запуск отменен.")
            return

    user_data_manager = UserDataManager(db_path=DATABASE_FILENAME)
    await user_data_manager._init_db()
    logging.info("База данных пользователей инициализирована.")

    # --- ИЗМЕНЕНИЕ: Настраиваем RedisStorage с DefaultKeyBuilder ---
    redis_client = Redis.from_url(redis_url)
    storage = RedisStorage(redis=redis_client, key_builder=DefaultKeyBuilder(with_destiny=True))
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    
    default_properties = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token=bot_token, default=default_properties)
    dp = Dispatcher(storage=storage)

    scheduler = setup_scheduler(
        bot=bot,
        manager=timetable_manager,
        user_data_manager=user_data_manager
    )

    dp.update.middleware(ManagerMiddleware(timetable_manager))
    dp.update.middleware(UserDataMiddleware(user_data_manager))

    dp.include_router(main_menu.dialog)
    dp.include_router(schedule_dialog)
    dp.include_router(settings_dialog)
    dp.include_router(find_dialog)
    setup_dialogs(dp) 

    dp.message.register(start_command_handler, CommandStart())

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