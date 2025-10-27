import logging
from typing import Any, Optional

from aiogram import Bot

logger = logging.getLogger(__name__)


class CleanupBot(Bot):
    """
    Bot wrapper that automatically tracks its own outgoing messages in Redis and
    prunes older ones per chat/thread, keeping only a limited number.
    """

    def __init__(
        self,
        *args: Any,
        redis: Any,
        keep_messages: int = 5,
        key_ttl_seconds: int = 2 * 24 * 60 * 60,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._redis = redis
        self._keep_messages = max(0, int(keep_messages))
        self._key_ttl_seconds = int(key_ttl_seconds)
        # Marker so middlewares can detect that outgoing cleanup is handled here
        self.auto_cleanup_outgoing = True

    def _make_key(self, chat_id: int | str, thread_id: Optional[int]) -> str:
        base = f"chat_cleanup:{chat_id}"
        if thread_id is not None:
            return f"{base}:{thread_id}"
        return base

    async def _track_and_prune(
        self,
        chat_id: int | str,
        message_id: int,
        thread_id: Optional[int],
    ) -> None:
        try:
            key = self._make_key(chat_id, thread_id)
            await self._redis.rpush(key, message_id)
            await self._redis.expire(key, self._key_ttl_seconds)

            # Fetch and prune older messages
            ids = await self._redis.lrange(key, 0, -1)
            if not ids:
                return
            if len(ids) <= self._keep_messages:
                return

            to_delete = ids[: -self._keep_messages]
            deleted = 0
            for mid in to_delete:
                try:
                    mid_int = int(mid.decode() if isinstance(mid, (bytes, bytearray)) else mid)
                    await self.delete_message(chat_id=chat_id, message_id=mid_int)
                except Exception:
                    # Ignore Telegram errors (too old/already deleted/etc.)
                    pass
                finally:
                    # Ensure the id is removed from Redis even if deletion failed
                    try:
                        await self._redis.lrem(key, 1, mid)
                    except Exception:
                        pass
                    deleted += 1
            if deleted:
                logger.debug(
                    "Pruned %s old messages in chat %s (thread %s)",
                    deleted,
                    chat_id,
                    thread_id,
                )
        except Exception as e:
            logger.debug("CleanupBot tracking error: %s", e)

    # --- Overridden send methods ---
    async def send_message(self, chat_id: int | str, text: str, **kwargs: Any):
        result = await super().send_message(chat_id, text, **kwargs)
        thread_id = kwargs.get("message_thread_id")
        await self._track_and_prune(chat_id, result.message_id, thread_id)
        return result

    async def send_photo(self, chat_id: int | str, photo: Any, **kwargs: Any):
        # Media messages are exempt from auto-tracking/deletion
        return await super().send_photo(chat_id, photo, **kwargs)

    async def send_document(self, chat_id: int | str, document: Any, **kwargs: Any):
        # Files are exempt from auto-tracking/deletion
        return await super().send_document(chat_id, document, **kwargs)

    async def send_video(self, chat_id: int | str, video: Any, **kwargs: Any):
        # Media messages are exempt from auto-tracking/deletion
        return await super().send_video(chat_id, video, **kwargs)

    async def send_animation(self, chat_id: int | str, animation: Any, **kwargs: Any):
        # Media messages are exempt from auto-tracking/deletion
        return await super().send_animation(chat_id, animation, **kwargs)

    async def send_media_group(self, chat_id: int | str, media: Any, **kwargs: Any):
        # Media groups are exempt from auto-tracking/deletion
        return await super().send_media_group(chat_id, media, **kwargs)
