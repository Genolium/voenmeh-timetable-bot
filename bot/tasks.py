import asyncio
import logging
import os
import time
from typing import Dict, Any
from datetime import datetime

import dramatiq
from aiogram import Bot
from aiolimiter import AsyncLimiter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from dramatiq.brokers.redis import RedisBroker
from redis.asyncio import Redis
import redis

from bot.text_formatters import generate_reminder_text
from core.image_generator import generate_schedule_image
from core.config import MEDIA_PATH
from bot.utils.image_compression import get_telegram_safe_image_path
import os
from core.image_cache_manager import ImageCacheManager

load_dotenv()

# --- Конфигурация брокера и логгера ---
redis_url = os.getenv("REDIS_URL")
redis_password = os.getenv("REDIS_PASSWORD")
if not redis_url or not redis_password:
    raise RuntimeError("REDIS_URL или REDIS_PASSWORD не найдены. Воркер не может стартовать.")

redis_broker = RedisBroker(url=redis_url, password=redis_password)
dramatiq.set_broker(redis_broker)

# Redis-клиент будет создаваться внутри каждой задачи

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# --- Инициализация бота для воркера ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Воркер не может работать.")

BOT_INSTANCE = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
# Убираем глобальный LOOP - будем создавать event loop локально в каждой задаче

# Убираем глобальный рейт-лимитер - будем создавать его локально в каждой задаче
# Telegram ограничивает около 30 msg/s на бота

# Добавляем счетчик для отладки рейт лимитера
_rate_limit_counter = 0

# --- Асинхронные хелперы для отправки ---
async def _send_message(user_id: int, text: str):
    global _rate_limit_counter
    try:
        _rate_limit_counter += 1
        log.info(f"Попытка отправки сообщения #{_rate_limit_counter} пользователю {user_id}")
        # Создаем локальный рейт-лимитер для каждого вызова
        rate_limiter = AsyncLimiter(25, time_period=1)
        async with rate_limiter:
            await BOT_INSTANCE.send_message(user_id, text, disable_web_page_preview=True)
        log.info(f"Сообщение #{_rate_limit_counter} успешно отправлено пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to send message #{_rate_limit_counter} to {user_id}: {e}")
        raise

async def _copy_message(user_id: int, from_chat_id: int, message_id: int):
    global _rate_limit_counter
    try:
        _rate_limit_counter += 1
        log.info(f"Попытка копирования сообщения #{_rate_limit_counter} (ID: {message_id}) пользователю {user_id}")
        # Создаем локальный рейт-лимитер для каждого вызова
        rate_limiter = AsyncLimiter(25, time_period=1)
        async with rate_limiter:
            await BOT_INSTANCE.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        log.info(f"Сообщение #{_rate_limit_counter} (ID: {message_id}) успешно скопировано пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to copy message #{_rate_limit_counter} (ID: {message_id}) to {user_id}: {e}")
        raise

# --- Акторы ---
@dramatiq.actor
def send_message_task(user_id: int, text: str):
    # Создаем синхронный Redis-клиент для избежания проблем с event loop
    sync_redis_client = redis.Redis.from_url(redis_url, password=redis_password, decode_responses=False)
    
    # Add unique key, e.g., hash of user_id + text
    unique_key = f"send_msg_{user_id}_{hash(text)}"
    if sync_redis_client.get(unique_key):  # Use sync client directly
        return  # Already sent

    # Add retry for set
    for _ in range(3):
        try:
            asyncio.run(_send_message(user_id, text))
            sync_redis_client.set(unique_key, "done", ex=3600)
            break
        except Exception as e:
            logging.warning(f"Retry redis/set for {unique_key}: {e}")

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
def copy_message_task(user_id: int, from_chat_id: int, message_id: int):
    asyncio.run(_copy_message(user_id, from_chat_id, message_id))

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
def send_lesson_reminder_task(user_id: int, lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None, reminder_time_minutes: int | None = None):
    try:
        text_to_send = generate_reminder_text(lesson, reminder_type, break_duration, reminder_time_minutes)
        if text_to_send:
            send_message_task.send(user_id, text_to_send)
    except Exception as e:
        log.error(f"Dramatiq task send_lesson_reminder_task FAILED to prepare reminder for {user_id}: {e}")


# --- Генерация недельного изображения в очереди ---
@dramatiq.actor(max_retries=3, min_backoff=2000, time_limit=300000)
def generate_week_image_task(cache_key: str = None, week_schedule: Dict[str, Any] = None, week_name: str = None, group: str = None, user_id: int | None = None, placeholder_msg_id: int | None = None, final_caption: str | None = None, **kwargs):
    """
    Генерирует изображение расписания в фоновом режиме.
    НЕ блокирует основной поток бота благодаря Dramatiq.
    
    СОВМЕСТИМОСТЬ: Поддерживает как новый формат вызова с cache_key,
    так и старый формат с week_key (для совместимости со старыми задачами в очереди).
    """
    async def _run():
        nonlocal cache_key, week_schedule, week_name, group, user_id, placeholder_msg_id, final_caption
        
        # ОБРАБОТКА УСТАРЕВШЕГО ФОРМАТА ВЫЗОВА
        # Если функция была вызвана со старыми параметрами (week_key вместо cache_key)
        if 'week_key' in kwargs and cache_key is None:
            week_key = kwargs.get('week_key')
            group = kwargs.get('group', group)
            week_schedule = kwargs.get('week_schedule', week_schedule)
            week_name = kwargs.get('week_name', week_name)
            user_id = kwargs.get('user_id', user_id)
            placeholder_msg_id = kwargs.get('placeholder_msg_id', placeholder_msg_id)
            final_caption = kwargs.get('final_caption', final_caption)
            
            # Создаем cache_key из group и week_key
            cache_key = f"{group}_{week_key}"
            log.warning(f"⚠️ УСТАРЕВШИЙ ВЫЗОВ generate_week_image_task с week_key='{week_key}', конвертируем в cache_key='{cache_key}'")
        
        # Валидация параметров
        if not cache_key or not group or week_schedule is None:
            log.error(f"❌ Некорректные параметры для generate_week_image_task: cache_key={cache_key}, group={group}, week_schedule={'None' if week_schedule is None else 'provided'}")
            return
        
        try:
            # Создаем синхронный Redis-клиент для избежания проблем с event loop
            sync_redis_client = redis.Redis.from_url(redis_url, password=redis_password, decode_responses=False)
            
            # Определяем режим работы: автоматическая генерация или для пользователя
            is_auto_generation = user_id is None
            
            if is_auto_generation:
                log.info(f"🎨 [АВТО] Генерация изображения для {cache_key}")
            else:
                log.info(f"🎨 Начинаем генерацию изображения для {cache_key}, пользователь: {user_id}")
            
            # Создаем обертку для синхронного Redis
            class SyncRedisWrapper:
                def __init__(self, sync_client):
                    self.sync_client = sync_client
                
                async def get(self, key):
                    return self.sync_client.get(key)
                
                async def set(self, key, value, ex=None):
                    return self.sync_client.set(key, value, ex=ex)
                
                async def delete(self, key):
                    return self.sync_client.delete(key)
                
                async def keys(self, pattern):
                    return self.sync_client.keys(pattern)
                
                async def exists(self, key):
                    return self.sync_client.exists(key)
                
                def __getattr__(self, name):
                    return getattr(self.sync_client, name)
            
            redis_wrapper = SyncRedisWrapper(sync_redis_client)
            cache_manager = ImageCacheManager(redis_wrapper, cache_ttl_hours=24)
            
            # Используем унифицированный сервис изображений
            from core.image_service import ImageService
            image_service = ImageService(cache_manager, BOT_INSTANCE)
            
            # Извлекаем week_key из cache_key
            week_key = cache_key.split("_")[-1] if "_" in cache_key else "even"
            
            if is_auto_generation:
                # Для автоматической генерации просто генерируем и кэшируем
                success, file_path = await image_service._generate_and_cache_image(
                    cache_key, week_schedule, week_name, group
                )
                if success:
                    log.info(f"✅ [АВТО] Изображение {cache_key} успешно сгенерировано и сохранено в кэш")
                else:
                    log.error(f"❌ [АВТО] Не удалось сгенерировать изображение {cache_key}")
            else:
                # Для пользовательской генерации используем полный сервис
                success, file_path = await image_service.get_or_generate_week_image(
                    group=group,
                    week_key=week_key,
                    week_name=week_name,
                    week_schedule=week_schedule,
                    user_id=user_id,
                    placeholder_msg_id=placeholder_msg_id,
                    final_caption=final_caption
                )
                
                if not success:
                    await _send_error_message(user_id, "Не удалось сгенерировать изображение")
            
        except Exception as e:
            log.error(f"❌ generate_week_image_task failed: {e}")
            if not is_auto_generation and user_id:
                await _send_error_message(user_id, "Произошла ошибка при генерации")
    
    # В Dramatiq workers всегда запускаем новый event loop
    asyncio.run(_run())

async def _send_cached_image(cache_key: str, user_id: int, placeholder_msg_id: int, final_caption: str):
    """Отправляет кэшированное изображение пользователю."""
    try:
        # Создаем синхронный Redis-клиент для избежания проблем с event loop
        sync_redis_client = redis.Redis.from_url(redis_url, password=redis_password, decode_responses=False)
        
        # Создаем асинхронную обертку для синхронного Redis-клиента
        class SyncRedisWrapper:
            def __init__(self, sync_client):
                self.sync_client = sync_client
            
            async def get(self, key):
                return self.sync_client.get(key)
            
            async def set(self, key, value, ex=None):
                return self.sync_client.set(key, value, ex=ex)
            
            async def delete(self, key):
                return self.sync_client.delete(key)
            
            async def keys(self, pattern):
                return self.sync_client.keys(pattern)
            
            # Добавляем метод hasattr для совместимости
            def __getattr__(self, name):
                return getattr(self.sync_client, name)
        
        redis_wrapper = SyncRedisWrapper(sync_redis_client)
        cache_manager = ImageCacheManager(redis_wrapper, cache_ttl_hours=24)
        image_bytes = await cache_manager.get_cached_image(cache_key)
        
        if not image_bytes:
            await _send_error_message(user_id, "Кэшированное изображение не найдено")
            return
        
        # Сохраняем временно для отправки
        temp_path = MEDIA_PATH / "generated" / f"temp_{cache_key}.png"
        with open(temp_path, 'wb') as f:
            f.write(image_bytes)
        
        await _send_generated_image(str(temp_path), user_id, placeholder_msg_id, final_caption)
        
        # Удаляем временный файл
        try:
            os.remove(temp_path)
        except:
            pass
            
    except Exception as e:
        log.error(f"❌ Ошибка отправки кэшированного изображения: {e}")
        await _send_error_message(user_id, "Ошибка при отправке изображения")

async def _send_generated_image(output_path: str, user_id: int, placeholder_msg_id: int, final_caption: str):
    """Отправляет сгенерированное изображение пользователю."""
    try:
        # Сжимаем изображение для Telegram если нужно
        safe_image_path = get_telegram_safe_image_path(output_path)
        photo = FSInputFile(safe_image_path)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
        ])
        
        global _rate_limit_counter
        _rate_limit_counter += 1
        log.info(f"📤 Попытка отправки изображения #{_rate_limit_counter} пользователю {user_id}")
        
        # Создаем локальный рейт-лимитер для каждого вызова
        rate_limiter = AsyncLimiter(25, time_period=1)
        async with rate_limiter:
            if placeholder_msg_id:
                try:
                    # Пытаемся отредактировать существующее сообщение
                    media = InputMediaPhoto(media=photo, caption=final_caption or "")
                    await BOT_INSTANCE.edit_message_media(
                        chat_id=user_id, 
                        message_id=placeholder_msg_id, 
                        media=media, 
                        reply_markup=kb
                    )
                    log.info(f"✅ Изображение #{_rate_limit_counter} успешно отредактировано для пользователя {user_id}")
                except Exception as e:
                    # Если не удалось отредактировать, отправляем новое
                    if "message is not modified" in str(e).lower():
                        await BOT_INSTANCE.send_photo(
                            chat_id=user_id, 
                            photo=photo, 
                            caption=final_caption or "", 
                            reply_markup=kb
                        )
                        log.info(f"✅ Изображение #{_rate_limit_counter} отправлено как новое сообщение для пользователя {user_id}")
                    else:
                        raise
            else:
                # Отправляем новое сообщение
                await BOT_INSTANCE.send_photo(
                    chat_id=user_id, 
                    photo=photo, 
                    caption=final_caption or "", 
                    reply_markup=kb
                )
                log.info(f"✅ Изображение #{_rate_limit_counter} успешно отправлено пользователю {user_id}")
                
    except Exception as e:
        log.error(f"❌ Ошибка отправки изображения пользователю {user_id}: {e}")
        await _send_error_message(user_id, "Не удалось отправить изображение")

async def _send_error_message(user_id: int, error_text: str):
    """Отправляет сообщение об ошибке пользователю."""
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
        ])
        
        error_message = f"❌ {error_text}\n\nПопробуйте позже или обратитесь к администратору."
        
        global _rate_limit_counter
        _rate_limit_counter += 1
        
        # Создаем локальный рейт-лимитер для каждого вызова
        rate_limiter = AsyncLimiter(25, time_period=1)
        async with rate_limiter:
            await BOT_INSTANCE.send_message(
                chat_id=user_id,
                text=error_message,
                reply_markup=kb
            )
        log.info(f"⚠️ Отправлено сообщение об ошибке пользователю {user_id}")
    except Exception as e:
        log.error(f"❌ Не удалось отправить сообщение об ошибке пользователю {user_id}: {e}")