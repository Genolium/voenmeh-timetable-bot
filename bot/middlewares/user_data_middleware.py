from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.middlewares.event_utils import extract_from_user
from core.user_data import UserDataManager


class UserDataMiddleware(BaseMiddleware):
    def __init__(self, user_data_manager: UserDataManager):
        self.user_data_manager = user_data_manager

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["user_data_manager"] = self.user_data_manager

        # Обновляем last_active_date при любом взаимодействии пользователя с ботом
        # (сообщения, callback-кнопки, inline-запросы).
        from_user = extract_from_user(event)
        user_id = getattr(from_user, "id", None)
        username = getattr(from_user, "username", None)

        if user_id:
            try:
                user_info = await self.user_data_manager.register_user_and_get(user_id, username)
                if user_info:
                    data["user_info"] = user_info
            except Exception:
                # Не блокируем обработку апдейта из-за сбоя БД
                pass

        return await handler(event, data)
