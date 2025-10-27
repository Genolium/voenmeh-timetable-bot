#!/usr/bin/env python3
"""
Тестовый скрипт для проверки новой логики подсчета недель семестров.
"""

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

from bot.text_formatters import calculate_semester_week_number, calculate_semester_week_number_fallback


async def test_semester_logic():
    """Тестируем новую логику подсчета недель семестров."""

    # Настраиваем mock для session_factory
    mock_session_factory = AsyncMock()

    # Настраиваем mock для SemesterSettingsManager
    with patch("bot.text_formatters.SemesterSettingsManager") as mock_settings_manager:
        mock_instance = AsyncMock()
        mock_settings_manager.return_value = mock_instance

        # Устанавливаем даты семестров
        fall_start = date(2024, 9, 1)  # Осенний семестр начинается 1 сентября
        spring_start = date(2024, 2, 9)  # Весенний семестр начинается 9 февраля
        mock_instance.get_semester_settings.return_value = (fall_start, spring_start)

        test_cases = [
            # Даты ДО начала весеннего семестра - недели не считаются
            (date(2024, 1, 15), 0, "До начала весеннего семестра"),
            # Даты В весеннем семестре - недели считаются
            (date(2024, 2, 9), 1, "Первый день весеннего семестра"),
            (date(2024, 2, 19), 2, "2-я неделя весеннего семестра"),
            (date(2024, 3, 15), 6, "6-я неделя весеннего семестра"),
            # Даты МЕЖДУ семестрами (после весеннего, до осеннего) - недели не считаются
            (date(2024, 7, 15), 0, "Между семестрами (июль)"),
            # Даты В осеннем семестре - недели считаются
            (date(2024, 9, 1), 1, "Первый день осеннего семестра"),
            (date(2024, 9, 15), 3, "3-я неделя осеннего семестра"),
            (date(2024, 10, 15), 7, "7-я неделя осеннего семестра"),
        ]

        print("🧪 Тестирование новой логики подсчета недель семестров")
        print("=" * 60)

        for test_date, expected, description in test_cases:
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            status = "✅" if result == expected else "❌"
            print("20")

        print("\n📊 Резюме:")
        print("- Недели считаются только в активных семестрах")
        print("- До начала семестров и между семестрами недели = 0")
        print("- В активных семестрах недели считаются от даты начала")


if __name__ == "__main__":
    asyncio.run(test_semester_logic())
