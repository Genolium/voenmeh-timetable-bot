import asyncio
import logging

# Глобальный семафор для ограничения количества одновременных генераций изображений
# Используем threading.Semaphore для работы в разных event loop'ах Dramatiq
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from bot.utils.image_compression import get_telegram_safe_image_path
from core.config import MEDIA_PATH, MOSCOW_TZ
from core.image_cache_manager import ImageCacheManager
from core.image_generator import generate_schedule_image
from core.metrics import SCHEDULE_GENERATION_TIME

_generation_semaphore = threading.Semaphore(
    int(os.getenv("IMAGE_SERVICE_SEMAPHORE", "2"))
)  # Максимум 2 одновременных генерации на процесс

logger = logging.getLogger(__name__)


class ImageService:
    """
    Унифицированный сервис для работы с изображениями расписания.
    Обрабатывает генерацию, кэширование и отправку изображений.
    """

    def __init__(self, cache_manager: ImageCacheManager, bot: Bot):
        self.cache_manager = cache_manager
        self.bot = bot
        self.generation_locks = {}  # Локальные блокировки для генерации

    async def get_or_generate_week_image(
        self,
        group: str,
        week_key: str,
        week_name: str,
        week_schedule: Dict[str, Any],
        user_id: Optional[int] = None,
        user_theme: Optional[str] = None,
        placeholder_msg_id: Optional[int] = None,
        final_caption: Optional[str] = None,
        lang: str = "ru",
    ) -> Tuple[bool, Optional[str]]:
        """
        Получает изображение из кэша или генерирует новое.

        Args:
            group: Название группы
            week_key: Ключ недели (even/odd)
            week_name: Название недели
            week_schedule: Данные расписания
            user_id: ID пользователя для отправки
            user_theme: Тема оформления пользователя для генерации
            placeholder_msg_id: ID сообщения-плейсхолдера
            final_caption: Подпись к изображению

        Returns:
            Tuple[success, file_path]
        """
        # Ключ кэша: для пользовательских тем учитываем тему
        if user_theme and user_theme != "standard":
            cache_key = f"{group}_{week_key}_{user_theme}_{lang}"
        else:
            cache_key = f"{group}_{week_key}_{lang}"

        logger.info(f"🎨 Requesting week image for {cache_key} (theme={user_theme or 'standard'})")

        # Проверяем кэш
        if await self.cache_manager.is_cached(cache_key):
            logger.info(f"✅ Cache HIT for {cache_key}")
            file_path = self.cache_manager.get_file_path(cache_key)

            # Проверяем, что файл действительно существует
            if not file_path.exists():
                logger.warning(f"⚠️ Cache hit but file missing: {file_path}")
                # Удаляем из кэша и генерируем заново
                await self.cache_manager.invalidate_cache(cache_key)
            else:
                if user_id:
                    send_success = await self._send_image_to_user(file_path, user_id, placeholder_msg_id, final_caption, lang=lang)
                    if not send_success:
                        logger.warning(f"⚠️ Failed to send cached image, will regenerate")
                        # Если не удалось отправить, генерируем заново
                        await self.cache_manager.invalidate_cache(cache_key)
                    else:
                        return True, str(file_path)
                else:
                    return True, str(file_path)

        logger.info(f"❌ Cache MISS for {cache_key}, generating...")

        # Генерируем изображение
        success, file_path = await self._generate_and_cache_image(
            cache_key, week_schedule, week_name, group, user_theme=user_theme, lang=lang
        )

        if success and user_id:
            await self._send_image_to_user(file_path, user_id, placeholder_msg_id, final_caption, lang=lang)

        return success, file_path

    async def _generate_and_cache_image(
        self,
        cache_key: str,
        schedule_data: Dict[str, Any],
        week_type: str,
        group: str,
        *,
        generated_by: str = "single",
        schedule_hash: Optional[str] = None,
        user_theme: Optional[str] = None,
        lang: str = "ru",
    ) -> Tuple[bool, Optional[str]]:
        """
        Генерирует изображение и сохраняет в кэш.

        Args:
            cache_key: Ключ кэша
            schedule_data: Данные расписания
            week_type: Тип недели
            group: Группа
            generated_by: Источник генерации
            schedule_hash: Хэш расписания
            user_theme: Тема оформления пользователя

        Returns:
            Tuple[success, file_path]
        """
        start_time = datetime.now(MOSCOW_TZ)

        # Ограничение на количество одновременных генераций
        with _generation_semaphore:
            logger.info(f"🔄 Starting image generation for {cache_key} (semaphore acquired)")

            # Добавляем метрику начала генерации
            from core.metrics import IMAGE_CACHE_MISSES, SCHEDULE_GENERATION_TIME

            IMAGE_CACHE_MISSES.labels(cache_type="generation_requested").inc()

            # Создаем лок для предотвращения дублирования генерации
            if cache_key not in self.generation_locks:
                self.generation_locks[cache_key] = asyncio.Lock()

            async with self.generation_locks[cache_key]:
                # Проверяем кэш еще раз после получения лока
                if await self.cache_manager.is_cached(cache_key):
                    logger.info(f"✅ Another process generated {cache_key} while waiting")
                    file_path = self.cache_manager.get_file_path(cache_key)
                    from core.metrics import IMAGE_CACHE_HITS

                    IMAGE_CACHE_HITS.labels(cache_type="concurrent_generation").inc()
                    return True, str(file_path)

                # Генерируем изображение
                file_path = self.cache_manager.get_file_path(cache_key)
                file_path.parent.mkdir(parents=True, exist_ok=True)

                logger.info(f"🔄 Generating image for {cache_key}")
                logger.info(f"Using user_theme={user_theme or 'standard'} for {cache_key}")

                try:
                    # Измеряем время генерации для метрик
                    with SCHEDULE_GENERATION_TIME.labels(schedule_type="week").time():
                        # Сохраняем исходную компоновку — жестко фиксированный холст
                        from core.render_config import VIEWPORT_HEIGHT, VIEWPORT_WIDTH

                        highres_vp = {
                            "width": VIEWPORT_WIDTH,
                            "height": VIEWPORT_HEIGHT,
                        }

                        success = await generate_schedule_image(
                            schedule_data=schedule_data,
                            week_type=week_type,
                            group=group,
                            output_path=str(file_path),
                            viewport_size=highres_vp,
                            user_theme=user_theme,
                            lang=lang,
                        )

                    if not success or not file_path.exists():
                        logger.error(f"❌ Failed to generate image for {cache_key}")
                        # Проверяем, есть ли файл и его размер
                        if file_path.exists():
                            file_size = file_path.stat().st_size
                            logger.error(f"   File exists but size is {file_size} bytes")
                        else:
                            logger.error(f"   File does not exist: {file_path}")
                        # Метрика неудачной генерации
                        from core.metrics import IMAGE_CACHE_MISSES

                        IMAGE_CACHE_MISSES.labels(cache_type="generation_failed").inc()
                        return False, None

                    # Сохраняем в кэш
                    try:
                        with open(file_path, "rb") as f:
                            image_bytes = f.read()

                        await self.cache_manager.cache_image(
                            cache_key,
                            image_bytes,
                            metadata={
                                "group": group,
                                "week_key": week_type,
                                "lang": lang,
                                "generated_at": datetime.now(MOSCOW_TZ).isoformat(),
                                "file_size": len(image_bytes),
                                "generated_by": generated_by,
                                **({"schedule_hash": schedule_hash} if schedule_hash else {}),
                            },
                        )

                        # Метрика успешного кэширования
                        from core.metrics import IMAGE_CACHE_OPERATIONS

                        IMAGE_CACHE_OPERATIONS.labels(operation="store").inc()

                        logger.info(f"💾 Successfully cached {cache_key} ({len(image_bytes)} bytes)")

                    except Exception as e:
                        logger.warning(f"⚠️ Failed to cache {cache_key}: {e}")
                        # Не возвращаем False, так как файл все равно создан

                    # Обновляем метрики времени генерации
                    generation_time = (datetime.now(MOSCOW_TZ) - start_time).total_seconds()
                    SCHEDULE_GENERATION_TIME.labels(schedule_type="week").observe(generation_time)

                    logger.info(f"✅ Successfully generated {cache_key} in {generation_time:.2f}s")
                    return True, str(file_path)

                except Exception as e:
                    logger.error(f"❌ Error generating {cache_key}: {e}")
                    return False, None

    async def _send_image_to_user(
        self,
        file_path: str,
        user_id: int,
        placeholder_msg_id: Optional[int],
        final_caption: Optional[str],
        lang: str = "ru",
    ) -> bool:
        """
        Отправляет изображение пользователю.

        Args:
            file_path: Путь к файлу
            user_id: ID пользователя
            placeholder_msg_id: ID сообщения-плейсхолдера
            final_caption: Подпись к изображению

        Returns:
            True если успешно отправлено
        """
        try:
            # Проверяем существование файла
            path_obj = Path(file_path)
            if not path_obj.exists():
                logger.error(f"❌ File not found: {file_path}")
                return False

            # Проверяем размер файла
            file_size = path_obj.stat().st_size
            if file_size == 0:
                logger.error(f"❌ File is empty: {file_path}")
                return False

            logger.info(f"📁 Sending file: {file_path} ({file_size} bytes)")

            # Сжимаем изображение для Telegram если нужно
            safe_image_path = get_telegram_safe_image_path(file_path)
            safe_path_obj = Path(safe_image_path)

            if not safe_path_obj.exists():
                logger.error(f"❌ Safe image file not found: {safe_image_path}")
                return False

            photo = FSInputFile(safe_image_path)

            # Клавиатура: оригинал файла + назад (в одном сообщении)
            from core.i18n import i18n

            btn_original_text = i18n.get("btn_original", lang=lang)
            btn_back_text = i18n.get("btn_back", lang=lang)

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=btn_original_text,
                            callback_data="send_original_file",
                        ),
                        InlineKeyboardButton(text=btn_back_text, callback_data="back_to_day_img"),
                    ]
                ]
            )

            if placeholder_msg_id:
                try:
                    # Пытаемся отредактировать существующее сообщение
                    from aiogram.types import InputMediaPhoto

                    media = InputMediaPhoto(media=photo, caption=final_caption or "")
                    await self.bot.edit_message_media(
                        chat_id=user_id,
                        message_id=placeholder_msg_id,
                        media=media,
                        reply_markup=kb,
                    )
                    logger.info(f"✅ Image edited for user {user_id}")
                    return True

                except Exception as e:
                    # Фолбэк при сетевых таймаутах и несущественных ошибках: отправим как новое
                    if "message is not modified" not in str(e).lower():
                        logger.warning(f"Failed to edit message for user {user_id}: {e}")
                        # Продолжаем с отправкой нового сообщения

            # Отправляем новое сообщение
            try:
                await self.bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=final_caption or "",
                    reply_markup=kb,
                )
            except Exception as e:
                # Если не удалось отправить фото — отправим текст и ссылку на оригинал (если есть)
                logger.warning(f"Photo send failed for user {user_id}: {e}")
                fallback_text = (final_caption or "") + "\n\n⚠️ Не удалось отправить изображение. Попробуйте позже."
                try:
                    await self.bot.send_message(chat_id=user_id, text=fallback_text, parse_mode="HTML")
                except Exception:
                    pass
                return False

            logger.info(f"✅ Image sent to user {user_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to send image to user {user_id}: {e}")
            return False

    async def get_cache_info(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        Получает информацию о кэшированном изображении.

        Args:
            cache_key: Ключ кэша

        Returns:
            Информация о кэше или None
        """
        return await self.cache_manager.get_cache_info(cache_key)

    async def invalidate_cache(self, cache_key: str) -> bool:
        """
        Удаляет изображение из кэша.

        Args:
            cache_key: Ключ кэша

        Returns:
            True если успешно удалено
        """
        return await self.cache_manager.invalidate_cache(cache_key)

    async def cleanup_expired_cache(self) -> int:
        """
        Очищает устаревшие файлы из кэша.

        Returns:
            Количество удаленных файлов
        """
        return await self.cache_manager.cleanup_expired_cache()

    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Получает статистику кэша.

        Returns:
            Статистика кэша
        """
        return await self.cache_manager.get_cache_stats()

    async def diagnose_cache(self, cache_key: str) -> Dict[str, Any]:
        """
        Диагностирует состояние кэша для конкретного ключа.

        Args:
            cache_key: Ключ кэша

        Returns:
            Диагностическая информация
        """
        return await self.cache_manager.diagnose_cache(cache_key)
