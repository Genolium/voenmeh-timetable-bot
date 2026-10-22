import json
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.middlewares.event_utils import (
    extract_callback_data,
    extract_event_type,
    extract_from_user,
    extract_message,
    extract_text,
)
from core.metrics import (
    ACTIVE_SESSIONS,
    BOT_COMMANDS,
    DIALOG_USAGE,
    FEATURE_POPULARITY,
    NEW_USERS_DAILY,
    USER_ACTION_INTERVAL,
    USER_ACTIONS,
    USER_ACTIVITY_DAILY,
    USER_ACTIVITY_HOURLY,
)
from core.time_utils import now_msk_naive

logger = logging.getLogger(__name__)


class ActivityLoggingMiddleware(BaseMiddleware):
    """
    Middleware для логирования всех действий пользователей в Loki и обновления метрик.
    """

    def __init__(self):
        self.last_user_actions = {}  # Для отслеживания интервалов между действиями
        self.active_sessions: dict[str, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Получаем информацию о пользователе (outer middleware получает Update)
        from_user = extract_from_user(event)
        user_id = "unknown"
        username = "unknown"
        user_type = "unknown"
        user_group = "unknown"

        if from_user is not None and getattr(from_user, "id", None):
            user_id = str(from_user.id)
            username = getattr(from_user, "username", "unknown")
            user_full_name = getattr(from_user, "full_name", "unknown")

        # Получаем тип пользователя из данных (если доступно)
        # В UserDataMiddleware данные пользователя уже должны быть в middleware_data
        if "user_info" in data:
            user_info = data["user_info"]
            user_type = getattr(user_info, "user_type", "student")
            user_group = getattr(user_info, "group", "unknown")
        else:
            # Fallback: используем значения по умолчанию, данные будут получены позже
            user_type = "unknown"
            user_group = "unknown"

        # Определяем тип события и действие
        event_type = extract_event_type(event)
        text_value = extract_text(event)
        callback_data = extract_callback_data(event)
        action = "unknown"

        if text_value and not str(text_value).startswith("<MagicMock"):
            if str(text_value).startswith("/"):
                action = "command"
                BOT_COMMANDS.labels(command=str(text_value)[1:], user_type=user_type).inc()
            else:
                action = "text_message"
        elif callback_data is not None and not str(callback_data).startswith("<MagicMock"):
            action = "button_click"
            dialog_name = "unknown"
            state = "unknown"
            if "dialog_manager" in data:
                dialog_manager = data["dialog_manager"]
                if hasattr(dialog_manager, "current_dialog"):
                    dialog_name = getattr(dialog_manager.current_dialog, "__name__", "unknown")
                if hasattr(dialog_manager, "current_context"):
                    state = str(dialog_manager.current_context.state)

                DIALOG_USAGE.labels(dialog_name=dialog_name, state=state, user_type=user_type).inc()

        # Определяем функцию/фичу, которую использует пользователь
        feature = self._determine_feature(event, data)

        # Получаем текущий час и день недели
        now = now_msk_naive()
        hour_of_day = str(now.hour)
        day_of_week = now.strftime("%A").lower()

        # Обновляем метрики
        self._update_metrics(
            action,
            feature,
            user_id,
            user_type,
            user_group,
            hour_of_day,
            day_of_week,
            event_type,
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
            "message_text": extract_text(event),
            "callback_data": extract_callback_data(event),
            "chat_id": (
                str(getattr(getattr(extract_message(event), "chat", None), "id", None))
                if extract_message(event) is not None
                else None
            ),
        }

        # Логируем в формате JSON для лучшей читаемости в Loki
        logger.info(
            "User activity",
            extra={
                "json": json.dumps(log_data, default=str),  # default=str для обработки несериализуемых объектов
                "user_id": user_id,
                "action": action,
                "feature": feature,
            },
        )

        # Обновляем время последнего действия для расчета интервалов
        if user_id != "unknown":
            self.last_user_actions[user_id] = time.time()

        try:
            return await handler(event, data)
        except Exception as e:
            # Логируем ошибки
            logger.error(
                "Error in handler",
                extra={"user_id": user_id, "action": action, "error": str(e)},
            )
            raise

    def _determine_feature(self, event: TelegramObject, data: Dict[str, Any]) -> str:
        """Определяет, какую функцию/фичу использует пользователь."""

        text_value = extract_text(event)
        if text_value and not str(text_value).startswith("<MagicMock"):
            text = str(text_value).lower()

            # Команды
            if text.startswith("/"):
                if "start" in text:
                    return "registration"
                elif "help" in text or "помощь" in text:
                    return "help"
                elif "admin" in text:
                    return "admin_panel"
                elif "feedback" in text or "фидбек" in text or "отзыв" in text:
                    return "feedback"
                elif "find" in text or "поиск" in text:
                    return "search"
                elif "settings" in text or "настройки" in text:
                    return "settings"
                else:
                    return "other_commands"

            # Текстовые сообщения
            else:
                return "text_input"

        callback_data = extract_callback_data(event)
        if callback_data is not None and not str(callback_data).startswith("<MagicMock"):
            callback_data = str(callback_data)

            # Определяем по callback_data или по диалогу
            if "schedule" in callback_data.lower() or "расписани" in callback_data.lower() or "view" in callback_data.lower():
                return "schedule_view"
            elif "search" in callback_data.lower() or "поиск" in callback_data.lower() or "find" in callback_data.lower():
                return "search"
            elif (
                "settings" in callback_data.lower()
                or "настройк" in callback_data.lower()
                or "setting" in callback_data.lower()
            ):
                return "settings"
            elif "admin" in callback_data.lower():
                return "admin_panel"
            elif "feedback" in callback_data.lower() or "фидбек" in callback_data.lower():
                return "feedback"
            elif "back" in callback_data.lower() or "cancel" in callback_data.lower() or "menu" in callback_data.lower():
                return "navigation"
            else:
                return "other_commands"

        return "unknown"

    def _update_metrics(
        self,
        action: str,
        feature: str,
        user_id: str,
        user_type: str,
        user_group: str,
        hour_of_day: str,
        day_of_week: str,
        event_type: str,
    ):
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

        # Активные сессии: O(1) на событие, редкая очистка вместо полного обхода.
        now_ts = time.time()
        self.active_sessions[user_id] = now_ts
        if len(self.active_sessions) > 1000 and len(self.active_sessions) % 100 == 0:
            cutoff = now_ts - 3600
            self.active_sessions = {uid: ts for uid, ts in self.active_sessions.items() if ts >= cutoff}
        ACTIVE_SESSIONS.labels(user_type=user_type).set(len(self.active_sessions))
