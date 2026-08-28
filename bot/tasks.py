import atexit
import asyncio
import logging
import os
import threading
import time
from typing import Any, Dict

import aiohttp
import dramatiq
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from dramatiq.errors import RateLimitExceeded
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

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


from aiogram.client.default import DefaultBotProperties
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from aiolimiter import AsyncLimiter
from dotenv import load_dotenv
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.errors import RateLimitExceeded
from dramatiq.encoder import JSONEncoder
from pythonjsonlogger.json import JsonFormatter
from redis import asyncio as redis

from bot.text_formatters import generate_reminder_text
from bot.utils.image_compression import get_telegram_safe_image_path
from core.config import MEDIA_PATH, SUBSCRIPTION_CHANNEL, settings
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
from core.image_service import ImageService
from core.telemetry import setup_telemetry, shutdown_telemetry
from core.user_data import UserDataManager
from bot.utils.worker_loop import run_async

load_dotenv()


def _create_bot_with_timeout():
    """Create a Bot instance with proper HTTP client timeout settings."""
    session = AiohttpSession(timeout=60.0, limit=15)
    return Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)


TELEMETRY_HANDLES = None
if settings.OTEL_ENABLED:
    TELEMETRY_HANDLES = setup_telemetry(
        service_name=f"{settings.OTEL_SERVICE_NAME}-worker",
        environment=settings.OTEL_ENVIRONMENT,
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        headers=settings.OTEL_EXPORTER_OTLP_HEADERS,
    )

root_logger = logging.getLogger()
if not root_logger.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(
        JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s", json_default=str)
    )
    root_logger.addHandler(stream_handler)

if TELEMETRY_HANDLES and TELEMETRY_HANDLES.log_handler:
    root_logger.addHandler(TELEMETRY_HANDLES.log_handler)

root_logger.setLevel(logging.INFO)


def _shutdown_telemetry() -> None:
    shutdown_telemetry(TELEMETRY_HANDLES)


atexit.register(_shutdown_telemetry)


# --- Конфигурация Redis пула ---
def get_redis_client(decode_responses: bool = False):
    """Создает новый Redis-клиент для текущего event loop.

    Важно: не переиспользуем глобальные пулы между потоками/лупами Dramatiq,
    чтобы избежать ошибок вида "Future attached to a different loop".
    """
    return redis.Redis.from_url(
        redis_url,
        password=redis_password,
        decode_responses=decode_responses,
        retry_on_timeout=True,
        socket_timeout=10,
        socket_connect_timeout=5,
        health_check_interval=30,
        max_connections=10,
    )


# --- Конфигурация брокера RabbitMQ ---
broker_url = os.getenv("DRAMATIQ_BROKER_URL")
rabbitmq_broker = None
if broker_url:
    # Configure RabbitMQ broker with URL-based configuration
    # Connection parameters are handled via rabbitmq.conf and retry middleware
    rabbitmq_broker = RabbitmqBroker(url=broker_url, confirm_delivery=True)
    # Настройка энкодера
    rabbitmq_broker.encoder = JSONEncoder()
else:
    # В тестовой/CI-среде допускаем отсутствие брокера: используем StubBroker
    try:
        from dramatiq.brokers.stub import StubBroker

        rabbitmq_broker = StubBroker()
    except Exception:
        # В крайнем случае — создаём in-memory stub через стандартный Broker API
        from dramatiq import Broker

        class _NoopBroker(Broker):
            def __init__(self):
                super().__init__()

            def declare_actor(self, actor):
                return

            def enqueue(self, message, *, delay=None):
                return

        rabbitmq_broker = _NoopBroker()

# Add retry middleware for better connection stability
from dramatiq.middleware import Middleware
from dramatiq.middleware.retries import Retries
from dramatiq.middleware.time_limit import TimeLimit
from dramatiq.results.backends import RedisBackend


# Enhanced retry middleware with exponential backoff for RabbitMQ connection issues
class RobustRetries(Retries):
    """Enhanced retry middleware with better handling for connection issues."""

    def after_process_message(self, broker, message, *, result=None, exception=None):
        if exception is not None:
            # Special handling for RabbitMQ connection issues
            if any(
                error_type in str(exception).lower()
                for error_type in [
                    "broken pipe",
                    "connection lost",
                    "stream lost",
                    "connection reset",
                    "server disconnected",
                    "amqp connection error",
                ]
            ):
                # Exponential backoff for connection issues
                retries_left = message.options.get("retries", self.max_retries)
                if retries_left > 0:
                    # Increase backoff for connection issues
                    backoff = min(
                        self.max_backoff,
                        self.min_backoff * (2 ** (self.max_retries - retries_left)),
                    )
                    message.options["retries"] = retries_left - 1
                    broker.enqueue(message, delay=backoff)
                    return

        # Fall back to default retry logic
        super().after_process_message(broker, message, result=result, exception=exception)


# Configure middleware stack with guards against duplicates
rabbitmq_broker.add_middleware(RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000))
try:
    if not any(isinstance(m, TimeLimit) for m in getattr(rabbitmq_broker, "middleware", [])):
        rabbitmq_broker.add_middleware(TimeLimit(time_limit=1800000))  # 30 minutes (соответствует RabbitMQ consumer_timeout)
except Exception:
    # Fallback: attempt to add once; ignore if duplicated by framework
    try:
        rabbitmq_broker.add_middleware(TimeLimit(time_limit=1800000))
    except Exception:
        pass

# Note: Connection stability is enhanced via rabbitmq.conf settings,
# connection pooling config above, and robust retry middleware

dramatiq.set_broker(rabbitmq_broker)

# --- Harden broker URL defaults if missing heartbeat/timeouts ---
if broker_url:
    try:
        # Append sane defaults if not present in URL
        # Works for amqp and amqps URLs
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

        _parsed = urlparse(broker_url)
        _q = dict(parse_qsl(_parsed.query, keep_blank_values=True))
        _q.setdefault("heartbeat", "600")  # Большой heartbeat для жёсткой нагрузки
        _q.setdefault("blocked_connection_timeout", "600")  # Больше времени на блокированное соединение
        _q.setdefault("connection_attempts", "10")
        _q.setdefault("retry_delay", "5")
        # Ensure socket-level timeout to avoid lingering dead connections
        _q.setdefault("socket_timeout", "20")
        if _q != dict(parse_qsl(_parsed.query, keep_blank_values=True)):
            _new = _parsed._replace(query=urlencode(_q))
            broker_url = urlunparse(_new)
            # Reconfigure broker with new URL only if different
            if getattr(rabbitmq_broker, "url", None) != broker_url:
                rabbitmq_broker.close()
                rabbitmq_broker = RabbitmqBroker(url=broker_url, confirm_delivery=True)
                rabbitmq_broker.encoder = JSONEncoder()
                rabbitmq_broker.add_middleware(RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000))
                try:
                    if not any(isinstance(m, TimeLimit) for m in getattr(rabbitmq_broker, "middleware", [])):
                        rabbitmq_broker.add_middleware(TimeLimit(time_limit=1800000))
                except Exception:
                    try:
                        rabbitmq_broker.add_middleware(TimeLimit(time_limit=1800000))
                    except Exception:
                        pass
                dramatiq.set_broker(rabbitmq_broker)
    except Exception:
        # Do not fail worker on URL tweak errors
        pass

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

_worker_bot = None

async def get_worker_bot() -> Bot:
    """Возвращает единый экземпляр бота с общим пулом соединений для воркера."""
    global _worker_bot
    if _worker_bot is None:
        # limit=15 ограничивает количество одновременных TCP-соединений
        session = AiohttpSession(timeout=45.0, limit=15)
        _worker_bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)
        
    return _worker_bot


async def _send_message(user_id: int, text: str):
    """Отправка сообщения с защитой от дублирования."""
    try:
        bot = await get_worker_bot()
        async with rate_limiter:
            await bot.send_message(user_id, text, disable_web_page_preview=True)
            
        log.info(f"[SEND_OK] Сообщение успешно отправлено пользователю {user_id}")
        return

    except TelegramForbiddenError:
        log.info(f"[SEND_BLOCKED] User {user_id} blocked the bot. Skipping.")
        return
    except TelegramBadRequest as e:
        if "bot was blocked by the user" in str(e).lower() or "chat not found" in str(e).lower():
            log.info(f"[SEND_BLOCKED] User {user_id} blocked or deleted chat.")
            return
        log.error(f"BadRequest sending to {user_id}: {e}")
        raise # Неизвестная ошибка API, отправляем на ретрай для разбора
    except RetryAfter as e:
        log.warning(f"Telegram Rate Limit (RetryAfter {e.retry_after}s) for {user_id}")
        raise RateLimitExceeded() 
    except Exception as e:
        error_msg = str(e).lower()
        
        # Если ошибка соединения (мы даже не достучались до серверов Telegram) - ретраим
        if "connect call failed" in error_msg or "cannot connect" in error_msg:
            log.warning(f"Connection failed for {user_id}, returning to Dramatiq queue: {e}")
            raise 
            
        # Если это Read Timeout (мы отправили данные, но ответ завис) - НЕ РЕТРАИМ!
        # Telegram с вероятностью 99% уже доставил сообщение. Ретрай вызовет дубликат.
        if "timeout" in error_msg:
            log.error(f"Read Timeout for {user_id}. Message likely delivered. Skipping retry to avoid duplicates.")
            return 
            
        log.error(f"[SEND_FAIL] Unknown error sending to {user_id}: {e}")
        raise


async def _copy_message(user_id: int, from_chat_id: int, message_id: int):
    """Копирование сообщения с защитой от дублирования."""
    try:
        log.info(f"Попытка копирования сообщения (ID: {message_id}) пользователю {user_id}")
        bot = await get_worker_bot()
        async with rate_limiter:
            await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        log.info(f"Сообщение (ID: {message_id}) успешно скопировано пользователю {user_id}")
        return
    except TelegramForbiddenError:
        log.info(f"User {user_id} blocked the bot. Skipping.")
        return
    except TelegramBadRequest as e:
        if "bot was blocked by the user" in str(e).lower() or "chat not found" in str(e).lower():
            log.info(f"[SEND_BLOCKED] User {user_id} blocked or deleted chat.")
            return
        log.error(f"BadRequest copying message to {user_id}: {e}")
        raise
    except RetryAfter as e:
        log.warning(f"Telegram Rate Limit (RetryAfter {e.retry_after}s) for {user_id}")
        raise RateLimitExceeded()
    except Exception as e:
        error_msg = str(e).lower()
        
        # Если ошибка соединения - ретраим
        if "connect call failed" in error_msg or "cannot connect" in error_msg:
            log.warning(f"Connection failed for {user_id}, returning to Dramatiq queue: {e}")
            raise 
            
        # Если это Read Timeout - НЕ РЕТРАИМ!
        if "timeout" in error_msg:
            log.error(f"Read Timeout for {user_id}. Message likely delivered. Skipping retry to avoid duplicates.")
            return 
            
        log.error(f"[COPY_FAIL] Unknown error copying message to {user_id}: {e}")
        raise


@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=900000)  # 15 мин (достаточно для сетевых задержек)
def send_message_task(user_id: int, text: str):
    run_async(_send_message(user_id, text))


@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=300000)  # 5 мин
def copy_message_task(user_id: int, from_chat_id: int, message_id: int):
    run_async(_copy_message(user_id, from_chat_id, message_id))


@dramatiq.actor(max_retries=5, min_backoff=1000, time_limit=600000)  # 10 мин (больше на случай задержек сети)
def send_lesson_reminder_task(
    user_id: int,
    lesson: Dict[str, Any] | None,
    reminder_type: str,
    break_duration: int | None,
    reminder_time_minutes: int | None = None,
    lang: str = "ru",
):
    async def _inner():
        try:
            text_to_send = generate_reminder_text(lesson, reminder_type, break_duration, reminder_time_minutes, lang)
            if text_to_send:
                await _send_message(user_id, text_to_send)
        except Exception as e:
            log.error(f"Dramatiq task send_lesson_reminder_task FAILED to prepare reminder for {user_id}: {e}")

    run_async(_inner())


# Семафор для ограничения количества одновременных задач генерации изображений
# Используем threading.Semaphore вместо asyncio.Semaphore для работы в разных event loop'ах Dramatiq
_generation_semaphore = threading.Semaphore(int(os.getenv("IMAGE_GENERATION_SEMAPHORE", "4")))  # Оптимизировано для 4 ядер


@dramatiq.actor(max_retries=3, min_backoff=2000, time_limit=1200000)  # 20 мин (для генерации изображений, особенно при нагрузке)
def generate_week_image_task(
    cache_key: str,
    week_schedule: Dict[str, Any],
    week_name: str,
    group: str,
    week_key: str,
    user_id: int | None = None,
    placeholder_msg_id: int | None = None,
    final_caption: str | None = None,
    lang: str = "ru",
):
    # CRITICAL: Acquire semaphore in the Dramatiq thread to avoid blocking the background loop
    with _generation_semaphore:
        async def _inner():
            is_auto_generation = user_id is None
            log.info(f"🎨 [{'АВТО' if is_auto_generation else 'USER'}] Генерация изображения для {cache_key} (семафор получен)")

            try:
                redis_client = get_redis_client(decode_responses=False)
                cache_manager = ImageCacheManager(redis_client, cache_ttl_hours=192)
                # Создаём бота в текущем лупе только если он действительно понадобится
                bot_for_images: Bot | None = None
                if not is_auto_generation:
                    bot_for_images = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
                image_service = ImageService(cache_manager, bot_for_images)

                # week_key now passed explicitly

                if is_auto_generation:
                    success, _ = await image_service._generate_and_cache_image(
                        cache_key, week_schedule, week_name, group, generated_by="mass", exec_locally=True
                    )
                    if success:
                        log.info(f"✅ [АВТО] Изображение {cache_key} успешно сгенерировано и сохранено в кэш")
                    else:
                        log.error(f"❌ [АВТО] Не удалось сгенерировать изображение {cache_key}")
                else:
                    # Используем контекстный менеджер для корректного закрытия сессии бота
                    assert bot_for_images is not None
                    async with bot_for_images:
                        # Вычисляем тему пользователя, если доступен user_id
                        user_theme = None
                        try:
                            if user_id is not None:
                                db_url = os.getenv("DATABASE_URL")
                                udm = UserDataManager(db_url=db_url or "", redis_url=redis_url)
                                user_theme = await udm.get_user_theme(user_id)
                        except Exception:
                            user_theme = None
                        success, _ = await image_service.get_or_generate_week_image(
                            group=group,
                            week_key=week_key,
                            week_name=week_name,
                            week_schedule=week_schedule,
                            user_id=user_id,
                            user_theme=user_theme,
                            placeholder_msg_id=placeholder_msg_id,
                            final_caption=final_caption,
                            lang=lang,
                            exec_locally=True,
                        )
                    if not success and user_id:
                        await _send_error_message(user_id, "Не удалось сгенерировать изображение")
            except Exception as e:
                log.error(f"❌ generate_week_image_task failed: {e}")
                if not is_auto_generation and user_id:
                    await _send_error_message(user_id, "Произошла ошибка при генерации")
            finally:
                if "redis_client" in locals():
                    try:
                        aclose = getattr(redis_client, "aclose", None)
                        if aclose and asyncio.iscoroutinefunction(aclose):
                            await aclose()
                    except Exception:
                        pass

    run_async(_inner())


async def _send_error_message(user_id: int, error_text: str):
    """Отправляет сообщение об ошибке пользователю."""
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]])
        error_message = f"❌ {error_text}\n\nПопробуйте позже или обратитесь к администратору."
        await _send_message(user_id, error_message)
        log.info(f"⚠️ Отправлено сообщение об ошибке пользователю {user_id}")
    except Exception as e:
        log.error(f"❌ Не удалось отправить сообщение об ошибке пользователю {user_id}: {e}")


@dramatiq.actor(max_retries=3, min_backoff=1500, time_limit=300000)  # 5 мин (для проверки подписки и отправки сообщений)
def check_theme_subscription_task(user_id: int, callback_data: str = None):
    """
    Проверяет подписку пользователя на канал для доступа к темам.
    Кэширует результат проверки на 6 часов для подписанных, на 1 минуту для неподписанных.
    """

    async def _inner():
        try:
            r = get_redis_client(decode_responses=True)
            bot = _create_bot_with_timeout()
            async with bot:
                is_subscribed = False
                cache_key = f"theme_sub_status:{user_id}"
                try:
                    cached = await r.get(cache_key)
                    if cached is not None:
                        is_subscribed = cached == "1"
                    else:
                        if SUBSCRIPTION_CHANNEL:
                            try:
                                member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                                status = getattr(member, "status", None)
                                is_subscribed = status in (
                                    "member",
                                    "administrator",
                                    "creator",
                                )
                            except Exception:
                                is_subscribed = False
                        await r.set(
                            cache_key,
                            "1" if is_subscribed else "0",
                            ex=21600 if is_subscribed else 60,
                        )
                except Exception:
                    pass

                if not is_subscribed and SUBSCRIPTION_CHANNEL:
                    # Корректно формируем ссылку на канал
                    channel_link = SUBSCRIPTION_CHANNEL
                    if channel_link.startswith("@"):
                        channel_link = f"https://t.me/{channel_link[1:]}"
                    elif channel_link.startswith("-"):
                        # Для каналов с числовым ID
                        channel_link = f"tg://resolve?domain={channel_link}"
                    elif not channel_link.startswith("http"):
                        # Для обыных имен каналов
                        channel_link = f"https://t.me/{channel_link}"

                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=channel_link)]])

                    message_text = (
                        "🎨 <b>Доступ к персональным темам</b>\n\n"
                        "Выберите уникальную тему для вашего расписания:\n\n"
                        "🎨 <b>Стандартная</b> - красная для нечётных, фиолетовая для чётных недель\n"
                        "☀️ <b>Светлая</b> - бирюзовая тема с кремовыми карточками\n"
                        "🌙 <b>Тёмная</b> - тёмная тема с фиолетовыми акцентами\n"
                        "📜 <b>Классическая</b> - тёмно-синяя тема с белыми карточками\n"
                        "☕ <b>Кофейная</b> - коричнево-золотая тема с кремовыми карточками\n\n"
                        "<i>Доступно только по подписке на канал разработки</i>"
                    )

                    await bot.send_message(user_id, message_text, reply_markup=kb)
                else:
                    # Пользователь подписан, отправляем уведомление об успехе
                    if callback_data:
                        await bot.answer_callback_query(callback_data, "✅ Доступ к темам подтверждён!")
                    # Отправляем сообщение с уведомлением и переключаем в нужное состояние
                    await bot.send_message(
                        user_id,
                        "✅ <b>Подписка подтверждена!</b>\n\n"
                        "Теперь вам доступны персональные темы оформления:\n\n"
                        "🎨 <b>Стандартная</b> - красная для нечётных, фиолетовая для чётных недель\n"
                        "☀️ <b>Светлая</b> - бирюзовая тема с кремовыми карточками\n"
                        "🌙 <b>Тёмная</b> - тёмная тема с фиолетовыми акцентами\n"
                        "📜 <b>Классическая</b> - тёмно-синяя тема с белыми карточками\n"
                        "☕ <b>Кофейная</b> - коричнево-золотая тема с кремовыми карточками\n\n"
                        "Выберите тему в настройках → 🎨 Тема",
                        parse_mode="HTML",
                    )

        except Exception as e:
            log.error(f"❌ check_theme_subscription_task failed: {e}")

    run_async(_inner())


@dramatiq.actor(max_retries=3, min_backoff=1500, time_limit=600000)  # 10 мин (для отправки изображения + проверка)
def send_week_original_if_subscribed_task(user_id: int, cache_key: str):
    async def _inner():
        try:
            r = get_redis_client(decode_responses=True)
            bot = _create_bot_with_timeout()
            async with bot:
                # ... (subscription check logic remains the same) ...
                
                # Check subscription status
                is_subscribed = False
                status_cache_key = f"sub_status:{user_id}"
                try:
                    cached = await r.get(status_cache_key)
                    if cached is not None:
                        is_subscribed = cached == "1"
                    else:
                        if SUBSCRIPTION_CHANNEL:
                            try:
                                member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                                status = getattr(member, "status", None)
                                is_subscribed = status in ("member", "administrator", "creator")
                            except Exception:
                                is_subscribed = False
                        await r.set(status_cache_key, "1" if is_subscribed else "0", ex=21600 if is_subscribed else 60)
                except Exception:
                    pass

                if not is_subscribed and SUBSCRIPTION_CHANNEL:
                    # (skip channel link generation for brevity in thought, but keep it in implementation)
                    channel_link = SUBSCRIPTION_CHANNEL
                    if channel_link.startswith("@"): channel_link = f"https://t.me/{channel_link[1:]}"
                    elif channel_link.startswith("-"): channel_link = f"tg://resolve?domain={channel_link}"
                    elif not channel_link.startswith("http"): channel_link = f"https://t.me/{channel_link}"
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=channel_link)]])
                    await bot.send_message(user_id, "Доступ к полному качеству по подписке на канал.", reply_markup=kb)
                    return

                # Use correct cache_key based path
                file_path = MEDIA_PATH / "generated" / f"{cache_key}.png"
                if not file_path.exists():
                    return
                # Добавляем имя файла и подпись для красоты и надежности
                doc_file = FSInputFile(file_path, filename=f"Schedule_{cache_key}.png")
                await bot.send_document(user_id, document=doc_file, caption="📄 Оригинал расписания (полное качество)")
        except Exception as e:
            log.error(f"send_week_original_if_subscribed_task failed: {e}")

    run_async(_inner())
