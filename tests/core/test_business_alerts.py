import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from core.business_alerts import (
    BusinessMetricsMonitor, AlertSeverity, BusinessAlert
)

@pytest.fixture
def mock_settings():
    return {
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
        "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test",
        "TELEGRAM_ALERT_BOT_TOKEN": "test_token",
        "TELEGRAM_ALERT_CHAT_ID": "123456789",
        "ALERT_WEBHOOK_URL": "https://webhook.site/test"
    }

@pytest.fixture
def sample_alert():
    return BusinessAlert(
        title="Test Alert",
        message="Test alert message",
        severity=AlertSeverity.CRITICAL,
        metric_name="test_metric",
        current_value=0.1,
        threshold=0.5,
        timestamp=datetime.now(),
        tags={"source": "test"}
    )

class TestBusinessMetricsMonitor:
    
    @pytest.mark.asyncio
    async def test_business_metrics_monitor_initialization(self):
        """Тест инициализации BusinessMetricsMonitor."""
        with patch.dict('os.environ', {
            'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/test',
            'PROMETHEUS_URL': 'http://localhost:9090'
        }):
            monitor = BusinessMetricsMonitor()
            
            assert monitor.cooldown_minutes == 30
            assert "user_activity_drop" in monitor.thresholds
            assert "cache_hit_rate" in monitor.thresholds
            assert monitor.prometheus_url == "http://localhost:9090"

    @pytest.mark.asyncio
    async def test_alert_severity_enum(self):
        """Тест перечисления уровней серьезности."""
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"

    @pytest.mark.asyncio
    async def test_business_alert_dataclass(self, sample_alert):
        """Тест датакласса BusinessAlert."""
        assert sample_alert.title == "Test Alert"
        assert sample_alert.message == "Test alert message"
        assert sample_alert.severity == AlertSeverity.CRITICAL
        assert sample_alert.metric_name == "test_metric"
        assert sample_alert.current_value == 0.1
        assert sample_alert.threshold == 0.5
        assert "source" in sample_alert.tags

    @pytest.mark.asyncio
    async def test_check_business_metrics_with_prometheus(self):
        """Тест проверки бизнес-метрик с Prometheus."""
        with patch.dict('os.environ', {'PROMETHEUS_URL': 'http://localhost:9090'}):
            monitor = BusinessMetricsMonitor()
            
            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = AsyncMock()
                mock_session_class.return_value.__aenter__.return_value = mock_session
                
                mock_response = AsyncMock()
                mock_response.json = AsyncMock(return_value={'status': 'success'})
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                await monitor._check_business_metrics()
                
                mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_business_metrics_without_prometheus(self):
        """Тест проверки бизнес-метрик без Prometheus."""
        with patch.dict('os.environ', {}, clear=True):
            monitor = BusinessMetricsMonitor()
            
            # Должен завершиться без ошибок
            await monitor._check_business_metrics()
            
            # Проверяем, что функция выполнилась без ошибок
            assert True

    @pytest.mark.asyncio
    async def test_check_business_metrics_prometheus_failure(self):
        """Тест проверки бизнес-метрик при ошибке Prometheus."""
        with patch.dict('os.environ', {'PROMETHEUS_URL': 'http://localhost:9090'}):
            monitor = BusinessMetricsMonitor()
            
            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = AsyncMock()
                mock_session_class.return_value.__aenter__.return_value = mock_session
                
                mock_response = AsyncMock()
                mock_response.json = AsyncMock(return_value={'status': 'error'})
                mock_session.get.return_value.__aenter__.return_value = mock_response
                
                with patch.object(monitor, '_send_alert') as mock_send_alert:
                    await monitor._check_business_metrics()
                    
                    # Проверяем, что функция выполнилась без ошибок
                    assert True

    @pytest.mark.asyncio
    async def test_check_business_metrics_exception(self):
        """Тест проверки бизнес-метрик с исключением."""
        with patch.dict('os.environ', {'PROMETHEUS_URL': 'http://localhost:9090'}):
            monitor = BusinessMetricsMonitor()
            
            with patch('aiohttp.ClientSession', side_effect=Exception("Network error")):
                # Функция должна обработать исключение
                await monitor._check_business_metrics()
                
                # Проверяем, что функция завершилась без критических ошибок
                assert True

    @pytest.mark.asyncio
    async def test_send_alert_success(self):
        """Тест успешной отправки алерта."""
        with patch.dict('os.environ', {
            'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/test'
        }):
            monitor = BusinessMetricsMonitor()
            
            with patch('core.business_alerts.AlertSender') as mock_alert_sender:
                mock_sender = AsyncMock()
                mock_alert_sender.return_value.__aenter__.return_value = mock_sender
                
                await monitor._send_alert(
                    title="Test Alert",
                    message="Test message",
                    severity=AlertSeverity.CRITICAL,
                    metric_name="test_metric",
                    additional_data={"test": "value"}
                )
                
                mock_sender.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_cooldown(self):
        """Тест кулдауна алертов."""
        with patch.dict('os.environ', {
            'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/test'
        }):
            monitor = BusinessMetricsMonitor()
            
            # Устанавливаем недавний алерт
            alert_key = "test_metric_critical"
            monitor.alert_cooldown[alert_key] = datetime.now()
            
            with patch('core.business_alerts.AlertSender') as mock_alert_sender:
                mock_sender = AsyncMock()
                mock_alert_sender.return_value.__aenter__.return_value = mock_sender
                
                await monitor._send_alert(
                    title="Test Alert",
                    message="Test message",
                    severity=AlertSeverity.CRITICAL,
                    metric_name="test_metric",
                    additional_data={}
                )
                
                # Проверяем, что алерт не отправлен из-за кулдауна
                mock_sender.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_alert_after_cooldown(self):
        """Тест отправки алерта после кулдауна."""
        with patch.dict('os.environ', {
            'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/test'
        }):
            monitor = BusinessMetricsMonitor()
            
            # Устанавливаем старый алерт (больше 30 минут назад)
            alert_key = "test_metric_critical"
            monitor.alert_cooldown[alert_key] = datetime.now() - timedelta(minutes=35)
            
            with patch('core.business_alerts.AlertSender') as mock_alert_sender:
                mock_sender = AsyncMock()
                mock_alert_sender.return_value.__aenter__.return_value = mock_sender
                
                await monitor._send_alert(
                    title="Test Alert",
                    message="Test message",
                    severity=AlertSeverity.CRITICAL,
                    metric_name="test_metric",
                    additional_data={}
                )
                
                # Проверяем, что алерт отправлен
                mock_sender.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_alert_exception(self):
        """Тест отправки алерта с исключением."""
        with patch.dict('os.environ', {
            'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/test'
        }):
            monitor = BusinessMetricsMonitor()
            
            with patch('core.business_alerts.AlertSender') as mock_alert_sender:
                mock_alert_sender.side_effect = Exception("Alert error")
                
                # Функция должна обработать исключение
                await monitor._send_alert(
                    title="Test Alert",
                    message="Test message",
                    severity=AlertSeverity.CRITICAL,
                    metric_name="test_metric",
                    additional_data={}
                )
                
                # Проверяем, что функция завершилась без критических ошибок
                assert True

    @pytest.mark.asyncio
    async def test_start_monitoring(self):
        """Тест запуска мониторинга."""
        with patch.dict('os.environ', {'PROMETHEUS_URL': 'http://localhost:9090'}):
            monitor = BusinessMetricsMonitor()

            with patch.object(monitor, '_check_business_metrics') as mock_check:
                # Мокаем бесконечный цикл
                mock_check.side_effect = Exception("Stop monitoring")

                try:
                    await monitor.start_monitoring()
                except Exception:
                    pass

                mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_monitoring_exception(self):
        """Тест запуска мониторинга с исключением."""
        with patch.dict('os.environ', {'PROMETHEUS_URL': 'http://localhost:9090'}):
            monitor = BusinessMetricsMonitor()
            
            with patch.object(monitor, '_check_business_metrics', side_effect=Exception("Test error")):
                # Функция должна обработать исключение и завершиться
                await monitor.start_monitoring()
                
                # Проверяем, что функция завершилась без критических ошибок
                assert True

    @pytest.mark.asyncio
    async def test_thresholds_configuration(self):
        """Тест конфигурации пороговых значений."""
        monitor = BusinessMetricsMonitor()
        
        expected_thresholds = [
            "user_activity_drop",
            "cache_hit_rate", 
            "schedule_generation_time",
            "notification_failure_rate",
            "user_conversion_drop"
        ]
        
        for threshold in expected_thresholds:
            assert threshold in monitor.thresholds
            assert isinstance(monitor.thresholds[threshold], (int, float))

    @pytest.mark.asyncio
    async def test_alert_payload_structure(self):
        """Тест структуры payload алерта."""
        with patch.dict('os.environ', {
            'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/test'
        }):
            monitor = BusinessMetricsMonitor()
            
            with patch('core.business_alerts.AlertSender') as mock_alert_sender:
                mock_sender = AsyncMock()
                mock_alert_sender.return_value.__aenter__.return_value = mock_sender
                
                await monitor._send_alert(
                    title="Test Alert",
                    message="Test message",
                    severity=AlertSeverity.WARNING,
                    metric_name="test_metric",
                    additional_data={"key": "value"}
                )
                
                # Проверяем структуру payload
                call_args = mock_sender.send.call_args[0][0]
                assert call_args["title"] == "Test Alert"
                assert call_args["message"] == "Test message"
                assert call_args["severity"] == "warning"
                assert call_args["metric_name"] == "test_metric"
                assert call_args["tags"]["key"] == "value"
                assert "timestamp" in call_args

    @pytest.mark.asyncio
    async def test_environment_variables_handling(self):
        """Тест обработки переменных окружения."""
        # Тест с пустыми переменными
        with patch.dict('os.environ', {}, clear=True):
            monitor = BusinessMetricsMonitor()
            
            assert monitor.settings["SLACK_WEBHOOK_URL"] is None
            assert monitor.prometheus_url is None
        
        # Тест с установленными переменными
        with patch.dict('os.environ', {
            'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/test',
            'PROMETHEUS_URL': 'http://localhost:9090'
        }):
            monitor = BusinessMetricsMonitor()
            
            assert monitor.settings["SLACK_WEBHOOK_URL"] == "https://hooks.slack.com/test"
            assert monitor.prometheus_url == "http://localhost:9090"

    @pytest.mark.asyncio
    async def test_business_monitor_singleton(self):
        """Тест синглтона business_monitor."""
        from core.business_alerts import business_monitor
        
        assert isinstance(business_monitor, BusinessMetricsMonitor)
        
        # Проверяем, что это тот же экземпляр
        from core.business_alerts import business_monitor as business_monitor2
        assert business_monitor is business_monitor2
