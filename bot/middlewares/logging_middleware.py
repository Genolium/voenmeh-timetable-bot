import logging
import time
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.metrics import EVENTS_PROCESSED, HANDLER_DURATION

logger = logging.getLogger(__name__)

class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id = "N/A"
        if hasattr(event, "from_user") and getattr(event.from_user, "id", None):
            user_id = event.from_user.id
        
        event_type = type(event).__name__
        
        EVENTS_PROCESSED.labels(event_type=event_type).inc()

        log_extra = { "user_id": user_id, "event_type": event_type }
        adapter = logging.LoggerAdapter(logger, extra=log_extra)
        data["logger"] = adapter
        
        adapter.info("Processing event")

        try:
            handler_func = getattr(data.get('handler'), 'callback', None)
            handler_name = handler_func.__name__ if handler_func and hasattr(handler_func, '__name__') else str(handler_func or 'unknown_handler')
            
            with HANDLER_DURATION.labels(handler_name=handler_name).time():
                return await handler(event, data)
        except Exception as e:
            adapter.error("Exception caught in handler", exc_info=True)
            raise