"""Тесты для FastConnectAiohttpSession и NetworkRetryRequestMiddleware."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetUpdates, SendMessage

from bot.middlewares.network_retry_middleware import (
    FastConnectAiohttpSession,
    NetworkRetryRequestMiddleware,
)


@pytest.mark.asyncio
async def test_fast_connect_session_sets_ipv4_and_sock_connect_timeout():
    session = FastConnectAiohttpSession(timeout=60.0, limit=15, sock_connect_timeout=4.0)
    assert session._connector_init["family"] == socket.AF_INET
    assert session.sock_connect_timeout == 4.0

    bot = MagicMock(spec=Bot)
    method = SendMessage(chat_id=1, text="hi")

    with patch.object(AiohttpSession, "make_request", new_callable=AsyncMock, return_value="ok") as super_call:
        res = await session.make_request(bot, method)
        assert res == "ok"
        ct = super_call.await_args.kwargs["timeout"]
        assert isinstance(ct, aiohttp.ClientTimeout)
        assert ct.total == 60.0
        assert ct.sock_connect == 4.0

        await session.make_request(bot, method, timeout=2)
        ct_small = super_call.await_args.kwargs["timeout"]
        assert ct_small.total == 2.0
        assert ct_small.sock_connect == 2.0


@pytest.mark.asyncio
async def test_network_retry_skips_get_updates():
    mw = NetworkRetryRequestMiddleware(max_attempts=3, backoff_base=0.1)
    bot = MagicMock(spec=Bot)
    method = GetUpdates()
    make_request = AsyncMock(side_effect=TelegramNetworkError(method=method, message="timeout"))

    with pytest.raises(TelegramNetworkError):
        await mw(make_request, bot, method)
    assert make_request.await_count == 1


@pytest.mark.asyncio
async def test_network_retry_recovers_after_transient_error():
    mw = NetworkRetryRequestMiddleware(max_attempts=3, backoff_base=0.2)
    bot = MagicMock(spec=Bot)
    method = SendMessage(chat_id=1, text="hi")
    make_request = AsyncMock(
        side_effect=[
            TelegramNetworkError(method=method, message="Connect call failed"),
            "sent",
        ]
    )

    with patch("bot.middlewares.network_retry_middleware.asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await mw(make_request, bot, method)

    assert res == "sent"
    assert make_request.await_count == 2
    mock_sleep.assert_awaited_once_with(0.2)


@pytest.mark.asyncio
async def test_network_retry_raises_after_max_attempts():
    mw = NetworkRetryRequestMiddleware(max_attempts=2, backoff_base=0.1)
    bot = MagicMock(spec=Bot)
    method = SendMessage(chat_id=1, text="hi")
    make_request = AsyncMock(side_effect=TelegramNetworkError(method=method, message="Connect call failed"))

    with (
        patch("bot.middlewares.network_retry_middleware.asyncio.sleep", AsyncMock()) as mock_sleep,
        pytest.raises(TelegramNetworkError),
    ):
        await mw(make_request, bot, method)

    assert make_request.await_count == 2
    mock_sleep.assert_awaited_once_with(0.1)
