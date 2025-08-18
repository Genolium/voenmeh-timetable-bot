#!/usr/bin/env python3
"""
Скрипт для генерации всех изображений расписаний с подробным прогресс-баром.
Генерирует изображения для всех групп на обе недели (четную и нечетную).
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime
import time

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent))

from core.image_generator import generate_schedule_image
from core.parser import fetch_and_parse_all_schedules
from core.manager import TimetableManager
from core.config import MOSCOW_TZ

def print_progress_bar(current: int, total: int, prefix: str = "Прогресс", suffix: str = "", length: int = 50):
    """Выводит прогресс-бар в консоль."""
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    percent = f"{100 * current // total}%"
    print(f'\r{prefix} |{bar}| {percent} {suffix}', end='', flush=True)
    if current == total:
        print()

def print_detailed_progress(current: int, total: int, group: str, week_type: str, step: str):
    """Выводит детальный прогресс для отдельного изображения."""
    filled_length = int(30 * current // total)
    bar = '█' * filled_length + '-' * (30 - filled_length)
    percent = f"{100 * current // total}%"
    print(f'\r  {group} ({week_type}) |{bar}| {percent} {step}', end='', flush=True)
    if current == total:
        print()

def get_file_size_mb(file_path: str) -> float:
    """Возвращает размер файла в мегабайтах."""
    try:
        size_bytes = os.path.getsize(file_path)
        return round(size_bytes / (1024 * 1024), 2)
    except:
        return 0

async def generate_all_schedule_images():
    """Генерирует изображения расписаний для всех групп на обе недели."""
    
    print("🚀 Запуск генерации всех изображений расписаний")
    print("=" * 60)
    
    # Создаем папку для результатов
    output_dir = Path("generated_images")
    output_dir.mkdir(exist_ok=True)
    
    # Шаг 1: Получаем данные расписания
    print("📡 Получение данных расписания с сервера...")
    print_progress_bar(1, 4, "Подготовка", "Получение данных")
    
    try:
        schedule_data = await fetch_and_parse_all_schedules()
        if not schedule_data:
            print("\n❌ Ошибка: Не удалось получить данные расписания")
            return
        
        # Создаем менеджер расписания
        from redis.asyncio import Redis
        redis_client = Redis.from_url("redis://localhost:6379/0")
        manager = TimetableManager(schedule_data, redis_client)
        
        # Получаем список всех групп
        groups = list(schedule_data.keys())
        groups = [g for g in groups if not g.startswith('__')]  # Исключаем служебные ключи
        
        print_progress_bar(2, 4, "Подготовка", f"Найдено {len(groups)} групп")
        
        # Шаг 2: Подготавливаем список задач
        print_progress_bar(3, 4, "Подготовка", "Подготовка списка задач")
        
        tasks = []
        week_types = [
            ("Нечётная неделя", "odd"),
            ("Чётная неделя", "even")
        ]
        
        for group in groups:
            for week_name, week_key in week_types:
                # Проверяем, есть ли расписание для этой группы и недели
                group_schedule = schedule_data.get(group, {})
                week_schedule = group_schedule.get(week_key, {})
                
                if week_schedule:  # Только если есть расписание
                    output_filename = f"{group}_{week_key}.png"
                    output_path = output_dir / output_filename
                    tasks.append((group, week_schedule, week_name, week_key, str(output_path)))
        
        print_progress_bar(4, 4, "Подготовка", f"Подготовлено {len(tasks)} изображений")
        
        # Шаг 3: Генерируем изображения
        print(f"\n🎨 Начинаю генерацию {len(tasks)} изображений...")
        print("=" * 60)
        
        successful = 0
        failed = 0
        total_size_mb = 0
        start_time = time.time()
        
        for i, (group, week_schedule, week_name, week_key, output_path) in enumerate(tasks):
            # Показываем общий прогресс
            print_progress_bar(i, len(tasks), "Общий прогресс", f"{i}/{len(tasks)} изображений")
            
            # Показываем детальный прогресс для текущего изображения
            print_detailed_progress(1, 5, group, week_name, "Подготовка шаблона")
            
            try:
                # Генерируем изображение
                success = await generate_schedule_image(
                    schedule_data=week_schedule,
                    week_type=week_name,
                    group=group,
                    output_path=output_path
                )
                
                if success and os.path.exists(output_path):
                    successful += 1
                    file_size = get_file_size_mb(output_path)
                    total_size_mb += file_size
                    print_detailed_progress(5, 5, group, week_name, f"✅ Готово ({file_size} МБ)")
                else:
                    failed += 1
                    print_detailed_progress(5, 5, group, week_name, "❌ Ошибка")
                    
            except Exception as e:
                failed += 1
                print_detailed_progress(5, 5, group, week_name, f"❌ Ошибка: {str(e)[:30]}")
        
        # Шаг 4: Итоговая статистика
        end_time = time.time()
        duration = round(end_time - start_time, 2)
        
        print("\n" + "=" * 60)
        print("📊 ИТОГОВАЯ СТАТИСТИКА")
        print("=" * 60)
        print(f"✅ Успешно сгенерировано: {successful} изображений")
        print(f"❌ Ошибок: {failed} изображений")
        print(f"📁 Общий размер: {total_size_mb} МБ")
        print(f"⏱️ Время выполнения: {duration} секунд")
        print(f"🚀 Средняя скорость: {round(successful/duration, 2) if duration > 0 else 0} изображений/сек")
        
        if successful > 0:
            print(f"\n📂 Изображения сохранены в: {output_dir.absolute()}")
            print(f"💾 Средний размер изображения: {round(total_size_mb/successful, 2)} МБ")
        
        # Показываем топ-5 самых больших файлов
        if successful > 0:
            print(f"\n📈 Топ-5 самых больших изображений:")
            files_with_sizes = []
            for file_path in output_dir.glob("*.png"):
                size_mb = get_file_size_mb(str(file_path))
                files_with_sizes.append((file_path.name, size_mb))
            
            files_with_sizes.sort(key=lambda x: x[1], reverse=True)
            for i, (filename, size) in enumerate(files_with_sizes[:5]):
                print(f"  {i+1}. {filename}: {size} МБ")
        
        print("\n🎉 Генерация завершена!")
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()

async def generate_sample_images():
    """Генерирует тестовые изображения для демонстрации."""
    
    print("🧪 Генерация тестовых изображений")
    print("=" * 40)
    
    # Создаем тестовые данные
    test_data = {
        "ПОНЕДЕЛЬНИК": [
            {"subject": "ФИЗИКА", "type": "лаб", "room": "322", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
            {"subject": "МАТЕМАТИКА", "type": "лек", "room": "101", "time": "10:50 - 12:20", "start_time_raw": "10:50"},
        ],
        "ВТОРНИК": [
            {"subject": "ПРОГРАММИРОВАНИЕ", "type": "пр", "room": "205", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
        ],
        "СРЕДА": [
            {"subject": "ХИМИЯ", "type": "лаб", "room": "301", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
        ],
        "ЧЕТВЕРГ": [
            {"subject": "ИСТОРИЯ", "type": "лек", "room": "201", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
        ],
        "ПЯТНИЦА": [
            {"subject": "АНГЛИЙСКИЙ", "type": "пр", "room": "203", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
        ],
        "СУББОТА": [
            {"subject": "ФИЗКУЛЬТУРА", "type": "", "room": "", "time": "9:00 - 10:30", "start_time_raw": "9:00"},
        ]
    }
    
    output_dir = Path("generated_images")
    output_dir.mkdir(exist_ok=True)
    
    week_types = [
        ("Нечётная неделя", "test_sample_odd.png"),
        ("Чётная неделя", "test_sample_even.png")
    ]
    
    print(f"Генерирую {len(week_types)} тестовых изображений...")
    
    for i, (week_type, filename) in enumerate(week_types):
        output_path = output_dir / filename
        print_progress_bar(i, len(week_types), "Тестовые изображения", f"{i}/{len(week_types)}")
        
        success = await generate_schedule_image(
            schedule_data=test_data,
            week_type=week_type,
            group="TEST_SAMPLE",
            output_path=str(output_path)
        )
        
        if success:
            file_size = get_file_size_mb(str(output_path))
            print_progress_bar(i + 1, len(week_types), "Тестовые изображения", f"{i + 1}/{len(week_types)} ({file_size} МБ)")
        else:
            print(f"\n❌ Ошибка при создании {filename}")
    
    print("\n✅ Тестовые изображения готовы!")

async def main():
    """Основная функция."""
    print("🎯 Генератор изображений расписаний")
    print("=" * 50)
    print("1. Генерировать все изображения (реальные данные)")
    print("2. Генерировать тестовые изображения")
    print("3. Выход")
    
    while True:
        try:
            choice = input("\nВыберите опцию (1-3): ").strip()
            
            if choice == "1":
                await generate_all_schedule_images()
                break
            elif choice == "2":
                await generate_sample_images()
                break
            elif choice == "3":
                print("👋 До свидания!")
                break
            else:
                print("❌ Неверный выбор. Попробуйте снова.")
        except KeyboardInterrupt:
            print("\n\n👋 Генерация прервана пользователем.")
            break
        except Exception as e:
            print(f"\n❌ Ошибка: {e}")
            break

if __name__ == "__main__":
    asyncio.run(main())
