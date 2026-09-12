#!/usr/bin/env python3
"""
Скрипт для тестирования всех пользовательских тем оформления расписания.
Генерирует изображения для каждой темы с тестовыми данными.
"""
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent))

from core.image_generator import generate_schedule_image


def create_test_schedule_data():
    """Создает тестовые данные для расписания в формате, ожидаемом image_generator"""
    return {
        "ПОНЕДЕЛЬНИК": [
            {"subject": "ФИЗИКА", "type": "лаб", "room": "322", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
            {"subject": "СИСТЕМНОЕ ПО", "type": "", "room": "325*", "time": "10:50 - 12:20", "start_time_raw": "10:50"},
            {"subject": "ОСН.СИСТ.АН.", "type": "пр", "room": "259", "time": "12:40 - 14:10", "start_time_raw": "12:40"},
            {"subject": "СИСТЕМНОЕ ПО", "type": "пр", "room": "265", "time": "14:55 - 16:25", "start_time_raw": "14:55"},
            {"subject": "ТОЭ", "type": "", "room": "429", "time": "16:45 - 18:15", "start_time_raw": "16:45"},
        ],
        "ВТОРНИК": [{"subject": "МАТЕМАТИКА", "type": "лек", "room": "101", "time": "9:00 - 10:30", "start_time_raw": "9:00"}],
        "СРЕДА": [{"subject": "ФИЗИКА", "type": "лек", "room": "201", "time": "10:50 - 12:20", "start_time_raw": "10:50"}],
        "ЧЕТВЕРГ": [],
        "ПЯТНИЦА": [
            {"subject": "СИСТЕМНОЕ ПО", "type": "лаб", "room": "301", "time": "12:40 - 14:10", "start_time_raw": "12:40"}
        ],
        "СУББОТА": [],
    }


def print_progress_bar(current: int, total: int, prefix: str = "Прогресс", suffix: str = "", length: int = 50):
    """Выводит прогресс-бар в консоль."""
    filled_length = int(length * current // total)
    bar = "█" * filled_length + "-" * (length - filled_length)
    percent = f"{100 * current // total}%"
    print(f"\r{prefix} |{bar}| {percent} {suffix}", end="", flush=True)
    if current == total:
        print()  # Новая строка в конце


async def test_all_themes():
    """Тестирует генерацию изображений для всех пользовательских тем."""

    # Доступные темы
    themes = [
        ("standard", "Стандартная"),
        ("light", "Светлая"),
        ("dark", "Тёмная"),
        ("classic", "Классическая"),
        ("coffee", "Кофейная"),
        ("blueprint", "Чертёж"),
        ("space", "Космос"),
        ("nord", "Норд"),
        ("cyberpunk", "Киберпанк"),
        ("matrix", "Матрица"),
        ("matcha", "Матча"),
        ("sunset", "Закат"),
        ("lavender", "Лаванда"),
        ("oled", "OLED Стелс"),
        ("paper", "Книга"),
        ("rain", "Питерский дождь"),
    ]

    # Типы недель
    week_types = [("Нечётная", "odd"), ("Чётная", "even")]

    # Тестовые данные
    schedule_data = create_test_schedule_data()
    group = "ИКБО-01-23"

    # Создаем директорию для результатов
    output_dir = Path("generated_images") / "themes_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    total_images = len(themes) * len(week_types)
    current_image = 0

    print(f"🎨 Начинаю тестирование {total_images} тем ({len(themes)} тем × {len(week_types)} недель)...\n")

    for theme_id, theme_name in themes:
        for week_name, week_slug in week_types:
            current_image += 1

            filename = f"test_{theme_id}_{week_slug}.png"
            output_path = output_dir / filename

            print_progress_bar(
                current_image, total_images, f"Генерация тем", f"{current_image}/{total_images} ({theme_name}, {week_name})"
            )

            try:
                success = await generate_schedule_image(
                    schedule_data=schedule_data,
                    week_type=week_name,
                    group=group,
                    output_path=str(output_path),
                    user_theme=theme_id,
                )

                if success and output_path.exists():
                    file_size = output_path.stat().st_size / (1024 * 1024)
                    print(f"\n✅ Успешно: {filename} ({file_size:.2f} MB) - {theme_name}")
                else:
                    print(f"\n❌ Ошибка: {filename} - {theme_name}")

            except Exception as e:
                print(f"\n❌ Исключение: {filename} - {theme_name}: {e}")

    print(f"\n🎉 Тестирование завершено! Сгенерировано изображений: {current_image}")
    print(f"📁 Результаты сохранены в: {output_dir.absolute()}")


if __name__ == "__main__":
    asyncio.run(test_all_themes())
