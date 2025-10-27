"""
Утилиты для сжатия изображений перед отправкой в Telegram
"""

import logging
import os
from pathlib import Path
from typing import Optional

from PIL import Image

# Максимальные размеры для Telegram
TELEGRAM_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
TELEGRAM_MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5MB для фото
TELEGRAM_MAX_DIMENSION = 4096  # Максимальная сторона


def compress_image_for_telegram(
    input_path: str,
    output_path: Optional[str] = None,
    max_size_bytes: int = TELEGRAM_MAX_PHOTO_SIZE,
    max_dimension: int = TELEGRAM_MAX_DIMENSION,
    quality: int = 85,
) -> str:
    """
    Сжимает изображение для отправки в Telegram.

    Args:
        input_path: Путь к исходному изображению
        output_path: Путь для сохранения сжатого изображения (если None, перезаписывает исходное)
        max_size_bytes: Максимальный размер файла в байтах
        max_dimension: Максимальная сторона изображения в пикселях
        quality: Качество JPEG (1-100)

    Returns:
        Путь к сжатому изображению
    """
    if output_path is None:
        output_path = input_path

    try:
        with Image.open(input_path) as img:
            # Конвертируем в RGB если нужно
            if img.mode in ("RGBA", "LA", "P"):
                # Создаем белый фон для прозрачных изображений
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Проверяем размеры
            width, height = img.size
            if width > max_dimension or height > max_dimension:
                # Вычисляем коэффициент масштабирования
                scale = min(max_dimension / width, max_dimension / height)
                new_width = int(width * scale)
                new_height = int(height * scale)
                img = img.resize((new_width, new_height), Image.LANCZOS)
                logging.info(f"Изображение уменьшено с {width}x{height} до {new_width}x{new_height}")

            # Сохраняем с начальным качеством
            img.save(output_path, "JPEG", quality=quality, optimize=True)

            # Проверяем размер файла
            file_size = os.path.getsize(output_path)

            # Если файл все еще слишком большой, уменьшаем качество
            if file_size > max_size_bytes:
                quality_reduction_steps = [70, 60, 50, 40, 30]
                for new_quality in quality_reduction_steps:
                    img.save(output_path, "JPEG", quality=new_quality, optimize=True)
                    file_size = os.path.getsize(output_path)
                    if file_size <= max_size_bytes:
                        logging.info(f"Изображение сжато до {file_size} байт с качеством {new_quality}")
                        break
                else:
                    # Если все еще слишком большое, уменьшаем размеры
                    logging.warning(f"Не удалось сжать изображение до {max_size_bytes} байт, уменьшаем размеры")
                    while file_size > max_size_bytes and (img.width > 800 or img.height > 800):
                        new_width = int(img.width * 0.9)
                        new_height = int(img.height * 0.9)
                        img = img.resize((new_width, new_height), Image.LANCZOS)
                        img.save(output_path, "JPEG", quality=30, optimize=True)
                        file_size = os.path.getsize(output_path)
                        logging.info(f"Изображение уменьшено до {new_width}x{new_height}, размер: {file_size} байт")

            logging.info(f"Изображение успешно сжато: {file_size} байт")
            return output_path

    except Exception as e:
        logging.error(f"Ошибка при сжатии изображения {input_path}: {e}")
        # Возвращаем исходный путь если сжатие не удалось
        return input_path


def get_telegram_safe_image_path(input_path: str) -> str:
    """
    Возвращает путь к безопасному для Telegram изображению.
    Если исходное изображение слишком большое, создает сжатую версию.

    Args:
        input_path: Путь к исходному изображению

    Returns:
        Путь к безопасному изображению
    """
    if not os.path.exists(input_path):
        return input_path

    file_size = os.path.getsize(input_path)

    # Если файл уже подходящего размера, возвращаем как есть
    if file_size <= TELEGRAM_MAX_PHOTO_SIZE:
        return input_path

    # Создаем путь для сжатого изображения
    input_path_obj = Path(input_path)
    compressed_path = input_path_obj.parent / f"{input_path_obj.stem}_compressed{input_path_obj.suffix}"

    # Сжимаем изображение
    return compress_image_for_telegram(
        input_path=input_path,
        output_path=str(compressed_path),
        max_size_bytes=TELEGRAM_MAX_PHOTO_SIZE,
    )
