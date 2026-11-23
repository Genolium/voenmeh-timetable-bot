"""Сетевая устойчивость запросов к Telegram Bot API: быстрый sock_connect (IPv4) и авто-ретраи."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import Response, TelegramType

logger = logging.getLogger(__name__)


class FastConnectAiohttpSession(AiohttpSession):
    """AiohttpSession с принудительным IPv4 и коротким таймаутом установки TCP-соединения."""

    def __init__(
        self,
        timeout: float = 60.0,
        limit: int = 100,
        sock_connect_timeout: float = 4.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(timeout=timeout, limit=limit, **kwargs)
        self._connector_init["family"] = socket.AF_INET
        self.sock_connect_timeout = sock_connect_timeout

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        effective_total = float(self.timeout if timeout is None else timeout)
        client_timeout = aiohttp.ClientTimeout(
            total=effective_total,
            sock_connect=min(self.sock_connect_timeout, effective_total),
        )
        return await super().make_request(bot, method, timeout=client_timeout)  # type: ignore[arg-type]


class NetworkRetryRequestMiddleware(BaseRequestMiddleware):
    """Повторяет запросы к Bot API при транзиентных TelegramNetworkError (кроме GetUpdates)."""

    def __init__(self, max_attempts: int = 4, backoff_base: float = 0.3) -> None:
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)

        attempt = 1
        while True:
            try:
                return await make_request(bot, method)
            except TelegramNetworkError as exc:
                if attempt >= self.max_attempts:
                    raise
                delay = self.backoff_base * attempt
                logger.warning(
                    "Telegram network error on %s (attempt %d/%d): %s; retrying in %.1fs",
                    method.__api_method__,
                    attempt,
                    self.max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
