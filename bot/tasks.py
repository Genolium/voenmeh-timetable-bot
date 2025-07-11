import asyncio
import logging
import os
import random
import sys
from typing import Dict, Any

import dramatiq
import graypy
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from dramatiq.brokers.redis import RedisBroker
from pythonjsonlogger import jsonlogger


load_dotenv()

# Настройка подключения к Redis
redis_url = os.getenv("REDIS_URL")
redis_password = os.getenv("REDIS_PASSWORD")
if not redis_url or not redis_password:
    raise RuntimeError("REDIS_URL или REDIS_PASSWORD не найдены. Воркер не может стартовать.")

redis_broker = RedisBroker(url=redis_url, password=redis_password)
dramatiq.set_broker(redis_broker)

# Настройка логирования для воркера
def setup_worker_logging():
    log = logging.getLogger("dramatiq")
    log.setLevel(logging.INFO)
    
    if log.hasHandlers():
        log.handlers.clear()

    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(name)s %(levelname)s %(message)s'
    )
    
    # Обработчик для консоли
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    log.addHandler(console_handler)

    # Обработчик для Graylog/Logstash
    try:
        gelf_handler = graypy.GELFUDPHandler('logstash', 12201, extra_fields=True)
        gelf_handler.setFormatter(formatter)
        log.addHandler(gelf_handler)
        log.info("Worker GELF logging to Logstash enabled.")
    except Exception as e:
        log.error(f"Worker failed to set up GELF logging to Logstash: {e}")

setup_worker_logging()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден. Воркер не может работать.")

BOT_INSTANCE = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
LOOP = asyncio.get_event_loop()

UNSUBSCRIBE_FOOTER = "\n\n<tg-spoiler><i>Отключить эту рассылку можно в «⚙️ Настройки»</i></tg-spoiler>"

async def _send_message(user_id: int, text: str):
    """Асинхронный хелпер для отправки сообщения."""
    try:
        await BOT_INSTANCE.send_message(user_id, text, disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Failed to send message to {user_id}: {e}")

async def _copy_message(user_id: int, from_chat_id: int, message_id: int):
    """Асинхронный хелпер для копирования сообщения."""
    try:
        await BOT_INSTANCE.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
    except Exception as e:
        logging.error(f"Failed to copy message to {user_id}: {e}")

@dramatiq.actor(max_retries=1, time_limit=30000)
def send_message_task(user_id: int, text: str):
    """Задача, которая использует глобальный loop для отправки."""
    LOOP.run_until_complete(_send_message(user_id, text))

@dramatiq.actor(max_retries=1, time_limit=30000)
def copy_message_task(user_id: int, from_chat_id: int, message_id: int):
    """Задача, которая использует глобальный loop для копирования."""
    LOOP.run_until_complete(_copy_message(user_id, from_chat_id, message_id))

@dramatiq.actor(max_retries=1, time_limit=30000)
def send_lesson_reminder_task(user_id: int, lesson: Dict[str, Any] | None, reminder_type: str, break_duration: int | None):
    text = ""
    try:
        if reminder_type == "first" and lesson:
            greetings = ["Первая пара через 20 минут!", "Скоро начало, не опаздывайте!", "Готовимся к первой паре!"]
            text = f"🔔 <b>{random.choice(greetings)}</b>\n\n"
        
        elif reminder_type == "break" and lesson:
            next_lesson_time = lesson.get('time', 'N/A').split('-')[0].strip()
            if break_duration and break_duration >= 40:
                break_ideas = ["Можно успеть полноценно пообедать!", "Отличный шанс сходить в столовую."]
                break_text = f"У вас большой перерыв {break_duration} минут до {next_lesson_time}. {random.choice(break_ideas)}"
            elif break_duration and break_duration >= 15:
                break_ideas = ["Время выпить чаю.", "Можно немного размяться."]
                break_text = f"Перерыв {break_duration} минут до {next_lesson_time}. {random.choice(break_ideas)}"
            else:
                break_ideas = ["Успейте дойти до следующей аудитории.", "Короткая передышка."]
                break_text = random.choice(break_ideas)
            text = f"✅ <b>Пара закончилась!</b>\n{break_text}\n\n☕️ <b>Следующая пара:</b>\n"

        elif reminder_type == "final":
            final_phrases = ["Пары на сегодня всё! Можно отдыхать.", "Учебный день окончен. Хорошего вечера!"]
            text = f"🎉 <b>{random.choice(final_phrases)}</b>{UNSUBSCRIBE_FOOTER}"
            send_message_task.send(user_id, text)
            return
        
        else:
            return 
        
        if lesson:
            text += f"<b>{lesson.get('subject', 'N/A')}</b> ({lesson.get('type', 'N/A')}) в <b>{lesson.get('time', 'N/A')}</b>\n"
            info_parts = []
            if room := lesson.get('room'): info_parts.append(f"📍 {room}")
            if teachers := lesson.get('teachers'): info_parts.append(f"<i>с {teachers}</i>")
            if info_parts: text += " ".join(info_parts)
        
        text += UNSUBSCRIBE_FOOTER
        send_message_task.send(user_id, text)

    except Exception as e:
        logging.error(f"Dramatiq task send_lesson_reminder_task FAILED to prepare reminder for {user_id}: {e}")