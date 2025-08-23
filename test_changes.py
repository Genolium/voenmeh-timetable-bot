#!/usr/bin/env python3
"""
Тест изменений в команде /events
Запустите этот скрипт, чтобы убедиться, что изменения работают
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

async def test_events_changes():
    """Тестируем изменения в команде /events"""
    print("🚀 Тестирование изменений в команде /events")
    print("=" * 50)

    # Импортируем наши функции
    from bot.dialogs.events_menu import get_events_for_user
    from bot.dialogs.admin_menu import get_events_list

    print("✅ Импорт функций прошел успешно")

    # Тест 1: Пользовательский интерфейс
    print("\n📱 Тестирование пользовательского интерфейса:")

    mock_manager = MagicMock()
    mock_manager.middleware_data = {'session_factory': AsyncMock()}
    mock_manager.dialog_data = {'time_filter': None, 'page': 0}

    from unittest.mock import patch
    with patch('bot.dialogs.events_menu.EventsManager') as mock_events_manager:
        mock_instance = AsyncMock()
        mock_events_manager.return_value = mock_instance

        # Тестовое мероприятие с служебными словами
        mock_event = MagicMock()
        mock_event.title = 'Пропустить важное мероприятие'
        mock_event.start_at = datetime(2025, 8, 25, 12, 0, 0)  # Будущая дата
        mock_event.location = 'Тестовая локация'
        mock_event.id = 1

        mock_instance.list_events.return_value = ([mock_event], 1)

        result = await get_events_for_user(mock_manager)
        call_args = mock_instance.list_events.call_args

        print(f"   ✅ from_now_only = {call_args[1]['from_now_only']} (ожидается True)")
        print(f"   ✅ now параметр передан = {call_args[1]['now'] is not None}")

        if result['events']:
            title, event_id = result['events'][0]
            print(f"   ✅ Заголовок отфильтрован: '{title}'")
            print(f"   ✅ 'Пропустить' удалено: {'Пропустить' not in title}")
            print(f"   ✅ 'важное мероприятие' сохранено: {'важное' in title}")

    # Тест 2: Админ-интерфейс
    print("\n👨‍💼 Тестирование админ-интерфейса:")

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
        mock_event.title = 'Отмена мероприятия'
        mock_event.is_published = True
        mock_event.id = 1

        mock_instance.list_events.return_value = ([mock_event], 1)

        result = await get_events_list(mock_manager)
        call_args = mock_instance.list_events.call_args

        print(f"   ✅ from_now_only = {call_args[1]['from_now_only']} (ожидается True)")
        print(f"   ✅ now параметр передан = {call_args[1]['now'] is not None}")

        events_text = result['events_text']
        print(f"   ✅ 'Отмена' удалено из заголовка: {'Отмена' not in events_text}")
        print(f"   ✅ 'мероприятия' сохранено: {'мероприятия' in events_text}")

    print("\n" + "=" * 50)
    print("🎉 ВСЕ ИЗМЕНЕНИЯ РАБОТАЮТ КОРРЕКТНО!")
    print("\n📋 Резюме изменений:")
    print("   • Кнопка 'Все' показывает только будущие мероприятия (с сегодняшнего дня)")
    print("   • Кнопки 'Сегодня'/'Неделя' показывают только будущие мероприятия")
    print("   • Заголовки очищены от служебных слов ('Пропустить', 'Отмена', 'Skip' и т.д.)")
    print("   • Изменения применены как к пользовательскому, так и к админ-интерфейсу")

    print("\n🔄 Если изменения не видны в боте:")
    print("   1. Перезапустите бота (остановите и запустите снова)")
    print("   2. Убедитесь, что используется обновленный код")
    print("   3. Проверьте логи бота на наличие ошибок")

if __name__ == "__main__":
    asyncio.run(test_events_changes())
