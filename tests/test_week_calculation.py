#!/usr/bin/env python3
"""
Тестовый скрипт для проверки расчета номера недели с начала семестра.
"""

from datetime import date

from bot.text_formatters import calculate_semester_week_number_fallback as calculate_semester_week_number


def test_week_calculation():
    """Тестируем функцию calculate_semester_week_number"""

    test_cases = [
        # (дата, ожидаемый результат)
        (date(2024, 9, 1), 1),  # 1 сентября - неделя 1
        (date(2024, 9, 7), 1),  # 7 сентября - еще неделя 1
        (date(2024, 9, 8), 2),  # 8 сентября - неделя 2
        (date(2024, 9, 15), 2),  # 15 сентября - еще неделя 2
        (date(2024, 9, 16), 3),  # 16 сентября - неделя 3
        (date(2024, 12, 31), 17),  # Конец декабря - примерно неделя 17
        (date(2025, 1, 1), 18),  # 1 января - неделя 18
        (date(2025, 8, 31), 52),  # 31 августа - но функция должна ограничить до 32
        (date(2024, 8, 31), 1),  # 31 августа - до начала семестра, неделя 1
    ]

    print("🧪 Тестирование функции calculate_semester_week_number:")
    print("=" * 60)

    for test_date, expected in test_cases:
        result = calculate_semester_week_number(test_date)
        status = "✅" if result == expected else "❌"
        print("<20")

    print("\n" + "=" * 60)
    print("Тестирование завершено!")


if __name__ == "__main__":
    test_week_calculation()
