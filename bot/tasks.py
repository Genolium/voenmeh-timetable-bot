import asyncio
import logging
import os
from typing import Dict, Any
from datetime import datetime

import dramatiq
from aiogram import Bot
from aiolimiter import AsyncLimiter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from dramatiq.brokers.redis import RedisBroker

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

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# --- Инициализация бота для воркера ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Воркер не может работать.")

BOT_INSTANCE = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)

# Глобальный рейт-лимитер на исходящие сообщения: 25 сообщений в секунду
# Можно скорректировать по факту (Telegram ограничивает около 30 msg/s на бота)
SEND_RATE_LIMITER = AsyncLimiter(25, time_period=1)

# Добавляем счетчик для отладки рейт лимитера
_rate_limit_counter = 0

# --- Асинхронные хелперы для отправки ---
async def _send_message(user_id: int, text: str):
    global _rate_limit_counter
    try:
        _rate_limit_counter += 1
        log.info(f"Попытка отправки сообщения #{_rate_limit_counter} пользователю {user_id}")
        async with SEND_RATE_LIMITER:
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
        async with SEND_RATE_LIMITER:
            await BOT_INSTANCE.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        log.info(f"Сообщение #{_rate_limit_counter} (ID: {message_id}) успешно скопировано пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to copy message #{_rate_limit_counter} (ID: {message_id}) to {user_id}: {e}")
        raise

# --- Акторы ---
@dramatiq.actor
async def send_message_task(user_id: int, text: str):
    # Add unique key, e.g., hash of user_id + text
    unique_key = f"send_msg_{user_id}_{hash(text)}"
    if await redis_broker.get(unique_key):  # Assume redis available
        return  # Already sent

    # Add retry for set
    for _ in range(3):
        try:
            await _send_message(user_id, text)
            await redis_broker.set(unique_key, "done", ex=3600)
            break
        except Exception as e:
            logging.warning(f"Retry redis/set for {unique_key}: {e}")

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
def copy_message_task(user_id: int, from_chat_id: int, message_id: int):
    LOOP.run_until_complete(_copy_message(user_id, from_chat_id, message_id))

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
def send_lesson_reminder_task(user_id: int, lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None, reminder_time_minutes: int | None = None):
    try:
        text_to_send = generate_reminder_text(lesson, reminder_type, break_duration, reminder_time_minutes)
        if text_to_send:
            send_message_task.send(user_id, text_to_send)
    except Exception as e:
        log.error(f"Dramatiq task send_lesson_reminder_task FAILED to prepare reminder for {user_id}: {e}")


# --- Генерация недельного изображения в очереди ---
@dramatiq.actor(max_retries=3, min_backoff=2000, time_limit=180000)
def generate_week_image_task(cache_key: str, week_schedule: Dict[str, Any], week_name: str, group: str, user_id: int | None = None, placeholder_msg_id: int | None = None, final_caption: str | None = None):
    """
    Генерирует изображение расписания в фоновом режиме.
    НЕ блокирует основной поток бота благодаря Dramatiq.
    """
    async def _run():
        global _rate_limit_counter
        try:
            log.info(f"🎨 Начинаем генерацию изображения для {cache_key}, пользователь: {user_id}")
            
            # Проверяем кэш перед генерацией
            cache_manager = ImageCacheManager(redis_broker, cache_ttl_hours=24)
            if await cache_manager.is_cached(cache_key):
                log.info(f"✅ Изображение {cache_key} уже в кэше, пропускаем генерацию")
                # Отправляем кэшированное изображение
                await _send_cached_image(cache_key, user_id, placeholder_msg_id, final_caption)
                return
            
            # Создаем директорию для изображений
            output_dir = MEDIA_PATH / "generated"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{cache_key}.png"
            
            # Генерируем изображение с оптимизированными параметрами
            highres_vp = {"width": 2048, "height": 1400}
            ok = await generate_schedule_image(
                week_schedule, 
                week_name, 
                group, 
                str(output_path), 
                viewport_size=highres_vp
            )
            
            if not ok or not os.path.exists(output_path):
                log.error(f"❌ Не удалось сгенерировать изображение для {cache_key}")
                await _send_error_message(user_id, "Не удалось сгенерировать изображение")
                return
            
            # Сохраняем в кэш
            try:
                with open(output_path, 'rb') as f:
                    image_bytes = f.read()
                await cache_manager.cache_image(cache_key, image_bytes, metadata={
                    "group": group,
                    "week_key": week_name,
                    "generated_at": datetime.now().isoformat()
                })
                log.info(f"💾 Изображение {cache_key} сохранено в кэш")
            except Exception as e:
                log.warning(f"⚠️ Не удалось сохранить изображение в кэш: {e}")
            
            # Отправляем пользователю
            await _send_generated_image(output_path, user_id, placeholder_msg_id, final_caption)
            
        except Exception as e:
            log.error(f"❌ generate_week_image_task failed: {e}")
            await _send_error_message(user_id, "Произошла ошибка при генерации")
    
    # Запускаем в отдельном потоке, чтобы не блокировать Dramatiq
    LOOP.run_until_complete(_run())

async def _send_cached_image(cache_key: str, user_id: int, placeholder_msg_id: int, final_caption: str):
    """Отправляет кэшированное изображение пользователю."""
    try:
        cache_manager = ImageCacheManager(redis_broker, cache_ttl_hours=24)
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
        
        async with SEND_RATE_LIMITER:
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
        
        async with SEND_RATE_LIMITER:
            await BOT_INSTANCE.send_message(
                chat_id=user_id,
                text=error_message,
                reply_markup=kb
            )
        log.info(f"⚠️ Отправлено сообщение об ошибке пользователю {user_id}")
    except Exception as e:
        log.error(f"❌ Не удалось отправить сообщение об ошибке пользователю {user_id}: {e}")