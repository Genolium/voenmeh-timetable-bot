import asyncio
import logging
import os
from typing import Dict, Any

import dramatiq
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from dramatiq.brokers.redis import RedisBroker

from bot.text_formatters import generate_reminder_text

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
LOOP = asyncio.get_event_loop()

# --- Асинхронные хелперы для отправки ---
async def _send_message(user_id: int, text: str):
    try:
        await BOT_INSTANCE.send_message(user_id, text, disable_web_page_preview=True)
        log.info(f"Сообщение успешно отправлено пользователю {user_id}")
    except Exception as e:
        log.error(f"Dramatiq task FAILED to send message to {user_id}: {e}")
        raise

async def _copy_message(user_id: int, from_chat_id: int, message_id: int):
    try:
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
def send_lesson_reminder_task(user_id: int, lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None):
    try:
        text_to_send = generate_reminder_text(lesson, reminder_type, break_duration)
        if text_to_send:
            send_message_task.send(user_id, text_to_send)
    except Exception as e:
        log.error(f"Dramatiq task send_lesson_reminder_task FAILED to prepare reminder for {user_id}: {e}")