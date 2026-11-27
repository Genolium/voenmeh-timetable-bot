"""Request middleware для автоматической подстановки кастомных эмодзи Telegram во все исходящие сообщения и кнопки."""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import TelegramMethod
from aiogram.methods.base import Response, TelegramType
from aiogram.types import InlineKeyboardMarkup

from bot.middlewares.network_retry_middleware import NetworkRetryRequestMiddleware
from core.config import settings
from core.custom_emojis import replace_emojis_html, transform_inline_keyboard


def _is_html_parse_mode(parse_mode: Any, bot: Bot) -> bool:
    if isinstance(parse_mode, Default):
        default_mode = bot.default.parse_mode if getattr(bot, "default", None) else None
        return str(default_mode or "").upper() == "HTML"
    if parse_mode is None:
        return False
    return str(parse_mode).upper() == "HTML"


class CustomEmojiRequestMiddleware(BaseRequestMiddleware):
    """Перехватывает исходящие запросы Bot API и подставляет <tg-emoji> и icon_custom_emoji_id."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if not settings.CUSTOM_EMOJIS_ENABLED:
            return await make_request(bot, method)

        model_fields = type(method).model_fields
        updates: dict[str, Any] = {}

        if (
            "text" in model_fields
            and "parse_mode" in model_fields
            and isinstance(getattr(method, "text", None), str)
            and not getattr(method, "entities", None)
            and _is_html_parse_mode(getattr(method, "parse_mode", None), bot)
        ):
            new_text = replace_emojis_html(method.text)  # type: ignore[attr-defined]
            if new_text != method.text:  # type: ignore[attr-defined]
                updates["text"] = new_text

        if (
            "caption" in model_fields
            and "parse_mode" in model_fields
            and isinstance(getattr(method, "caption", None), str)
            and not getattr(method, "caption_entities", None)
            and _is_html_parse_mode(getattr(method, "parse_mode", None), bot)
        ):
            new_caption = replace_emojis_html(method.caption)  # type: ignore[attr-defined]
            if new_caption != method.caption:  # type: ignore[attr-defined]
                updates["caption"] = new_caption

        if "media" in model_fields:
            media = getattr(method, "media", None)
            if (
                media is not None
                and isinstance(getattr(media, "caption", None), str)
                and not getattr(media, "caption_entities", None)
                and _is_html_parse_mode(getattr(media, "parse_mode", None), bot)
            ):
                new_media_caption = replace_emojis_html(media.caption)
                if new_media_caption != media.caption:
                    updates["media"] = media.model_copy(update={"caption": new_media_caption})

        if "reply_markup" in model_fields:
            markup = getattr(method, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup):
                new_markup = transform_inline_keyboard(markup)
                if new_markup is not markup:
                    updates["reply_markup"] = new_markup

        if updates:
            method = method.model_copy(update=updates)

        return await make_request(bot, method)


def attach_custom_emoji_middleware(bot: Bot) -> Bot:
    """Регистрирует middleware кастомных эмодзи и сетевых ретраев на сессии бота."""
    session = getattr(bot, "session", None)
    middleware = getattr(session, "middleware", None)
    if callable(middleware):
        middleware(CustomEmojiRequestMiddleware())
        middleware(NetworkRetryRequestMiddleware())
    return bot
