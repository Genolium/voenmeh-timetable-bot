from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.manager import TimetableManager


class ManagerMiddleware(BaseMiddleware):
    def __init__(self, manager: TimetableManager):
        self.manager = manager

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        import bot.scheduler as scheduler_mod

        active_mgr = getattr(scheduler_mod, "global_timetable_manager_instance", None) or self.manager
        data["manager"] = active_mgr
        return await handler(event, data)
