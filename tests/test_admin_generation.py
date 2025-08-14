#!/usr/bin/env python3
"""
Простой тест для проверки функции генерации всех изображений в админ-панели.
"""

import asyncio
import sys
from pathlib import Path

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent))

from bot.dialogs.admin_menu import generate_all_images_background
from core.manager import TimetableManager
from redis.asyncio import Redis

async def test_generation():
    """Тестирует функцию генерации всех изображений."""
    
    print("🧪 Тест функции генерации всех изображений")
    print("=" * 50)
    
    try:
        # Подключаемся к Redis
        redis_client = Redis.from_url("redis://localhost:6379/0")
        
        # Создаем менеджер расписания
        timetable_manager = await TimetableManager.create(redis_client=redis_client)
        
        if not timetable_manager:
            print("❌ Не удалось создать TimetableManager")
            return
        
        print("✅ TimetableManager создан успешно")
        
        # Проверяем количество групп
        all_groups = list(timetable_manager._schedules.keys())
        all_groups = [g for g in all_groups if not g.startswith('__')]
        
        print(f"📊 Найдено групп: {len(all_groups)}")
        
        if len(all_groups) == 0:
            print("❌ Нет групп для тестирования")
            return
        
        # Подсчитываем задачи
        week_types = [
            ("Нечётная неделя", "odd"),
            ("Чётная неделя", "even")
        ]
        
        tasks = []
        for group in all_groups[:3]:  # Берем только первые 3 группы для теста
            for week_name, week_key in week_types:
                group_schedule = timetable_manager._schedules.get(group.upper(), {})
                week_schedule = group_schedule.get(week_key, {})
                
                if week_schedule:
                    tasks.append((group, week_schedule, week_name, week_key))
        
        print(f"📋 Задач для генерации: {len(tasks)}")
        
        if len(tasks) == 0:
            print("❌ Нет задач для генерации")
            return
        
        # Создаем мок-бот для тестирования
        class MockBot:
            async def edit_message_text(self, text, chat_id, message_id, parse_mode=None, reply_markup=None):
                print(f"📝 Обновление статуса: {text[:100]}...")
            
            async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
                print(f"📤 Отправка сообщения: {text[:100]}...")
        
        mock_bot = MockBot()
        
        print("\n🚀 Запускаем тестовую генерацию...")
        
        # Запускаем генерацию для небольшого количества задач
        await generate_all_images_background(
            bot=mock_bot,
            admin_id=123456789,  # Тестовый ID админа
            status_msg_id=1,
            timetable_manager=timetable_manager
        )
        
        print("\n✅ Тест завершен успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка теста: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_generation())
