import asyncio
import logging
import os
from typing import Dict, Any

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
import os

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

# --- Асинхронные хелперы для отправки ---
async def _send_message(user_id: int, text: str):
    try:
        async with SEND_RATE_LIMITER:
            await BOT_INSTANCE.send_message(user_id, text, disable_web_page_preview=True)
        log.info(f"Сообщение успешно отправлено пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to send message to {user_id}: {e}")
        raise

async def _copy_message(user_id: int, from_chat_id: int, message_id: int):
    try:
        async with SEND_RATE_LIMITER:
            await BOT_INSTANCE.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        log.info(f"Сообщение {message_id} из чата {from_chat_id} успешно скопировано пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to copy message {message_id} to {user_id}: {e}")
        raise

# --- Акторы ---
@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=30000)
def send_message_task(user_id: int, text: str):
    LOOP.run_until_complete(_send_message(user_id, text))

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
    async def _run():
        try:
            output_dir = MEDIA_PATH / "generated"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{cache_key}.png"
            highres_vp = {"width": 2048, "height": 1400}
            ok = await generate_schedule_image(week_schedule, week_name, group, str(output_path), viewport_size=highres_vp)
            if not ok:
                log.error(f"Failed to generate image for {cache_key}")
                return
            if user_id:
                try:
                    photo = FSInputFile(output_path)
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]
                    ])
                    async with SEND_RATE_LIMITER:
                        if placeholder_msg_id:
                            try:
                                media = InputMediaPhoto(media=photo, caption=final_caption or "")
                                await BOT_INSTANCE.edit_message_media(chat_id=user_id, message_id=placeholder_msg_id, media=media, reply_markup=kb)
                            except Exception as e:
                                # Если Telegram считает, что сообщение "не изменилось", отправим новое фото
                                if "message is not modified" in str(e).lower():
                                    await BOT_INSTANCE.send_photo(chat_id=user_id, photo=photo, caption=final_caption or "", reply_markup=kb)
                                else:
                                    raise
                        else:
                            await BOT_INSTANCE.send_photo(chat_id=user_id, photo=photo, caption=final_caption or "", reply_markup=kb)
                except Exception as e:
                    log.warning(f"Could not send or edit week image message for user {user_id}: {e}")
        except Exception as e:
            log.error(f"generate_week_image_task failed: {e}")
    LOOP.run_until_complete(_run())