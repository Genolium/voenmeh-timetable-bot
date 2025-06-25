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
from core.config import DATABASE_FILENAME
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
from bot.scheduler import setup_scheduler, global_timetable_manager_instance

# Настройка логирования для вывода информации в консоль
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


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
    # Загрузка переменных окружения из .env файла
    load_dotenv()
    bot_token = os.getenv("BOT_TOKEN")
    redis_url = os.getenv("REDIS_URL")

    if not bot_token:
        logging.error("Токен бота не найден! Укажите его в .env файле.")
        return
    if not redis_url:
        logging.error("URL для Redis не найден! Укажите REDIS_URL в .env файле.")
        return
        
    # 1. Инициализация менеджера расписаний
    # Этот менеджер должен быть готов ДО ВСЕГО ОСТАЛЬНОГО,
    # так как он будет передаваться в Middleware и Scheduler.
    # Если его инициализация не удалась, останавливаем запуск.
    initial_timetable_manager = TimetableManager.load_from_cache()
    if not initial_timetable_manager:
        logging.info("Кэш расписания отсутствует или устарел, загружаю новое расписание...")
        all_schedules = fetch_and_parse_all_schedules()
        if all_schedules:
            initial_timetable_manager = TimetableManager(all_schedules)
            initial_timetable_manager.save_to_cache()
        else:
            logging.error("НЕ УДАЛОСЬ ИНИЦИАЛИЗИРОВАТЬ МЕНЕДЖЕР РАСПИСАНИЙ. Бот не будет запущен.")
            return # Прерываем выполнение, если не удалось загрузить расписание
    
    # После успешной инициализации присваиваем его глобальной переменной.
    # Это гарантирует, что global_timetable_manager_instance никогда не будет None.
    global global_timetable_manager_instance
    global_timetable_manager_instance = initial_timetable_manager
    logging.info("TimetableManager успешно инициализирован.")

    # 2. Инициализация менеджера данных пользователя и создание таблицы в БД
    user_data_manager = UserDataManager(db_path=DATABASE_FILENAME)
    await user_data_manager._init_db()
    logging.info("База данных пользователей инициализирована.")

    # 3. Настройка FSM-хранилища на базе Redis
    redis_client = Redis.from_url(redis_url)
    storage = RedisStorage(redis=redis_client, key_builder=DefaultKeyBuilder(with_destiny=True))
    
    # 4. Настройка бота и диспетчера
    default_properties = DefaultBotProperties(parse_mode="HTML")
    bot = Bot(token=bot_token, default=default_properties)
    dp = Dispatcher(storage=storage)

    # 5. Регистрация Middleware (должна быть до регистрации роутеров/диалогов)
    dp.update.middleware(ManagerMiddleware(global_timetable_manager_instance))
    dp.update.middleware(UserDataMiddleware(user_data_manager))

    # 6. Регистрация роутеров и диалогов
    # Теперь диалоги будут регистрироваться, когда middleware уже настроен и менеджеры доступны
    dp.include_router(main_menu.dialog)
    dp.include_router(schedule_dialog)
    dp.include_router(settings_dialog)
    dp.include_router(find_dialog)
    setup_dialogs(dp) 

    # 7. Регистрация обычных хендлеров
    dp.message.register(start_command_handler, CommandStart())

    # 8. Настройка и запуск планировщика задач (scheduler)
    # Scheduler использует global_timetable_manager_instance, который уже инициализирован
    scheduler = setup_scheduler(
        bot=bot,
        manager=global_timetable_manager_instance, # Передаем уже инициализированный глобальный менеджер
        user_data_manager=user_data_manager
    )
    
    # 9. Запуск бота и планировщика
    logging.info("Запуск бота и планировщика...")
    try:
        scheduler.start()
        # Удаляем вебхук, если он был установлен ранее
        await bot.delete_webhook(drop_pending_updates=True)
        # Начинаем polling
        await dp.start_polling(bot)
    finally:
        # Корректно останавливаем планировщик и бота при выходе
        scheduler.shutdown()
        await dp.storage.close()
        await bot.session.close()
        logging.info("Планировщик и бот остановлены.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Приложение остановлено вручную.")