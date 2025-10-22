import logging
import time
import json
from datetime import datetime
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from core.metrics import (
    USER_ACTIONS, USER_ACTIVITY_DAILY, USER_ACTIVITY_HOURLY,
    BOT_COMMANDS, DIALOG_USAGE, FEATURE_POPULARITY,
    USER_ACTION_INTERVAL, ACTIVE_SESSIONS, NEW_USERS_DAILY
)

logger = logging.getLogger(__name__)

class ActivityLoggingMiddleware(BaseMiddleware):
    """
    Middleware для логирования всех действий пользователей в Loki и обновления метрик.
    """

    def __init__(self):
        self.last_user_actions = {}  # Для отслеживания интервалов между действиями

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:

        # Получаем информацию о пользователе
        user_id = "unknown"
        username = "unknown"
        user_type = "unknown"
        user_group = "unknown"

        if hasattr(event, "from_user") and getattr(event.from_user, "id", None):
            user_id = str(event.from_user.id)
            username = getattr(event.from_user, "username", "unknown")
            user_full_name = getattr(event.from_user, "full_name", "unknown")

        # Получаем тип пользователя из данных (если доступно)
        # В UserDataMiddleware данные пользователя уже должны быть в middleware_data
        if "user_info" in data:
            user_info = data["user_info"]
            user_type = getattr(user_info, 'user_type', 'student')
            user_group = getattr(user_info, 'group', 'unknown')
        else:
            # Fallback: используем значения по умолчанию, данные будут получены позже
            user_type = 'unknown'
            user_group = 'unknown'

        # Определяем тип события и действие
        event_type = type(event).__name__
        action = "unknown"

        # Определяем конкретное действие
        if hasattr(event, 'text') and getattr(event, 'text', None) and not str(getattr(event, 'text', '')).startswith('<MagicMock'):
            # Это сообщение с текстом (не callback и не MagicMock)
            text_value = str(getattr(event, 'text', ''))
            if text_value.startswith('/'):
                action = "command"
                BOT_COMMANDS.labels(command=text_value[1:], user_type=user_type).inc()
            else:
                action = "text_message"
        elif hasattr(event, 'data') and not str(getattr(event, 'data', '')).startswith('<MagicMock'):
            # Это callback query (не MagicMock)
            action = "button_click"
            # Определяем диалог и состояние
            dialog_name = "unknown"
            state = "unknown"
            if "dialog_manager" in data:
                dialog_manager = data["dialog_manager"]
                if hasattr(dialog_manager, 'current_dialog'):
                    dialog_name = getattr(dialog_manager.current_dialog, '__name__', 'unknown')
                if hasattr(dialog_manager, 'current_context'):
                    state = str(dialog_manager.current_context.state)

                DIALOG_USAGE.labels(
                    dialog_name=dialog_name,
                    state=state,
                    user_type=user_type
                ).inc()

        # Определяем функцию/фичу, которую использует пользователь
        feature = self._determine_feature(event, data)

        # Получаем текущий час и день недели
        now = datetime.now()
        hour_of_day = str(now.hour)
        day_of_week = now.strftime('%A').lower()

        # Обновляем метрики
        self._update_metrics(
            action, feature, user_id, user_type, user_group,
            hour_of_day, day_of_week, event_type
        )

        # Логируем действие для Loki с структурированными данными
        log_data = {
            "timestamp": now.isoformat(),
            "user_id": user_id,
            "username": username,
            "user_type": user_type,
            "user_group": user_group,
            "action": action,
            "feature": feature,
            "event_type": event_type,
            "hour_of_day": hour_of_day,
            "day_of_week": day_of_week,
            "message_text": str(getattr(event, 'text', None)) if hasattr(event, 'text') else None,
            "callback_data": str(getattr(event, 'data', None)) if hasattr(event, 'data') else None,
            "chat_id": str(getattr(getattr(event, 'chat', None), 'id', None)) if hasattr(event, 'chat') and hasattr(event.chat, 'id') else None
        }

        # Логируем в формате JSON для лучшей читаемости в Loki
        logger.info("User activity", extra={
            "json": json.dumps(log_data, default=str),  # default=str для обработки несериализуемых объектов
            "user_id": user_id,
            "action": action,
            "feature": feature
        })

        # Обновляем время последнего действия для расчета интервалов
        if user_id != "unknown":
            self.last_user_actions[user_id] = time.time()

        try:
            return await handler(event, data)
        except Exception as e:
            # Логируем ошибки
            logger.error("Error in handler", extra={
                "user_id": user_id,
                "action": action,
                "error": str(e)
            })
            raise

    def _determine_feature(self, event: TelegramObject, data: Dict[str, Any]) -> str:
        """Определяет, какую функцию/фичу использует пользователь."""

        if hasattr(event, 'text') and getattr(event, 'text', None) and not str(getattr(event, 'text', '')).startswith('<MagicMock'):
            text = str(getattr(event, 'text', '')).lower()

            # Команды
            if text.startswith('/'):
                if 'start' in text:
                    return 'registration'
                elif 'help' in text or 'помощь' in text:
                    return 'help'
                elif 'admin' in text:
                    return 'admin_panel'
                elif 'feedback' in text or 'фидбек' in text or 'отзыв' in text:
                    return 'feedback'
                elif 'find' in text or 'поиск' in text:
                    return 'search'
                elif 'settings' in text or 'настройки' in text:
                    return 'settings'
                else:
                    return 'other_commands'

            # Текстовые сообщения
            else:
                return 'text_input'

        elif hasattr(event, 'data') and not str(getattr(event, 'data', '')).startswith('<MagicMock'):
            callback_data = str(getattr(event, 'data', ''))

            # Определяем по callback_data или по диалогу
            if 'schedule' in callback_data.lower() or 'расписани' in callback_data.lower() or 'view' in callback_data.lower():
                return 'schedule_view'
            elif 'search' in callback_data.lower() or 'поиск' in callback_data.lower() or 'find' in callback_data.lower():
                return 'search'
            elif 'settings' in callback_data.lower() or 'настройк' in callback_data.lower() or 'setting' in callback_data.lower():
                return 'settings'
            elif 'admin' in callback_data.lower():
                return 'admin_panel'
            elif 'event' in callback_data.lower() or 'мероприят' in callback_data.lower():
                return 'events'
            elif 'feedback' in callback_data.lower() or 'фидбек' in callback_data.lower():
                return 'feedback'
            elif 'back' in callback_data.lower() or 'cancel' in callback_data.lower() or 'menu' in callback_data.lower():
                return 'navigation'
            else:
                return 'other_commands'

        return 'unknown'

    def _update_metrics(self, action: str, feature: str, user_id: str, user_type: str,
                       user_group: str, hour_of_day: str, day_of_week: str, event_type: str):
        """Обновляет все связанные метрики."""

        # Основные метрики действий
        USER_ACTIONS.labels(action=action, user_type=user_type, source=event_type).inc()
        FEATURE_POPULARITY.labels(feature_name=feature, user_type=user_type, day_of_week=day_of_week).inc()

        # Метрики по времени
        USER_ACTIVITY_DAILY.labels(action_type=action, user_group=user_group, hour_of_day=hour_of_day).inc()
        USER_ACTIVITY_HOURLY.labels(action_type=action, hour_of_day=hour_of_day).inc()

        # Если это новый пользователь (первые действия)
        if action == "command" and user_id not in self.last_user_actions:
            NEW_USERS_DAILY.labels(registration_source="bot_usage", user_type=user_type).inc()

        # Обновляем активные сессии (примерная оценка)
        current_hour = datetime.now().hour
        active_sessions_key = f"{current_hour}_{user_type}"
        ACTIVE_SESSIONS.labels(user_type=user_type).set(len([
            uid for uid, last_time in self.last_user_actions.items()
            if time.time() - last_time < 3600  # Активен в последний час
        ]))
