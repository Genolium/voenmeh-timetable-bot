import asyncio
import sys
from pathlib import Path

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
            {"subject": "ТОЭ", "type": "", "room": "429*", "time": "16:45 - 18:15", "start_time_raw": "16:45"},
        ],
        "ВТОРНИК": [
            {"subject": "МАТЕМАТИКА", "type": "", "room": "101", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
            {"subject": "ПРОГРАММИРОВАНИЕ", "type": "", "room": "205", "time": "10:50 - 12:20", "start_time_raw": "10:50"},
            {"subject": "ИСТОРИЯ", "type": "", "room": "150", "time": "12:40 - 14:10", "start_time_raw": "12:40"},
        ],
        "СРЕДА": [
            {"subject": "ХИМИЯ", "type": "", "room": "301", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
            {"subject": "БИОЛОГИЯ", "type": "", "room": "401", "time": "10:50 - 12:20", "start_time_raw": "10:50"},
        ],
        "ЧЕТВЕРГ": [
            {"subject": "ЛИТЕРАТУРА", "type": "", "room": "201", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
            {
                "subject": "ТЕХН. И ОБОР. ПРОИЗВ. ИЭТ (лек)",
                "type": "",
                "room": "302",
                "time": "10:50 - 12:20",
                "start_time_raw": "10:50",
            },
            {"subject": "ФИЗКУЛЬТУРА", "type": "", "room": "", "time": "12:40 - 14:10", "start_time_raw": "12:40"},
        ],
        "ПЯТНИЦА": [
            {"subject": "ИНФОРМАТИКА", "type": "", "room": "405", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
            {"subject": "АНГЛИЙСКИЙ", "type": "", "room": "203", "time": "10:50 - 12:20", "start_time_raw": "10:50"},
            {"subject": "ФИЗИКА", "type": "", "room": "304", "time": "12:40 - 14:10", "start_time_raw": "12:40"},
        ],
        "СУББОТА": [
            {"subject": "ЭКОНОМИКА", "type": "", "room": "501", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
            {"subject": "ПРАВО", "type": "", "room": "502", "time": "10:50 - 12:20", "start_time_raw": "10:50"},
        ],
    }


def print_progress_bar(current: int, total: int, prefix: str = "Прогресс", suffix: str = "", length: int = 50):
    """Выводит прогресс-бар в консоль."""
    filled_length = int(length * current // total)
    bar = "█" * filled_length + "-" * (length - filled_length)
    percent = f"{100 * current // total}%"
    print(f"\r{prefix} |{bar}| {percent} {suffix}", end="", flush=True)
    if current == total:
        print()


async def main():
    """Основная функция"""
    # Создаем папку для результатов, если её нет
    output_dir = Path("generated_images")
    output_dir.mkdir(exist_ok=True)

    # Создаем тестовые данные
    test_data = create_test_schedule_data()

    # Генерируем изображения для обеих недель
    week_types = [("Нечётная неделя", "test_schedule_odd.png"), ("Чётная неделя", "test_schedule_even.png")]

    total_images = len(week_types)
    print(f"Начинаю генерацию {total_images} тестовых изображений...")
    print_progress_bar(0, total_images, "Генерация тестовых изображений", "0/2 изображений")

    for i, (week_type, filename) in enumerate(week_types):
        output_path = output_dir / filename
        print_progress_bar(i, total_images, "Генерация тестовых изображений", f"{i}/{total_images} изображений")

        success = await generate_schedule_image(
            schedule_data=test_data, week_type=week_type, group="TEST_GROUP", output_path=str(output_path), user_theme=None
        )

        if success:
            print_progress_bar(i + 1, total_images, "Генерация тестовых изображений", f"{i + 1}/{total_images} изображений")
        else:
            print(f"\n[ERROR] Ошибка при создании изображения: {output_path}")

    print("\n[SUCCESS] Генерация тестовых изображений завершена!")


if __name__ == "__main__":
    asyncio.run(main())
