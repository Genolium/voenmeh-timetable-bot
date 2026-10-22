"""Timezone helpers.

The database is configured with ``Europe/Moscow`` and stores naive TIMESTAMP
values. All application writes must therefore use Moscow-naive datetimes;
mixing UTC-naive and MSK-naive values in the same column corrupts analytics.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.config import MOSCOW_TZ


def now_msk_naive() -> datetime:
    """Current Moscow time as a naive datetime (matches DB server_default=now())."""
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


def to_msk_naive(value: datetime) -> datetime:
    """Convert an aware datetime to naive Moscow time; naive input is returned as-is."""
    if value.tzinfo is None:
        return value
    return value.astimezone(MOSCOW_TZ).replace(tzinfo=None)


def utc_now_aware() -> datetime:
    """Timezone-aware UTC now, for protocol payloads that require UTC."""
    return datetime.now(timezone.utc)
