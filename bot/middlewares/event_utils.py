"""Helpers for outer (Update-level) middlewares.

Outer middlewares registered via ``dp.update.middleware`` receive ``Update``
objects, not ``Message``/``CallbackQuery``. These helpers unwrap the inner
event so that user, text and callback data are always available.
"""

from __future__ import annotations

from typing import Any


def _inner_event(event: Any) -> Any:
    for attr in ("message", "edited_message", "callback_query", "inline_query"):
        inner = getattr(event, attr, None)
        if inner is not None:
            return inner
    return event


def extract_from_user(event: Any) -> Any:
    """Return the Telegram user for any update/message/callback object."""
    user = getattr(event, "from_user", None)
    if user is not None:
        return user
    return getattr(_inner_event(event), "from_user", None)


def extract_message(event: Any) -> Any:
    """Return a Message-like object if the update contains one."""
    if hasattr(event, "from_user") and hasattr(event, "text"):
        return event
    message = getattr(event, "message", None) or getattr(event, "edited_message", None)
    if message is not None:
        return message
    callback = getattr(event, "callback_query", None)
    return getattr(callback, "message", None) if callback is not None else None


def extract_text(event: Any) -> str | None:
    """Return message text or inline query string."""
    message = extract_message(event)
    if message is not None and getattr(message, "text", None):
        return str(message.text)
    inline = getattr(event, "inline_query", None)
    if inline is not None and getattr(inline, "query", None):
        return str(inline.query)
    return None


def extract_callback_data(event: Any) -> str | None:
    """Return callback data for callback updates or message text otherwise."""
    callback = getattr(event, "callback_query", None)
    if callback is not None and getattr(callback, "data", None) is not None:
        return str(callback.data)
    return extract_text(event)


def extract_event_type(event: Any) -> str:
    """Human-readable inner event type: Message, CallbackQuery, InlineQuery."""
    if hasattr(event, "from_user") and hasattr(event, "text"):
        return type(event).__name__
    for attr, name in (
        ("message", "Message"),
        ("edited_message", "Message"),
        ("callback_query", "CallbackQuery"),
        ("inline_query", "InlineQuery"),
    ):
        if getattr(event, attr, None) is not None:
            return name
    return type(event).__name__
