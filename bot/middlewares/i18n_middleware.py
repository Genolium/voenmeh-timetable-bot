from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from core.i18n import i18n


class I18nMiddleware(BaseMiddleware):
    def __init__(self):
        pass

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Определяем язык
        lang = "ru"  # Default
        
        # 1. Сначала проверяем настройки из БД (они загружаются в UserDataMiddleware)
        user_info = data.get("user_info")
        if user_info and getattr(user_info, "language", None):
            lang = user_info.language
        else:
            # 2. Если нет в БД, пробуем определить по настройкам Telegram
            telegram_user = getattr(event, "from_user", None)
            if telegram_user and telegram_user.language_code:
                code = telegram_user.language_code.lower()
                if "zh" in code:
                    lang = "zh"
                elif "en" in code:
                    lang = "en"
                # ru остаётся ru
        
        # Инъекция языка и функции перевода
        data["lang"] = lang
        data["i18n_lang"] = lang  # Alias for schedule_view and settings_menu compatibility
        
        # Хелпер для получения перевода с уже установленным языком
        # Пример использования в хендлере: _("welcome_title")
        def string_getter(key: str, **kwargs) -> str:
            return i18n.get(key, lang=lang, **kwargs)
            
        data["i18n"] = i18n
        data["_"] = string_getter

        return await handler(event, data)
