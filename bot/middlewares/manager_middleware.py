from typing import Callable, Dict, Any, Awaitable
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
        data: Dict[str, Any]
    ) -> Any:
        data["manager"] = self.manager
        return await handler(event, data)