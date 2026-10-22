#!/usr/bin/env python3
"""
Тестовый скрипт для проверки обработки выпустившихся групп
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path


async def test_graduated_groups_handling():
    """Тестирует обработку выпустившихся групп."""

    print("🧪 Тестирование обработки выпустившихся групп")
    print("=" * 60)

    # Симулируем данные пользователей с группами
    users_with_groups = [
        (123456789, "О735Б"),  # Существующая группа
        (987654321, "А101С"),  # Существующая группа
        (111222333, "ВЫПУСКНИКИ_2024"),  # Выпустившаяся группа
        (444555666, "СТАРАЯ_ГРУППА"),  # Выпустившаяся группа
        (777888999, "Е211Б"),  # Существующая группа
    ]

    # Симулируем актуальные группы
    current_groups = {"О735Б", "А101С", "Е211Б", "А102С", "О736Б", "Е212Б"}

    print(f"📊 Пользователей для проверки: {len(users_with_groups)}")
    print(f"📊 Актуальных групп: {len(current_groups)}")

    # Находим выпустившиеся группы
    graduated_groups = set()
    affected_users = []

    for user_id, group_name in users_with_groups:
        if group_name and group_name.upper() not in current_groups:
            graduated_groups.add(group_name.upper())
            affected_users.append((user_id, group_name))

    print(f"\n🔍 Результаты проверки:")
    print(f"✅ Актуальные группы пользователей: {len(users_with_groups) - len(affected_users)}")
    print(f"⚠️ Выпустившиеся группы: {len(graduated_groups)}")
    print(f"👥 Затронуто пользователей: {len(affected_users)}")

    if graduated_groups:
        print(f"\n📋 Выпустившиеся группы:")
        for group in graduated_groups:
            print(f"   - {group}")

    if affected_users:
        print(f"\n👤 Затронутые пользователи:")
        for user_id, group in affected_users:
            print(f"   - Пользователь {user_id}: группа {group}")

    # Симулируем сообщение для пользователей
    if affected_users:
        print(f"\n📨 Пример сообщения для пользователей:")
        available_groups = sorted(list(current_groups))
        message_text = (
            f"⚠️ <b>Внимание!</b>\n\n"
            f"Группа <b>{affected_users[0][1]}</b> больше не существует в расписании.\n"
            f"Возможно, группа выпустилась или была переименована.\n\n"
            f"Пожалуйста, выберите новую группу:\n"
            f"<code>/start</code> - для выбора группы\n\n"
            f"Доступные группы: {', '.join(available_groups[:10])}"
            + (f"\n... и еще {len(available_groups) - 10} групп" if len(available_groups) > 10 else "")
        )
        print(message_text)

    # Симулируем статистику
    stats_data = {
        "timestamp": datetime.now().isoformat(),
        "graduated_groups": list(graduated_groups),
        "affected_users": len(affected_users),
        "notified_users": len(affected_users),  # Предполагаем, что все уведомлены
        "current_groups_count": len(current_groups),
        "total_users_checked": len(users_with_groups),
    }

    print(f"\n📊 Статистика для сохранения:")
    print(json.dumps(stats_data, indent=2, ensure_ascii=False))

    print(f"\n🎉 Тестирование завершено!")


if __name__ == "__main__":
    asyncio.run(test_graduated_groups_handling())
