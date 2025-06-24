from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from core.user_data import UserDataManager

class UserDataMiddleware(BaseMiddleware):
    def __init__(self, user_data_manager: UserDataManager):
        self.user_data_manager = user_data_manager

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data["user_data_manager"] = self.user_data_manager
        return await handler(event, data)