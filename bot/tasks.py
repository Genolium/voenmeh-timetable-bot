import asyncio
import logging
import os
import time
from typing import Dict, Any

import dramatiq
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
# Compatibility layer for RetryAfter across aiogram versions
try:
    # aiogram v3.x
    from aiogram.exceptions import TelegramRetryAfter as RetryAfter
except Exception:
    try:
        # aiogram v2.x
        from aiogram.utils.exceptions import RetryAfter  # type: ignore
    except Exception:  # Fallback: define a stub to keep logic working
        class RetryAfter(Exception):
            pass
from aiolimiter import AsyncLimiter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.encoder import JSONEncoder
from redis import asyncio as redis

from bot.text_formatters import generate_reminder_text
from core.config import MEDIA_PATH, SUBSCRIPTION_CHANNEL
from bot.utils.image_compression import get_telegram_safe_image_path
from core.image_cache_manager import ImageCacheManager
from core.image_service import ImageService
from core.image_generator import generate_schedule_image

load_dotenv()

# --- Конфигурация брокера RabbitMQ ---
broker_url = os.getenv("DRAMATIQ_BROKER_URL")
if not broker_url:
    raise RuntimeError("DRAMATIQ_BROKER_URL не найден. Воркер не может стартовать.")

# Настройки для автоматического переподключения к RabbitMQ
# NOTE: When using 'url', do not pass pika parameters; set timeouts via environment if needed
rabbitmq_broker = RabbitmqBroker(url=broker_url, confirm_delivery=True)

# Настройка энкодера
rabbitmq_broker.encoder = JSONEncoder()

dramatiq.set_broker(rabbitmq_broker)

# Redis-клиент для других нужд (не для брокера)
redis_url = os.getenv("REDIS_URL")
redis_password = os.getenv("REDIS_PASSWORD")

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

# --- Инициализация параметров бота ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Воркер не может работать.")

# Для обратной совместимости с тестами, которые патчат BOT_INSTANCE
BOT_INSTANCE = None  # Не используется напрямую; бот создаётся внутри задач
rate_limiter = AsyncLimiter(25, 1)

async def _send_message(user_id: int, text: str):
    try:
        log.info(f"Попытка отправки сообщения пользователю {user_id}")
        # Создаём экземпляр бота в рамках текущего event loop,
        # чтобы избежать ошибок повторного использования закрытого лупа/сессии
        bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        async with bot:
            async with rate_limiter:
                await bot.send_message(user_id, text, disable_web_page_preview=True)
        log.info(f"Сообщение успешно отправлено пользователю {user_id}")
    except TelegramForbiddenError as e:
        # Пользователь заблокировал бота — не ретраем, просто фиксируем
        log.info(f"User {user_id} blocked the bot. Skipping send.")
        return
    except TelegramBadRequest as e:
        # Частые кейсы, которые не нужно ретраить, например 'bot was blocked by the user'
        text = str(e)
        if 'bot was blocked by the user' in text.lower():
            log.info(f"User {user_id} blocked the bot (BadRequest). Skipping send.")
            return
        log.error(f"BadRequest sending to {user_id}: {e}")
        raise
    except RetryAfter as e:
        # Рейт-лимит от Telegram: пробросим исключение, чтобы сработал dramatiq retry/backoff
        log.warning(f"RetryAfter while sending to {user_id}: {e}")
        raise
    except Exception as e:
        log.error(f"Dramatiq task FAILED to send message to {user_id}: {e}")
        raise

async def _copy_message(user_id: int, from_chat_id: int, message_id: int):
    try:
        log.info(f"Попытка копирования сообщения (ID: {message_id}) пользователю {user_id}")
        bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        async with bot:
            async with rate_limiter:
                await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        log.info(f"Сообщение (ID: {message_id}) успешно скопировано пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to copy message (ID: {message_id}) to {user_id}: {e}")
        raise

@dramatiq.actor
def send_message_task(user_id: int, text: str):
    asyncio.run(_send_message(user_id, text))

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
def copy_message_task(user_id: int, from_chat_id: int, message_id: int):
    asyncio.run(_copy_message(user_id, from_chat_id, message_id))

@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
def send_lesson_reminder_task(user_id: int, lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None, reminder_time_minutes: int | None = None):
    async def _inner():
        try:
            text_to_send = generate_reminder_text(lesson, reminder_type, break_duration, reminder_time_minutes)
            if text_to_send:
                await _send_message(user_id, text_to_send)
        except Exception as e:
            log.error(f"Dramatiq task send_lesson_reminder_task FAILED to prepare reminder for {user_id}: {e}")
    asyncio.run(_inner())

@dramatiq.actor(max_retries=3, min_backoff=2000, time_limit=300000)
def generate_week_image_task(cache_key: str, week_schedule: Dict[str, Any], week_name: str, group: str, user_id: int | None = None, placeholder_msg_id: int | None = None, final_caption: str | None = None):
    async def _inner():
        is_auto_generation = user_id is None
        log.info(f"🎨 [{'АВТО' if is_auto_generation else 'USER'}] Генерация изображения для {cache_key}")

        try:
            redis_client = redis.Redis.from_url(redis_url, password=redis_password, decode_responses=False)
            cache_manager = ImageCacheManager(redis_client, cache_ttl_hours=192)
            # Создаём бота в текущем лупе только если он действительно понадобится
            bot_for_images: Bot | None = None
            if not is_auto_generation:
                bot_for_images = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
            image_service = ImageService(cache_manager, bot_for_images)
            
            week_key = cache_key.split("_")[-1]
            
            if is_auto_generation:
                success, _ = await image_service._generate_and_cache_image(
                    cache_key,
                    week_schedule,
                    week_name,
                    group,
                    generated_by="mass"
                )
                if success:
                    log.info(f"✅ [АВТО] Изображение {cache_key} успешно сгенерировано и сохранено в кэш")
                else:
                    log.error(f"❌ [АВТО] Не удалось сгенерировать изображение {cache_key}")
            else:
                # Используем контекстный менеджер для корректного закрытия сессии бота
                assert bot_for_images is not None
                async with bot_for_images:
                    success, _ = await image_service.get_or_generate_week_image(
                        group=group,
                        week_key=week_key,
                        week_name=week_name,
                        week_schedule=week_schedule,
                        user_id=user_id,
                        placeholder_msg_id=placeholder_msg_id,
                        final_caption=final_caption
                    )
                if not success and user_id:
                    await _send_error_message(user_id, "Не удалось сгенерировать изображение")
        except Exception as e:
            log.error(f"❌ generate_week_image_task failed: {e}")
            if not is_auto_generation and user_id:
                await _send_error_message(user_id, "Произошла ошибка при генерации")
        finally:
            if 'redis_client' in locals():
                try:
                    aclose = getattr(redis_client, 'aclose', None)
                    if aclose and asyncio.iscoroutinefunction(aclose):
                        await aclose()
                except Exception:
                    pass
    asyncio.run(_inner())

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


@dramatiq.actor(max_retries=3, min_backoff=1500, time_limit=60000)
def send_week_original_if_subscribed_task(user_id: int, group: str, week_key: str):
    async def _inner():
        try:
            r = redis.Redis.from_url(redis_url, password=redis_password, decode_responses=True)
            bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
            async with bot:
                is_subscribed = False
                cache_key = f"sub_status:{user_id}"
                try:
                    cached = await r.get(cache_key)
                    if cached is not None:
                        is_subscribed = cached == '1'
                    else:
                        if SUBSCRIPTION_CHANNEL:
                            try:
                                member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                                status = getattr(member, "status", None)
                                is_subscribed = status in ("member", "administrator", "creator")
                            except Exception:
                                is_subscribed = False
                        await r.set(cache_key, '1' if is_subscribed else '0', ex=21600 if is_subscribed else 60)
                except Exception:
                    pass

                if not is_subscribed and SUBSCRIPTION_CHANNEL:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔔 Подписаться", url=f"https://t.me/{str(SUBSCRIPTION_CHANNEL).lstrip('@')}")]
                    ])
                    await bot.send_message(user_id, "Доступ к полному качеству по подписке на канал.", reply_markup=kb)
                    return

                file_path = MEDIA_PATH / "generated" / f"{group}_{week_key}.png"
                if not file_path.exists():
                    await bot.send_message(user_id, "⏳ Готовлю оригинал, попробуйте чуть позже…")
                    return
                await bot.send_document(user_id, FSInputFile(file_path))
        except Exception as e:
            log.error(f"send_week_original_if_subscribed_task failed: {e}")
    asyncio.run(_inner())