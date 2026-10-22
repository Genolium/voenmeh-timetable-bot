"""Coverage for the session middleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.middlewares.session_middleware import SessionMiddleware


@pytest.mark.asyncio
async def test_session_middleware_injects_session_factory():
    session_factory = MagicMock(name="session_factory")
    middleware = SessionMiddleware(session_factory)

    handler = AsyncMock(return_value="handled")
    data = {}

    result = await middleware(handler, MagicMock(), data)

    assert result == "handled"
    assert data["session_factory"] is session_factory
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_middleware_passes_event_through():
    middleware = SessionMiddleware(MagicMock())

    event = MagicMock(name="event")
    data = {"existing": 1}
    seen = {}

    async def handler(received_event, received_data):
        seen["event"] = received_event
        seen["data"] = received_data
        return 42

    result = await middleware(handler, event, data)

    assert result == 42
    assert seen["event"] is event
    assert seen["data"] is data
    assert data["session_factory"] is middleware.session_factory
