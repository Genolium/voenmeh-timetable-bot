from typing import Any, Awaitable, Callable, Dict

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
        data: Dict[str, Any],
    ) -> Any:
        data["user_data_manager"] = self.user_data_manager

        # Обновляем last_active_date при любом взаимодействии пользователя с ботом
        user_id = None
        username = None

        try:
            from_user = getattr(event, "from_user", None)
            if from_user is not None:
                user_id = getattr(from_user, "id", None)
                username = getattr(from_user, "username", None)
            else:
                message = getattr(event, "message", None)
                if message is not None and getattr(message, "from_user", None) is not None:
                    user_id = getattr(message.from_user, "id", None)
                    username = getattr(message.from_user, "username", None)
        except Exception:
            pass

        if user_id:
            try:
                # Регистрируем и получаем пользователя за одну DB-сессию
                user_info = await self.user_data_manager.register_user_and_get(user_id, username)
                if user_info:
                    data["user_info"] = user_info

            except Exception:
                # Не блокируем обработку апдейта из-за сбоя БД
                pass

        return await handler(event, data)
