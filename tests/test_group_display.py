#!/usr/bin/env python3
"""
Тестовый скрипт для проверки отображения номера группы в расписании
"""

import asyncio
import os
from pathlib import Path

from core.image_generator import generate_schedule_image


async def test_group_display():
    """Тестирует отображение номера группы в расписании."""

    # Создаем тестовые данные
    test_data = {
        "ПОНЕДЕЛЬНИК": [
            {
                "subject": "ФИЗИКА",
                "type": "лаб",
                "room": "322",
                "time": "9:00 - 10:30",
                "start_time_raw": "9:00",
            },
            {
                "subject": "МАТЕМАТИКА",
                "type": "лек",
                "room": "101",
                "time": "10:50 - 12:20",
                "start_time_raw": "10:50",
            },
        ],
        "ВТОРНИК": [
            {
                "subject": "ПРОГРАММИРОВАНИЕ",
                "type": "пр",
                "room": "205",
                "time": "9:00 - 10:30",
                "start_time_raw": "9:00",
            },
        ],
        "СРЕДА": [
            {
                "subject": "ХИМИЯ",
                "type": "лаб",
                "room": "301",
                "time": "9:00 - 10:30",
                "start_time_raw": "9:00",
            },
        ],
        "ЧЕТВЕРГ": [
            {
                "subject": "ИСТОРИЯ",
                "type": "лек",
                "room": "201",
                "time": "9:00 - 10:30",
                "start_time_raw": "9:00",
            },
        ],
        "ПЯТНИЦА": [
            {
                "subject": "АНГЛИЙСКИЙ",
                "type": "пр",
                "room": "203",
                "time": "9:00 - 10:30",
                "start_time_raw": "9:00",
            },
        ],
        "СУББОТА": [
            {
                "subject": "ФИЗКУЛЬТУРА",
                "type": "",
                "room": "",
                "time": "9:00 - 10:30",
                "start_time_raw": "9:00",
            },
        ],
    }

    # Создаем папку для результатов
    output_dir = Path("test_output")
    output_dir.mkdir(exist_ok=True)

    # Тестируем разные группы
    test_groups = ["О735Б", "А101С", "Е211Б"]
    week_types = [("Нечётная неделя", "test_odd"), ("Чётная неделя", "test_even")]

    print("🧪 Тестирование отображения номера группы в расписании")
    print("=" * 60)

    for group in test_groups:
        for week_type, filename_prefix in week_types:
            output_path = output_dir / f"{filename_prefix}_{group}.png"

            print(f"📸 Генерирую: {group} - {week_type}")

            try:
                success = await generate_schedule_image(
                    schedule_data=test_data,
                    week_type=week_type,
                    group=group,
                    output_path=str(output_path),
                    user_theme=None,
                )

                if success and os.path.exists(output_path):
                    file_size = os.path.getsize(output_path) / (1024 * 1024)
                    print(f"✅ Успешно: {output_path.name} ({file_size:.2f} МБ)")
                else:
                    print(f"❌ Ошибка: {output_path.name}")

            except Exception as e:
                print(f"❌ Исключение: {e}")

    print("\n🎉 Тестирование завершено!")
    print(f"📁 Результаты сохранены в: {output_dir.absolute()}")


if __name__ == "__main__":
    asyncio.run(test_group_display())
