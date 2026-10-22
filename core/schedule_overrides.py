"""Admin-managed per-group schedule overrides stored in Redis."""

from __future__ import annotations

import json
import logging
from datetime import date

from redis.asyncio.client import Redis

logger = logging.getLogger(__name__)

OVERRIDE_PREFIX = "schedule_override:"
OVERRIDE_TTL_SECONDS = 30 * 86400


def _key(group: str, target_date: date) -> str:
    return f"{OVERRIDE_PREFIX}{group.upper()}:{target_date.isoformat()}"


async def set_group_override(redis: Redis, group: str, target_date: date, lessons: list[dict]) -> None:
    await redis.set(_key(group, target_date), json.dumps(lessons, ensure_ascii=False), ex=OVERRIDE_TTL_SECONDS)


async def get_group_override(redis: Redis, group: str, target_date: date) -> list[dict] | None:
    try:
        raw = await redis.get(_key(group, target_date))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except Exception as e:
        logger.warning(f"Failed to read schedule override for {group} {target_date}: {e}")
        return None


async def clear_group_override(redis: Redis, group: str, target_date: date) -> None:
    await redis.delete(_key(group, target_date))
