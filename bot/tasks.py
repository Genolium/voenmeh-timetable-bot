import asyncio
import logging
import os
import time
from typing import Dict, Any

import dramatiq
from aiogram import Bot
from aiolimiter import AsyncLimiter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.middleware.asyncio import AsyncIO
from redis.asyncio import Redis

from bot.text_formatters import generate_reminder_text
from core.config import MEDIA_PATH
from bot.utils.image_compression import get_telegram_safe_image_path
from core.image_cache_manager import ImageCacheManager
from core.image_service import ImageService

load_dotenv()

# --- Конфигурация брокера RabbitMQ ---
broker_url = os.getenv("DRAMATIQ_BROKER_URL")
if not broker_url:
    raise RuntimeError("DRAMATIQ_BROKER_URL не найден. Воркер не может стартовать.")

rabbitmq_broker = RabbitmqBroker(url=broker_url)
# Ensure asyncio support middleware is enabled
rabbitmq_broker.add_middleware(AsyncIO())
dramatiq.set_broker(rabbitmq_broker)

# Redis-клиент для других нужд (не для брокера)
redis_url = os.getenv("REDIS_URL")
redis_password = os.getenv("REDIS_PASSWORD")

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# --- Инициализация бота для воркера ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Воркер не может работать.")

BOT_INSTANCE = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
rate_limiter = AsyncLimiter(25, 1)

async def _send_message(user_id: int, text: str):
    try:
        log.info(f"Попытка отправки сообщения пользователю {user_id}")
        async with rate_limiter:
            await BOT_INSTANCE.send_message(user_id, text, disable_web_page_preview=True)
        log.info(f"Сообщение успешно отправлено пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to send message to {user_id}: {e}")
        raise

async def _copy_message(user_id: int, from_chat_id: int, message_id: int):
    try:
        log.info(f"Попытка копирования сообщения (ID: {message_id}) пользователю {user_id}")
        async with rate_limiter:
            await BOT_INSTANCE.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        log.info(f"Сообщение (ID: {message_id}) успешно скопировано пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to copy message (ID: {message_id}) to {user_id}: {e}")
        raise

@dramatiq.actor
async def send_message_task(user_id: int, text: str):
    await _send_message(user_id, text)

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
async def copy_message_task(user_id: int, from_chat_id: int, message_id: int):
    await _copy_message(user_id, from_chat_id, message_id)

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
async def send_lesson_reminder_task(user_id: int, lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None, reminder_time_minutes: int | None = None):
    try:
        text_to_send = generate_reminder_text(lesson, reminder_type, break_duration, reminder_time_minutes)
        if text_to_send:
            await _send_message(user_id, text_to_send)
    except Exception as e:
        log.error(f"Dramatiq task send_lesson_reminder_task FAILED to prepare reminder for {user_id}: {e}")

@dramatiq.actor(max_retries=3, min_backoff=2000, time_limit=300000)
async def generate_week_image_task(cache_key: str, week_schedule: Dict[str, Any], week_name: str, group: str, user_id: int | None = None, placeholder_msg_id: int | None = None, final_caption: str | None = None):
    is_auto_generation = user_id is None
    log.info(f"🎨 [{'АВТО' if is_auto_generation else 'USER'}] Генерация изображения для {cache_key}")

    try:
        redis_client = Redis.from_url(redis_url, password=redis_password, decode_responses=False)
        cache_manager = ImageCacheManager(redis_client, cache_ttl_hours=192)
        image_service = ImageService(cache_manager, BOT_INSTANCE)
        
        week_key = cache_key.split("_")[-1]
        
        if is_auto_generation:
            success, _ = await image_service._generate_and_cache_image(
                cache_key, week_schedule, week_name, group
            )
            if success:
                log.info(f"✅ [АВТО] Изображение {cache_key} успешно сгенерировано и сохранено в кэш")
            else:
                log.error(f"❌ [АВТО] Не удалось сгенерировать изображение {cache_key}")
        else:
            success, _ = await image_service.get_or_generate_week_image(
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
    finally:
        if 'redis_client' in locals():
            await redis_client.close()

async def _send_error_message(user_id: int, error_text: str):
    """Отправляет сообщение об ошибке пользователю."""
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
        ])
        error_message = f"❌ {error_text}\n\nПопробуйте позже или обратитесь к администратору."
        await _send_message(user_id, error_message)
        log.info(f"⚠️ Отправлено сообщение об ошибке пользователю {user_id}")
    except Exception as e:
        log.error(f"❌ Не удалось отправить сообщение об ошибке пользователю {user_id}: {e}")