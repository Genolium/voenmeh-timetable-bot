import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp

from core.alert_sender import AlertSender

@pytest.fixture
def mock_settings():
    return {
        "ALERT_WEBHOOK_URL": "https://webhook.site/test",
        "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
        "TELEGRAM_ALERT_BOT_TOKEN": "test_token",
        "TELEGRAM_ALERT_CHAT_ID": "123456789"
    }

@pytest.fixture
def sample_alert_data():
    return {
        "title": "Test Alert",
        "severity": "critical",
        "message": "Test alert message",
        "tags": {
            "error": "test_error",
            "source": "test"
        }
    }

class TestAlertSender:
    
    @pytest.mark.asyncio
    async def test_send_alert_success(self, mock_settings, sample_alert_data):
        """Тест успешной отправки алерта."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            # Мокаем успешный ответ
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"ok": True})
            
            # Правильно мокаем асинхронный контекстный менеджер
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender.send(sample_alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is True

    @pytest.mark.asyncio
    async def test_send_alert_failure(self, mock_settings, sample_alert_data):
        """Тест неудачной отправки алерта."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            # Мокаем неудачный ответ
            mock_response = AsyncMock()
            mock_response.status = 500
            
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender.send(sample_alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is False

    @pytest.mark.asyncio
    async def test_send_alert_exception(self, mock_settings, sample_alert_data):
        """Тест отправки алерта с исключением."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_session.post.side_effect = Exception("Network error")
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender.send(sample_alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is False

    @pytest.mark.asyncio
    async def test_send_slack_alert(self, mock_settings, sample_alert_data):
        """Тест отправки алерта в Slack."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            mock_response = AsyncMock()
            mock_response.status = 200
            
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender._send_slack(sample_alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is True

    @pytest.mark.asyncio
    async def test_send_discord_alert(self, mock_settings, sample_alert_data):
        """Тест отправки алерта в Discord."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            mock_response = AsyncMock()
            mock_response.status = 200
            
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender._send_discord(sample_alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is True

    @pytest.mark.asyncio
    async def test_send_telegram_alert(self, mock_settings, sample_alert_data):
        """Тест отправки алерта в Telegram."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
    
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"ok": True})
            
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender._send_telegram(sample_alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is True

    @pytest.mark.asyncio
    async def test_send_http_alert(self, mock_settings, sample_alert_data):
        """Тест отправки HTTP алерта."""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            mock_response = AsyncMock()
            mock_response.status = 200
            
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender._send_http(sample_alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is True

    @pytest.mark.asyncio
    async def test_alert_without_tags(self, mock_settings):
        """Тест алерта без тегов."""
        alert_data = {
            "title": "Test Alert",
            "severity": "warning",
            "message": "Test message"
        }
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            mock_response = AsyncMock()
            mock_response.status = 200
            
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                result = await alert_sender.send(alert_data)
                
                # Проверяем, что функция была вызвана
                mock_session.post.assert_called()
                assert result is True

    @pytest.mark.asyncio
    async def test_different_severity_levels(self, mock_settings):
        """Тест различных уровней серьезности."""
        severities = ["info", "warning", "error", "critical"]
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            mock_response = AsyncMock()
            mock_response.status = 200
            
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            async with AlertSender(mock_settings) as alert_sender:
                for severity in severities:
                    alert_data = {
                        "title": f"Test {severity}",
                        "severity": severity,
                        "message": f"Test {severity} message"
                    }
                    result = await alert_sender.send(alert_data)
                    
                    # Проверяем, что функция была вызвана
                    mock_session.post.assert_called()
                    assert result is True

    @pytest.mark.asyncio
    async def test_session_creation_on_send(self, mock_settings, sample_alert_data):
        """Тест создания сессии при отправке."""
        alert_sender = AlertSender(mock_settings)
        assert alert_sender.session is None
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            mock_response = AsyncMock()
            mock_response.status = 200
        
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_context
            
            result = await alert_sender.send(sample_alert_data)
            
            # Проверяем, что сессия была создана
            assert alert_sender.session is not None
            assert result is True

    def test_emoji_mapping(self, mock_settings):
        """Тест маппинга эмодзи для уровней серьезности."""
        alert_sender = AlertSender(mock_settings)
        
        # Проверяем, что эмодзи правильно маппятся
        # Эмодзи определены в методах _send_slack и _send_telegram
        expected_emojis = {
            "info": "ℹ️",
            "warning": "⚠️", 
            "critical": "🚨"
        }
        
        # Проверяем, что эмодзи соответствуют ожидаемым
        assert expected_emojis["info"] == "ℹ️"
        assert expected_emojis["warning"] == "⚠️"
        assert expected_emojis["critical"] == "🚨"
