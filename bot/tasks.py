import asyncio
import atexit
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

import aiohttp
import dramatiq
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from dramatiq import Broker
from dramatiq.errors import RateLimitExceeded

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
from dramatiq.encoder import JSONEncoder
from dramatiq.errors import RateLimitExceeded
from dramatiq.middleware import Middleware
from dramatiq.middleware.retries import Retries
from dramatiq.middleware.time_limit import TimeLimit
from dramatiq.results.backends import RedisBackend
from pythonjsonlogger.json import JsonFormatter
from redis import asyncio as redis

from bot.text_formatters import generate_reminder_text
from bot.utils.image_compression import get_telegram_safe_image_path
from bot.utils.worker_loop import run_async
from core.config import MEDIA_PATH, SUBSCRIPTION_CHANNEL, settings
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
from core.image_service import ImageService
from core.metrics import NOTIFICATION_DELIVERY
from core.telemetry import setup_telemetry, shutdown_telemetry
from core.user_data import UserDataManager

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
    stream_handler.setFormatter(JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s", json_default=str))
    root_logger.addHandler(stream_handler)

if TELEMETRY_HANDLES and TELEMETRY_HANDLES.log_handler:
    root_logger.addHandler(TELEMETRY_HANDLES.log_handler)

root_logger.setLevel(logging.INFO)


def _shutdown_telemetry() -> None:
    shutdown_telemetry(TELEMETRY_HANDLES)


atexit.register(_shutdown_telemetry)


# --- Конфигурация Redis пула ---
def get_redis_client(decode_responses: bool = False) -> Any:
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
# Enhanced retry middleware with exponential backoff for RabbitMQ connection issues
class RobustRetries(Retries):
    """Enhanced retry middleware with exponential backoff for RabbitMQ connection issues."""

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
        if exception is not None:
            retries = message.options.get("retries", 0)
            max_retries = message.options.get("max_retries", self.max_retries)
            if max_retries is not None and retries >= max_retries:
                _record_failed_message_to_dlq(message, exception)
        super().after_process_message(broker, message, result=result, exception=exception)


def _record_failed_message_to_dlq(message: object, exception: BaseException) -> None:
    """Persist a Dramatiq message to DLQ after retries are exhausted."""
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from core.config import settings
        from core.dlq_manager import DLQManager

        args = list(getattr(message, "args", None) or [])
        user_id = args[0] if args and isinstance(args[0], int) else 0
        retries = (getattr(message, "options", None) or {}).get("retries", 0)
        actor_name = getattr(message, "actor_name", "unknown")
        payload = {"actor": actor_name, "args": args[:8]}

        async def _persist():
            engine = create_async_engine(settings.DATABASE_URL)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                manager = DLQManager(session_factory=factory)
                await manager.record_failed_message(
                    user_id=user_id,
                    payload=payload,
                    error_message=str(exception),
                    message_type=actor_name,
                    retry_count=int(retries or 0),
                )
            finally:
                await engine.dispose()

        run_async(_persist())
    except Exception:
        logging.getLogger(__name__).exception("Failed to record exhausted Dramatiq retries into DLQ")


def _configure_broker_middleware(broker: Any) -> None:
    """Навешивает middleware на брокер с защитой от дублей."""
    broker.add_middleware(RobustRetries(max_retries=5, min_backoff=2000, max_backoff=30000))
    try:
        if not any(isinstance(m, TimeLimit) for m in getattr(broker, "middleware", [])):
            broker.add_middleware(TimeLimit(time_limit=1800000))  # 30 минут (RabbitMQ consumer_timeout)
    except Exception:
        # Fallback: attempt to add once; ignore if duplicated by framework
        try:
            broker.add_middleware(TimeLimit(time_limit=1800000))
        except Exception:
            pass


def _create_noop_broker():
    """Минимальный in-memory брокер на случай отсутствия dramatiq StubBroker."""
    from dramatiq import Broker

    class _NoopBroker(Broker):
        def __init__(self):
            super().__init__()
            self.encoder = JSONEncoder()

        def declare_actor(self, actor):
            return

        def enqueue(self, message, *, delay=None):
            return

    return _NoopBroker()


def _compose_hostname_outside_docker(broker_url: str) -> bool:
    """True when URL points at a Compose service name but we are not in Docker."""
    from urllib.parse import urlparse

    host = (urlparse(broker_url).hostname or "").lower()
    if host not in {"rabbitmq", "redis", "db"}:
        return False
    return not Path("/.dockerenv").exists()


def _create_broker(broker_url: str | None) -> "Broker":
    """Создаёт брокер Dramatiq: RabbitMQ в production, stub — в тестах/CI."""
    if broker_url and not _compose_hostname_outside_docker(broker_url):
        # Connection parameters are handled via rabbitmq.conf and retry middleware
        broker = RabbitmqBroker(url=broker_url, confirm_delivery=True)
    else:
        try:
            from dramatiq.brokers.stub import StubBroker

            broker = StubBroker()
        except Exception:
            broker = _create_noop_broker()
    # Энкодер должен совпадать с production, иначе сообщения сериализуются по-разному.
    setattr(broker, "encoder", JSONEncoder())
    _configure_broker_middleware(broker)
    return broker


def _harden_broker_url(broker_url: str) -> tuple[str, bool]:
    """Добавляет разумные таймауты в URL брокера. Возвращает (url, changed)."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(broker_url)
    original = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query = dict(original)
    query.setdefault("heartbeat", "600")  # Большой heartbeat для жёсткой нагрузки
    query.setdefault("blocked_connection_timeout", "600")  # Больше времени на блокированное соединение
    query.setdefault("connection_attempts", "10")
    query.setdefault("retry_delay", "5")
    # Ensure socket-level timeout to avoid lingering dead connections
    query.setdefault("socket_timeout", "20")
    if query == original:
        return broker_url, False
    return urlunparse(parsed._replace(query=urlencode(query))), True


def _setup_broker(broker_url: str | None) -> tuple["Broker", str | None]:
    """Создаёт брокер, применяет жёсткие таймауты к URL и регистрирует его в Dramatiq."""
    broker = _create_broker(broker_url)
    dramatiq.set_broker(broker)

    # --- Harden broker URL defaults if missing heartbeat/timeouts ---
    if broker_url:
        try:
            hardened_url, changed = _harden_broker_url(broker_url)
            if changed:
                broker_url = hardened_url
                # Reconfigure broker with new URL only if different
                if getattr(broker, "url", None) != broker_url:
                    try:
                        broker.close()
                    except Exception:
                        pass
                    broker = _create_broker(broker_url)
                    dramatiq.set_broker(broker)
        except Exception:
            # Do not fail worker on URL tweak errors
            pass
    return broker, broker_url


broker_url = os.getenv("DRAMATIQ_BROKER_URL")
# Note: Connection stability is enhanced via rabbitmq.conf settings,
# connection pooling config above, and robust retry middleware
rabbitmq_broker, broker_url = _setup_broker(broker_url)

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


async def _mark_user_as_blocked(user_id: int) -> None:
    """Помечает пользователя как заблокировавшего бота (is_blocked = True)."""
    udm = None
    try:
        udm = UserDataManager(db_url=settings.DATABASE_URL, redis_url=settings.REDIS_URL)
        await udm.set_user_blocked(user_id, True)
        log.info(f"User {user_id} marked as is_blocked=True in database.")
    except Exception as e:
        log.warning(f"Failed to mark user {user_id} as blocked in DB: {e}")
    finally:
        if udm is not None:
            try:
                await udm.engine.dispose()
            except Exception:
                pass


async def _send_message(user_id: int, text: str) -> None:
    """Отправка сообщения с защитой от дублирования."""
    try:
        bot = await get_worker_bot()
        async with rate_limiter:
            await bot.send_message(user_id, text, disable_web_page_preview=True)

        log.info(f"[SEND_OK] Сообщение успешно отправлено пользователю {user_id}")
        try:
            NOTIFICATION_DELIVERY.labels(delivery_status="success", notification_type="text").inc()
        except Exception:
            pass
        return

    except TelegramForbiddenError:
        log.info(f"[SEND_BLOCKED] User {user_id} blocked the bot. Marking as blocked.")
        try:
            NOTIFICATION_DELIVERY.labels(delivery_status="failed", notification_type="text").inc()
        except Exception:
            pass
        await _mark_user_as_blocked(user_id)
        return
    except TelegramBadRequest as e:
        if "bot was blocked by the user" in str(e).lower() or "chat not found" in str(e).lower():
            log.info(f"[SEND_BLOCKED] User {user_id} blocked or deleted chat. Marking as blocked.")
            await _mark_user_as_blocked(user_id)
            return
        log.error(f"BadRequest sending to {user_id}: {e}")
        raise  # Неизвестная ошибка API, отправляем на ретрай для разбора
    except RetryAfter as e:
        log.warning(f"Telegram Rate Limit (RetryAfter {e.retry_after}s) for {user_id}")
        raise RateLimitExceeded("telegram retry after")
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


async def _copy_message(user_id: int, from_chat_id: int, message_id: int) -> None:
    """Копирование сообщения с защитой от дублирования."""
    try:
        log.info(f"Попытка копирования сообщения (ID: {message_id}) пользователю {user_id}")
        bot = await get_worker_bot()
        async with rate_limiter:
            await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id)
        log.info(f"Сообщение (ID: {message_id}) успешно скопировано пользователю {user_id}")
        return
    except TelegramForbiddenError:
        log.info(f"User {user_id} blocked the bot. Marking as blocked.")
        await _mark_user_as_blocked(user_id)
        return
    except TelegramBadRequest as e:
        if "bot was blocked by the user" in str(e).lower() or "chat not found" in str(e).lower():
            log.info(f"[SEND_BLOCKED] User {user_id} blocked or deleted chat. Marking as blocked.")
            await _mark_user_as_blocked(user_id)
            return
        log.error(f"BadRequest copying message to {user_id}: {e}")
        raise
    except RetryAfter as e:
        log.warning(f"Telegram Rate Limit (RetryAfter {e.retry_after}s) for {user_id}")
        raise RateLimitExceeded("telegram retry after")
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


@dramatiq.actor(
    queue_name="high_priority", max_retries=5, min_backoff=1000, time_limit=900000
)  # 15 мин (достаточно для сетевых задержек)
def send_message_task(user_id: int, text: str) -> None:
    run_async(_send_message(user_id, text))


@dramatiq.actor(queue_name="high_priority", max_retries=5, min_backoff=1000, time_limit=300000)  # 5 мин
def copy_message_task(user_id: int, from_chat_id: int, message_id: int) -> None:
    run_async(_copy_message(user_id, from_chat_id, message_id))


@dramatiq.actor(
    queue_name="medium_priority", max_retries=5, min_backoff=1000, time_limit=600000
)  # 10 мин (больше на случай задержек сети)
def send_lesson_reminder_task(
    user_id: int,
    lesson: Dict[str, Any] | None,
    reminder_type: str,
    break_duration: int | None,
    reminder_time_minutes: int | None = None,
    lang: str = "ru",
) -> None:
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


@dramatiq.actor(
    queue_name="high_priority", max_retries=3, min_backoff=2000, time_limit=1200000
)  # 20 мин (для генерации изображений, особенно при нагрузке)
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
) -> None:
    # CRITICAL: Acquire semaphore in the Dramatiq thread to avoid blocking the background loop
    with _generation_semaphore:

        async def _inner():
            is_auto_generation = user_id is None
            log.info(
                f"🎨 [{'АВТО' if is_auto_generation else 'USER'}] Генерация изображения для {cache_key} (семафор получен)"
            )

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
                        # Вычисляем тему пользователя. В этой ветке user_id всегда задан,
                        # т.к. авто-генерация обрабатывается выше.
                        user_theme = None
                        assert user_id is not None
                        db_url = os.getenv("DATABASE_URL")
                        if db_url:
                            udm = UserDataManager(db_url=db_url, redis_url=redis_url)
                            try:
                                user_theme = await udm.get_user_theme(user_id)
                            except Exception:
                                user_theme = None
                            finally:
                                try:
                                    await udm.engine.dispose()
                                except Exception:
                                    pass
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


async def _send_error_message(user_id: int, error_text: str) -> None:
    """Отправляет сообщение об ошибке пользователю."""
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_day_img")]])
        error_message = f"❌ {error_text}\n\nПопробуйте позже или обратитесь к администратору."
        await _send_message(user_id, error_message)
        log.info(f"⚠️ Отправлено сообщение об ошибке пользователю {user_id}")
    except Exception as e:
        log.error(f"❌ Не удалось отправить сообщение об ошибке пользователю {user_id}: {e}")


@dramatiq.actor(
    queue_name="high_priority", max_retries=3, min_backoff=1500, time_limit=300000
)  # 5 мин (для проверки подписки и отправки сообщений)
def check_theme_subscription_task(user_id: int, callback_data: str = None) -> None:
    """
    Проверяет подписку пользователя на канал для доступа к темам.
    Кэширует результат проверки на 6 часов для подписанных, на 1 минуту для неподписанных.
    """

    async def _inner():
        r = None
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

                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=channel_link)]]
                    )

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
        finally:
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass

    run_async(_inner())


@dramatiq.actor(
    queue_name="high_priority", max_retries=3, min_backoff=1500, time_limit=600000
)  # 10 мин (для отправки изображения + проверка)
def send_week_original_if_subscribed_task(user_id: int, cache_key: str) -> None:
    async def _inner():
        r = None
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
                    if channel_link.startswith("@"):
                        channel_link = f"https://t.me/{channel_link[1:]}"
                    elif channel_link.startswith("-"):
                        channel_link = f"tg://resolve?domain={channel_link}"
                    elif not channel_link.startswith("http"):
                        channel_link = f"https://t.me/{channel_link}"
                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=channel_link)]]
                    )
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
        finally:
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass

    run_async(_inner())
