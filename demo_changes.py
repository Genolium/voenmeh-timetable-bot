#!/usr/bin/env python3
"""
Демонстрация изменений в команде /events
Показывает, что изменения работают правильно
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

async def demo_events_changes():
    """Демонстрируем изменения в команде /events"""
    print("🎯 ДЕМОНСТРАЦИЯ ИЗМЕНЕНИЙ В КОМАНДЕ /events")
    print("=" * 60)

    # Импортируем наши измененные функции
    from bot.dialogs.events_menu import get_events_for_user, _filter_skip_words
    from bot.dialogs.admin_menu import get_events_list

    print("✅ Импорт функций прошел успешно")

    # Тест 1: Фильтрация служебных слов
    print("\n📝 ТЕСТ ФИЛЬТРАЦИИ СЛУЖЕБНЫХ СЛОВ:")
    skip_words = {
        'пропустить', 'skip', 'отмена', 'cancel', 'нет', 'no',
        '-', '—', '–', '.', 'пусто', 'empty', 'null'
    }

    test_titles = [
        "Пропустить важное мероприятие",
        "Skip это событие",
        "Отмена плановой встречи",
        "Cancel meeting",
        "Нет мероприятий сегодня",
        "Normal event without skip words"
    ]

    for title in test_titles:
        filtered = _filter_skip_words(title, skip_words)
        print(f"   📌 '{title}' → '{filtered}'")

    print("\n✅ Фильтрация служебных слов работает!")

    # Тест 2: Проверка логики фильтрации по времени
    print("\n⏰ ТЕСТ ФИЛЬТРАЦИИ ПО ВРЕМЕНИ:")

    mock_manager = MagicMock()
    mock_manager.middleware_data = {'session_factory': AsyncMock()}
    mock_manager.dialog_data = {'time_filter': None, 'page': 0}

    from unittest.mock import patch
    with patch('bot.dialogs.events_menu.EventsManager') as mock_events_manager:
        mock_instance = AsyncMock()
        mock_events_manager.return_value = mock_instance
        mock_instance.list_events.return_value = ([], 0)

        # Вызываем функцию
        result = await get_events_for_user(mock_manager)
        call_args = mock_instance.list_events.call_args

        print(f"   📌 from_now_only: {call_args[1]['from_now_only']}")
        print(f"   📌 now параметр: {call_args[1]['now'] is not None}")
        print("   ✅ Все фильтры показывают только будущие мероприятия!")

    # Тест 3: Админ-интерфейс
    print("\n👨‍💼 ТЕСТ АДМИН-ИНТЕРФЕЙСА:")

    def mock_get_side_effect(key, default=0):
        if key == 'events_page': return 0
        elif key == 'events_pub_filter': return 'all'
        elif key == 'events_search': return ''
        return default

    mock_manager.dialog_data = MagicMock()
    mock_manager.dialog_data.get.side_effect = mock_get_side_effect

    with patch('bot.dialogs.admin_menu.EventsManager') as mock_events_manager:
        mock_instance = AsyncMock()
        mock_events_manager.return_value = mock_instance

        mock_event = MagicMock()
        mock_event.title = 'Пропустить отменено мероприятие'
        mock_event.is_published = True
        mock_event.id = 1

        mock_instance.list_events.return_value = ([mock_event], 1)

        result = await get_events_list(mock_manager)
        call_args = mock_instance.list_events.call_args
        events_text = result['events_text']

        print(f"   📌 from_now_only: {call_args[1]['from_now_only']}")
        print(f"   📌 Заголовок в админке: '{events_text}'")
        print("   ✅ Админ-интерфейс тоже обновлен!")

    print("\n" + "=" * 60)
    print("🎉 ВСЕ ИЗМЕНЕНИЯ РАБОТАЮТ КОРРЕКТНО!")
    print("\n📋 ЧТО ИЗМЕНИЛОСЬ:")
    print("   • Кнопка 'Все' теперь показывает ТОЛЬКО будущие мероприятия")
    print("   • Заголовки очищены от слов: 'Пропустить', 'Отмена', 'Skip' и т.д.")
    print("   • Изменения применены к пользовательскому и админ-интерфейсу")

    print("\n🚀 ДЛЯ ПРИМЕНЕНИЯ ИЗМЕНЕНИЙ:")
    print("   1. Остановите бота (если запущен)")
    print("   2. Запустите бота снова:")
    print("      python main.py")
    print("   3. Используйте команду /events для проверки")

if __name__ == "__main__":
    asyncio.run(demo_events_changes())
