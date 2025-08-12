import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from aiohttp import web

from core.alert_webhook import format_alertmanager_message, create_alert_app, run_alert_webhook_server


class DummyBot:
    def __init__(self):
        class Sess:
            class Loop:
                def create_task(self, coro):
                    return None
            loop = Loop()
        self.session = Sess()
        self.sent = []
    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_webhook_sends_to_admins(mocker):
    bot = DummyBot()
    app = create_alert_app(bot, [1, 2])

    from aiohttp.test_utils import TestServer, TestClient
    server = TestServer(app)
    await server.start_server()
    client = TestClient(server)
    await client.start_server()

    try:
        payload = {
            'status': 'firing',
            'alerts': [
                {
                    'labels': {'alertname': 'ScheduleStale', 'severity': 'critical'},
                    'annotations': {'description': 'No update > 1h'},
                    'startsAt': '2025-01-01T00:00:00Z'
                }
            ]
        }

        resp = await client.post('/alerts', json=payload)
        assert resp.status == 200
        assert any(c[0] in (1, 2) for c in bot.sent)
    finally:
        await client.close()
        await server.close()


def test_format_alertmanager_message_single_alert():
    """Тест форматирования сообщения с одним алертом"""
    payload = {
        "status": "firing",
        "alerts": [
            {
                "labels": {
                    "alertname": "HighCPUUsage",
                    "severity": "warning",
                    "source": "server1"
                },
                "annotations": {
                    "description": "CPU usage is above 90%",
                    "summary": "High CPU usage detected"
                },
                "startsAt": "2025-01-06T10:00:00Z",
                "endsAt": "2025-01-06T10:05:00Z"
            }
        ]
    }
    
    result = format_alertmanager_message(payload)
    
    assert "ALERTMANAGER: FIRING (1 alert(s))" in result
    assert "⚠️ HighCPUUsage [warning]" in result
    assert "CPU usage is above 90%" in result
    assert "source=server1" in result
    assert "startsAt=2025-01-06T10:00:00Z" in result
    assert "endsAt=2025-01-06T10:05:00Z" in result


def test_format_alertmanager_message_multiple_alerts():
    """Тест форматирования сообщения с несколькими алертами"""
    payload = {
        "status": "resolved",
        "alerts": [
            {
                "labels": {"alertname": "Alert1", "severity": "critical"},
                "annotations": {"description": "First alert"},
                "startsAt": "2025-01-06T10:00:00Z",
                "endsAt": "2025-01-06T10:05:00Z"
            },
            {
                "labels": {"alertname": "Alert2", "severity": "warning"},
                "annotations": {"summary": "Second alert"},
                "startsAt": "2025-01-06T10:10:00Z",
                "endsAt": "2025-01-06T10:15:00Z"
            }
        ]
    }
    
    result = format_alertmanager_message(payload)
    
    assert "ALERTMANAGER: RESOLVED (2 alert(s))" in result
    assert "⚠️ Alert1 [critical]" in result
    assert "⚠️ Alert2 [warning]" in result
    assert "First alert" in result
    assert "Second alert" in result


def test_format_alertmanager_message_missing_fields():
    """Тест форматирования сообщения с отсутствующими полями"""
    payload = {
        "status": "firing",
        "alerts": [
            {
                "labels": {},
                "annotations": {},
                "startsAt": "",
                "endsAt": ""
            }
        ]
    }
    
    result = format_alertmanager_message(payload)
    
    assert "ALERTMANAGER: FIRING (1 alert(s))" in result
    assert "⚠️ unknown [unknown]" in result
    assert "source=" in result
    assert "startsAt=" in result
    assert "endsAt=" in result


def test_format_alertmanager_message_empty_alerts():
    """Тест форматирования сообщения без алертов"""
    payload = {
        "status": "firing",
        "alerts": []
    }
    
    result = format_alertmanager_message(payload)
    
    assert "ALERTMANAGER: FIRING (0 alert(s))" in result
    assert "⚠️" not in result


def test_create_alert_app():
    """Тест создания приложения для алертов"""
    mock_bot = MagicMock()
    admin_ids = [123, 456]
    
    app = create_alert_app(mock_bot, admin_ids)
    
    assert isinstance(app, web.Application)
    assert len(app.router.routes()) > 0


@pytest.mark.asyncio
async def test_handle_alert_success():
    """Тест успешной обработки алерта"""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    admin_ids = [123, 456]
    
    app = create_alert_app(mock_bot, admin_ids)
    
    # Создаем тестовый запрос
    payload = {
        "status": "firing",
        "alerts": [
            {
                "labels": {"alertname": "TestAlert", "severity": "warning"},
                "annotations": {"description": "Test description"},
                "startsAt": "2025-01-06T10:00:00Z",
                "endsAt": "2025-01-06T10:05:00Z"
            }
        ]
    }
    
    # Мокаем request
    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value=payload)
    
    # Получаем handler
    handler = None
    for route in app.router.routes():
        if route.resource.canonical == '/alerts':
            handler = route.handler
            break
    
    assert handler is not None
    
    # Вызываем handler
    response = await handler(mock_request)
    
    assert response.status == 200
    assert response.text == "ok"
    
    # Проверяем, что сообщения отправлены админам
    assert mock_bot.send_message.call_count == 2
    
    # Проверяем, что сообщения содержат нужный текст
    calls = mock_bot.send_message.call_args_list
    assert any("ALERTMANAGER: FIRING (1 alert(s))" in str(call) for call in calls)
    assert any("⚠️ TestAlert [warning]" in str(call) for call in calls)


@pytest.mark.asyncio
async def test_handle_alert_invalid_json():
    """Тест обработки алерта с неверным JSON"""
    mock_bot = MagicMock()
    admin_ids = [123]
    
    app = create_alert_app(mock_bot, admin_ids)
    
    # Мокаем request с ошибкой JSON
    mock_request = MagicMock()
    mock_request.json = AsyncMock(side_effect=Exception("Invalid JSON"))
    
    # Получаем handler
    handler = None
    for route in app.router.routes():
        if route.resource.canonical == '/alerts':
            handler = route.handler
            break
    
    assert handler is not None
    
    # Вызываем handler
    response = await handler(mock_request)
    
    assert response.status == 400
    assert response.text == "invalid json"


@pytest.mark.asyncio
async def test_handle_alert_bot_send_failure():
    """Тест обработки алерта с ошибкой отправки ботом"""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("Bot error"))
    admin_ids = [123]
    
    app = create_alert_app(mock_bot, admin_ids)
    
    # Создаем тестовый запрос
    payload = {
        "status": "firing",
        "alerts": [
            {
                "labels": {"alertname": "TestAlert", "severity": "warning"},
                "annotations": {"description": "Test description"},
                "startsAt": "2025-01-06T10:00:00Z",
                "endsAt": "2025-01-06T10:05:00Z"
            }
        ]
    }
    
    # Мокаем request
    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value=payload)
    
    # Получаем handler
    handler = None
    for route in app.router.routes():
        if route.resource.canonical == '/alerts':
            handler = route.handler
            break
    
    assert handler is not None
    
    # Вызываем handler - должен выполниться без ошибок
    response = await handler(mock_request)
    
    assert response.status == 200
    assert response.text == "ok"


