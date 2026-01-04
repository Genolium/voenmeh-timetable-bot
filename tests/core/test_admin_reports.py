from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot

from core.admin_reports import AdminReportsGenerator, send_daily_reports, send_monthly_reports, send_weekly_reports


@pytest.fixture
def mock_bot():
    """Фикстура для мока Bot."""
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
def mock_user_data_manager():
    """Фикстура для мока UserDataManager."""
    manager = AsyncMock()
    manager.get_admin_users.return_value = [123456789, 987654321]
    manager.get_total_users_count.return_value = 1000
    manager.get_active_users_by_period.return_value = 500
    manager.get_subscribed_users_count.return_value = 800
    manager.get_unsubscribed_count.return_value = 50
    manager.get_subscription_breakdown.return_value = {
        "evening": 400,
        "morning": 300,
        "reminders": 200,
    }
    manager.get_top_groups.return_value = [
        ("О735Б", 150),
        ("О735А", 120),
        ("О734Б", 100),
        ("О734А", 90),
        ("О733Б", 80),
    ]
    manager.get_group_distribution.return_value = {
        "1 студент": 5,
        "2-5 студентов": 15,
        "6-10 студентов": 10,
        "11+ студентов": 8,
    }
    manager.get_new_users_count.return_value = 100
    return manager


@pytest.fixture
def admin_reports_generator(mock_bot, mock_user_data_manager):
    """Фикстура для AdminReportsGenerator."""
    return AdminReportsGenerator(mock_bot, mock_user_data_manager)


class TestAdminReportsGenerator:
    """Тесты для AdminReportsGenerator."""

    @pytest.mark.asyncio
    async def test_generate_daily_report_success(self, admin_reports_generator, mock_user_data_manager):
        """Тест успешной генерации ежедневного отчёта."""
        # Мокаем gather_stats
        mock_user_data_manager.gather_stats.return_value = (
            1000,
            500,
            700,
            900,
            800,
            50,  # total, dau, wau, mau, subscribed, unsubscribed
            {"evening": 400, "morning": 300, "reminders": 200},  # breakdown
            [("О735Б", 150), ("О735А", 120)],  # top_groups
            {"1 студент": 5, "2-5 студентов": 15},  # group_dist
        )

        # Мокаем Prometheus метрики
        with patch.object(admin_reports_generator, "_get_prometheus_metrics") as mock_metrics:
            mock_metrics.return_value = {
                "tasks_sent": 150,
                "errors_count": 2,
                "retries_count": 5,
                "schedule_updates": 3,
                "last_update": "22.10.2025 10:00",
            }

            report = await admin_reports_generator.generate_daily_report()

            # Проверяем, что отчёт содержит ключевые элементы
            assert "📊" in report
            assert "Ежедневный отчёт" in report
            assert "1000" in report  # total users
            assert "500" in report  # daily active
            assert "О735Б" in report  # top group
            assert "150" in report  # tasks sent

    @pytest.mark.asyncio
    async def test_generate_daily_report_error(self, admin_reports_generator, mock_user_data_manager):
        """Тест обработки ошибок при генерации ежедневного отчёта."""
        # Мокаем ошибку в gather_stats
        mock_user_data_manager.gather_stats.side_effect = Exception("Database error")

        report = await admin_reports_generator.generate_daily_report()

        assert "❌ Ошибка при генерации отчёта" in report

    @pytest.mark.asyncio
    async def test_generate_weekly_report_success(self, admin_reports_generator, mock_user_data_manager):
        """Тест успешной генерации еженедельного отчёта."""
        # Мокаем gather_stats
        mock_user_data_manager.gather_stats.return_value = (
            1000,  # total
            500,   # dau
            700,   # wau
            900,   # mau
            800,   # subscribed
            50,    # unsubscribed
            {"evening": 400, "morning": 300, "reminders": 200},  # breakdown
            [("О735Б", 150), ("О735А", 120)],  # top_groups
            {"1 студент": 5, "2-5 студентов": 15},  # group_dist
        )
        
        # Мокаем Prometheus метрики
        with patch.object(admin_reports_generator, "_get_prometheus_metrics_weekly") as mock_metrics:
            mock_metrics.return_value = {
                "tasks_sent_total": 1050,
                "errors_total": 14,
                "retries_total": 35,
                "schedule_updates": 21,
                "peak_activity": "Понедельник 08:00-09:00",
            }

            report = await admin_reports_generator.generate_weekly_report()

            # Проверяем, что отчёт содержит ключевые элементы
            assert "📊" in report
            assert "Еженедельный отчёт" in report
            assert "1050" in report  # tasks sent
            assert "Понедельник" in report  # peak activity

    @pytest.mark.asyncio
    async def test_generate_monthly_report_success(self, admin_reports_generator, mock_user_data_manager):
        """Тест успешной генерации ежемесячного отчёта."""
        # Мокаем gather_stats
        mock_user_data_manager.gather_stats.return_value = (
            1000,  # total
            500,   # dau
            700,   # wau
            900,   # mau
            800,   # subscribed
            50,    # unsubscribed
            {"evening": 400, "morning": 300, "reminders": 200},  # breakdown
            [("О735Б", 150), ("О735А", 120)],  # top_groups
            {"1 студент": 5, "2-5 студентов": 15},  # group_dist
        )
        
        # Мокаем Prometheus метрики
        with patch.object(admin_reports_generator, "_get_prometheus_metrics_monthly") as mock_metrics:
            mock_metrics.return_value = {
                "tasks_sent_total": 4200,
                "errors_total": 56,
                "retries_total": 140,
                "schedule_updates": 90,
                "avg_daily_users": 1250,
                "top_features": ["schedule_view", "settings", "feedback"],
            }

            report = await admin_reports_generator.generate_monthly_report()

            # Проверяем, что отчёт содержит ключевые элементы
            assert "📊" in report
            assert "Ежемесячный отчёт" in report
            assert "4200" in report  # tasks sent
            assert "schedule_view" in report  # top features

    def test_format_daily_report_structure(self, admin_reports_generator):
        """Тест структуры ежедневного отчёта."""
        today = "22.10.2025"
        prometheus_metrics = {
            "tasks_sent": 150,
            "errors_count": 2,
            "retries_count": 5,
            "schedule_updates": 3,
            "last_update": "22.10.2025 10:00",
        }

        report = admin_reports_generator._format_daily_report(
            today,
            1000,
            500,
            700,
            900,
            800,
            50,
            {"evening": 400, "morning": 300, "reminders": 200},
            [("О735Б", 150), ("О735А", 120)],
            {"1 студент": 5, "2-5 студентов": 15},
            prometheus_metrics,
        )

        # Проверяем структуру отчёта
        assert "📊" in report
        assert "👥" in report  # Пользователи
        assert "🔔" in report  # Подписки
        assert "🎓" in report  # Группы
        assert "⚡" in report  # Метрики
        assert "1000" in report
        assert "О735Б" in report

    def test_format_weekly_report_structure(self, admin_reports_generator):
        """Тест структуры еженедельного отчёта."""
        prometheus_metrics = {
            "tasks_sent_total": 1050,
            "errors_total": 14,
            "retries_total": 35,
            "schedule_updates": 21,
            "peak_activity": "Понедельник 08:00-09:00",
        }

        report = admin_reports_generator._format_weekly_report(
            100, 500, 1000, 800, [("О735Б", 150), ("О735А", 120)], prometheus_metrics
        )

        # Проверяем структуру отчёта
        assert "📊" in report
        assert "📈" in report  # Прирост
        assert "🔔" in report  # Подписки
        assert "⚡" in report  # Метрики
        assert "1050" in report
        assert "Понедельник" in report

    def test_format_monthly_report_structure(self, admin_reports_generator):
        """Тест структуры ежемесячного отчёта."""
        prometheus_metrics = {
            "tasks_sent_total": 4200,
            "errors_total": 56,
            "retries_total": 140,
            "schedule_updates": 90,
            "avg_daily_users": 1250,
            "top_features": ["schedule_view", "settings", "feedback"],
        }

        report = admin_reports_generator._format_monthly_report(
            200, 800, 1000, 800, [("О735Б", 150), ("О735А", 120)], prometheus_metrics
        )

        # Проверяем структуру отчёта
        assert "📊" in report
        assert "📈" in report  # Прирост
        assert "🏆" in report  # Популярные функции
        assert "4200" in report
        assert "schedule_view" in report


class TestAdminReportsSending:
    """Тесты для функций отправки отчётов."""

    @pytest.mark.asyncio
    async def test_send_daily_reports_success(self, mock_bot, mock_user_data_manager):
        """Тест успешной отправки ежедневных отчётов."""
        with patch("core.admin_reports.AdminReportsGenerator") as mock_generator_class:
            mock_generator = AsyncMock()
            mock_generator.generate_daily_report.return_value = "📊 Daily Report"
            mock_generator_class.return_value = mock_generator

            await send_daily_reports(mock_bot, mock_user_data_manager)

            # Проверяем, что генератор был создан и вызван
            mock_generator_class.assert_called_once_with(mock_bot, mock_user_data_manager)
            mock_generator.generate_daily_report.assert_called_once()

            # Проверяем, что сообщения были отправлены администраторам
            assert mock_bot.send_message.call_count == 2  # Два администратора

    @pytest.mark.asyncio
    async def test_send_daily_reports_no_admins(self, mock_bot, mock_user_data_manager):
        """Тест отправки отчётов при отсутствии администраторов."""
        mock_user_data_manager.get_admin_users.return_value = []

        await send_daily_reports(mock_bot, mock_user_data_manager)

        # Не должно отправлять сообщения
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_daily_reports_send_error(self, mock_bot, mock_user_data_manager):
        """Тест обработки ошибок при отправке ежедневных отчётов."""
        # Мокаем ошибку при отправке сообщения
        mock_bot.send_message.side_effect = Exception("Send message failed")

        # Не должно падать при ошибке отправки
        await send_daily_reports(mock_bot, mock_user_data_manager)

        # Должно попытаться отправить сообщения
        assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_send_weekly_reports_success(self, mock_bot, mock_user_data_manager):
        """Тест успешной отправки еженедельных отчётов."""
        with patch("core.admin_reports.AdminReportsGenerator") as mock_generator_class:
            mock_generator = AsyncMock()
            mock_generator.generate_weekly_report.return_value = "📊 Weekly Report"
            mock_generator_class.return_value = mock_generator

            await send_weekly_reports(mock_bot, mock_user_data_manager)

            # Проверяем, что генератор был создан и вызван
            mock_generator_class.assert_called_once_with(mock_bot, mock_user_data_manager)
            mock_generator.generate_weekly_report.assert_called_once()

            # Проверяем, что сообщения были отправлены администраторам
            assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_send_monthly_reports_success(self, mock_bot, mock_user_data_manager):
        """Тест успешной отправки ежемесячных отчётов."""
        with patch("core.admin_reports.AdminReportsGenerator") as mock_generator_class:
            mock_generator = AsyncMock()
            mock_generator.generate_monthly_report.return_value = "📊 Monthly Report"
            mock_generator_class.return_value = mock_generator

            await send_monthly_reports(mock_bot, mock_user_data_manager)

            # Проверяем, что генератор был создан и вызван
            mock_generator_class.assert_called_once_with(mock_bot, mock_user_data_manager)
            mock_generator.generate_monthly_report.assert_called_once()

            # Проверяем, что сообщения были отправлены администраторам
            assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_send_reports_exception_handling(self, mock_bot, mock_user_data_manager):
        """Тест обработки исключений в функциях отправки отчётов."""
        # Мокаем ошибку в генераторе отчёта
        with patch("core.admin_reports.AdminReportsGenerator") as mock_generator_class:
            mock_generator = AsyncMock()
            mock_generator.generate_daily_report.side_effect = Exception("Report generation failed")
            mock_generator_class.return_value = mock_generator

            # Не должно падать при ошибке генерации
            await send_daily_reports(mock_bot, mock_user_data_manager)
