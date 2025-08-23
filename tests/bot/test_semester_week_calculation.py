"""
Тесты для функции calculate_semester_week_number с использованием настроек семестров из БД.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, timedelta

from bot.text_formatters import calculate_semester_week_number, calculate_semester_week_number_fallback


class TestSemesterWeekCalculation:
    """Тесты для расчета номера недели семестра."""

    @pytest.mark.asyncio
    async def test_semester_week_with_db_settings(self):
        """Тест расчета недели с настройками из БД."""
        # Настраиваем mock для session_factory
        mock_session_factory = AsyncMock()

        # Настраиваем mock для SemesterSettingsManager
        with patch('bot.text_formatters.SemesterSettingsManager') as mock_settings_manager:
            mock_instance = AsyncMock()
            mock_settings_manager.return_value = mock_instance

            # Устанавливаем даты семестров: осенний 1 сентября, весенний 9 февраля
            fall_start = date(2024, 9, 1)
            spring_start = date(2024, 2, 9)
            mock_instance.get_semester_settings.return_value = (fall_start, spring_start)

            # Тест: дата в осеннем семестре
            test_date = date(2024, 9, 15)  # 15 сентября - 3-я неделя осеннего семестра
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            assert result == 3

            # Тест: дата в весеннем семестре
            test_date = date(2024, 2, 19)  # 19 февраля - 2-я неделя весеннего семестра
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            assert result == 2

            # Тест: дата в весеннем семестре (не между семестрами) - недели считаются
            test_date = date(2024, 3, 15)  # 15 марта - 6-я неделя весеннего семестра
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            assert result == 6  # 6-я неделя весеннего семестра

    @pytest.mark.asyncio
    async def test_semester_week_fallback_when_no_settings(self):
        """Тест использования значений по умолчанию, когда настройки не установлены."""
        # Настраиваем mock для session_factory
        mock_session_factory = AsyncMock()

        # Настраиваем mock для SemesterSettingsManager
        with patch('bot.text_formatters.SemesterSettingsManager') as mock_settings_manager:
            mock_instance = AsyncMock()
            mock_settings_manager.return_value = mock_instance

            # Настройки не установлены
            mock_instance.get_semester_settings.return_value = None

            # Тест с датой в осеннем семестре
            test_date = date(2024, 9, 15)  # 15 сентября
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            assert result == 3  # 3-я неделя с 1 сентября

    @pytest.mark.asyncio
    async def test_semester_week_boundary_cases(self):
        """Тест граничных случаев."""
        # Настраиваем mock для session_factory
        mock_session_factory = AsyncMock()

        # Настраиваем mock для SemesterSettingsManager
        with patch('bot.text_formatters.SemesterSettingsManager') as mock_settings_manager:
            mock_instance = AsyncMock()
            mock_settings_manager.return_value = mock_instance

            # Устанавливаем даты семестров
            fall_start = date(2024, 9, 1)
            spring_start = date(2024, 2, 9)
            mock_instance.get_semester_settings.return_value = (fall_start, spring_start)

            # Тест: первый день осеннего семестра
            test_date = date(2024, 9, 1)
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            assert result == 1

            # Тест: первый день весеннего семестра
            test_date = date(2024, 2, 9)
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            assert result == 1

    @pytest.mark.asyncio
    async def test_semester_week_date_before_fall_semester(self):
        """Тест даты до начала осеннего семестра."""
        # Настраиваем mock для session_factory
        mock_session_factory = AsyncMock()

        # Настраиваем mock для SemesterSettingsManager
        with patch('bot.text_formatters.SemesterSettingsManager') as mock_settings_manager:
            mock_instance = AsyncMock()
            mock_settings_manager.return_value = mock_instance

            # Устанавливаем даты семестров для 2024 года
            fall_start = date(2024, 9, 1)
            spring_start = date(2024, 2, 9)
            mock_instance.get_semester_settings.return_value = (fall_start, spring_start)

            # Тест: дата до осеннего семестра - недели не считаются
            test_date = date(2024, 1, 15)
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            # Дата находится до начала осеннего семестра, поэтому недели не считаются
            assert result == 0

            # Тест: дата между семестрами (после весеннего, до осеннего) - недели не считаются
            test_date = date(2024, 7, 15)  # 15 июля - между весенним и осенним семестрами
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            # Дата находится между семестрами, поэтому недели не считаются
            assert result == 0

    @pytest.mark.asyncio
    async def test_semester_week_exception_handling(self):
        """Тест обработки исключений."""
        # Настраиваем mock для session_factory, который вызовет исключение
        mock_session_factory = AsyncMock()

        # Настраиваем mock для SemesterSettingsManager
        with patch('bot.text_formatters.SemesterSettingsManager') as mock_settings_manager:
            mock_instance = AsyncMock()
            mock_settings_manager.return_value = mock_instance

            # Имитируем исключение
            mock_instance.get_semester_settings.side_effect = Exception("Database error")

            # Функция должна обработать исключение и вернуться к fallback логике
            test_date = date(2024, 9, 15)
            result = await calculate_semester_week_number(test_date, mock_session_factory)

            # Должно использовать fallback логику (1 сентября)
            assert result == 3  # 3-я неделя с 1 сентября

    def test_semester_week_fallback_function(self):
        """Тест fallback функции для обратной совместимости."""
        # Тест: 1 сентября
        test_date = date(2024, 9, 1)
        result = calculate_semester_week_number_fallback(test_date)
        assert result == 1

        # Тест: 8 сентября (2-я неделя)
        test_date = date(2024, 9, 8)
        result = calculate_semester_week_number_fallback(test_date)
        assert result == 2

        # Тест: дата до 1 сентября (должен использовать предыдущий год)
        test_date = date(2024, 8, 31)
        result = calculate_semester_week_number_fallback(test_date)
        # 31 августа 2023 года - это 53-я неделя 2023 года
        assert result == 53
