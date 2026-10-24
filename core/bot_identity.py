"""Cached Telegram bot username from get_me(), used in about, inline and locales."""

from __future__ import annotations

_BOT_USERNAME: str | None = None
_FALLBACK_USERNAME = "voenmeh_bot"


def set_bot_username(username: str | None) -> None:
    """Store the live bot username without a leading @."""
    global _BOT_USERNAME
    if not username:
        _BOT_USERNAME = None
        return
    _BOT_USERNAME = str(username).lstrip("@").strip() or None


def get_bot_username() -> str:
    """Return the cached username or a stable fallback."""
    return _BOT_USERNAME or _FALLBACK_USERNAME


def bot_mention() -> str:
    """Return @username for copy-paste into Telegram."""
    return f"@{get_bot_username()}"
