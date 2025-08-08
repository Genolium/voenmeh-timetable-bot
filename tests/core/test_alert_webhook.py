import pytest
from aiohttp import web
from core.alert_webhook import create_alert_app, format_alertmanager_message


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


def test_format_message_smoke():
    text = format_alertmanager_message({'status': 'firing', 'alerts': []})
    assert 'ALERTMANAGER' in text


