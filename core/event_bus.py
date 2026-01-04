"""
Event Bus — простая шина событий для слабой связанности компонентов.

Позволяет публиковать события и подписывать на них обработчики.
Поддерживает как синхронные, так и асинхронные обработчики.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# Типы событий
class EventTypes:
    """Константы типов событий."""
    
    # Расписание
    SCHEDULE_UPDATED = "schedule_updated"
    SCHEDULE_FETCHED = "schedule_fetched"
    
    # Пользователи
    USER_REGISTERED = "user_registered"
    USER_GROUP_CHANGED = "user_group_changed"
    USER_SETTINGS_CHANGED = "user_settings_changed"
    
    # Уведомления
    NOTIFICATION_SENT = "notification_sent"
    NOTIFICATION_FAILED = "notification_failed"
    
    # Поиск
    SEARCH_PERFORMED = "search_performed"
    
    # Кэш
    CACHE_INVALIDATED = "cache_invalidated"
    
    # Обратная связь
    FEEDBACK_RECEIVED = "feedback_received"


@dataclass
class Event:
    """Базовый класс события."""
    
    event_type: str
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: Optional[str] = None


class EventBus:
    """
    Простая шина событий с паттерном publish-subscribe.
    
    Пример использования:
    
        # Подписка на событие
        EventBus.subscribe(EventTypes.SCHEDULE_UPDATED, my_handler)
        
        # Публикация события
        await EventBus.publish(EventTypes.SCHEDULE_UPDATED, {
            "old_hash": "abc123",
            "new_hash": "def456",
        })
    """
    
    _handlers: Dict[str, List[Callable]] = {}
    _async_handlers: Dict[str, List[Callable]] = {}
    _one_time_handlers: Dict[str, Set[int]] = {}  # handler ids for one-time handlers
    
    @classmethod
    def subscribe(
        cls,
        event_type: str,
        handler: Callable,
        one_time: bool = False,
    ) -> None:
        """
        Подписывается на событие.
        
        Args:
            event_type: Тип события
            handler: Функция-обработчик (sync или async)
            one_time: Если True, обработчик будет вызван только один раз
        """
        if asyncio.iscoroutinefunction(handler):
            if event_type not in cls._async_handlers:
                cls._async_handlers[event_type] = []
            cls._async_handlers[event_type].append(handler)
        else:
            if event_type not in cls._handlers:
                cls._handlers[event_type] = []
            cls._handlers[event_type].append(handler)
        
        if one_time:
            if event_type not in cls._one_time_handlers:
                cls._one_time_handlers[event_type] = set()
            cls._one_time_handlers[event_type].add(id(handler))
        
        logger.debug(f"Subscribed handler {handler.__name__} to {event_type}")
    
    @classmethod
    def unsubscribe(cls, event_type: str, handler: Callable) -> None:
        """Отписывается от события."""
        if event_type in cls._handlers:
            try:
                cls._handlers[event_type].remove(handler)
            except ValueError:
                pass
        
        if event_type in cls._async_handlers:
            try:
                cls._async_handlers[event_type].remove(handler)
            except ValueError:
                pass
    
    @classmethod
    async def publish(
        cls,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ) -> int:
        """
        Публикует событие всем подписчикам.
        
        Args:
            event_type: Тип события
            data: Данные события
            source: Источник события
            
        Returns:
            Количество вызванных обработчиков
        """
        event = Event(
            event_type=event_type,
            data=data or {},
            source=source,
        )
        
        handlers_called = 0
        handlers_to_remove = []
        async_handlers_to_remove = []
        
        # Вызываем синхронные обработчики
        for handler in cls._handlers.get(event_type, []):
            try:
                handler(event)
                handlers_called += 1
                
                # Проверяем, был ли это one-time handler
                if cls._should_remove_handler(event_type, handler):
                    handlers_to_remove.append(handler)
                    
            except Exception as e:
                logger.error(f"Error in sync event handler {handler.__name__}: {e}")
        
        # Вызываем асинхронные обработчики
        for handler in cls._async_handlers.get(event_type, []):
            try:
                await handler(event)
                handlers_called += 1
                
                # Проверяем, был ли это one-time handler
                if cls._should_remove_handler(event_type, handler):
                    async_handlers_to_remove.append(handler)
                    
            except Exception as e:
                logger.error(f"Error in async event handler {handler.__name__}: {e}")
        
        # Удаляем one-time handlers
        for handler in handlers_to_remove:
            cls._handlers[event_type].remove(handler)
        for handler in async_handlers_to_remove:
            cls._async_handlers[event_type].remove(handler)
        
        if handlers_called > 0:
            logger.debug(
                f"Published event {event_type}, called {handlers_called} handlers"
            )
        
        return handlers_called
    
    @classmethod
    def _should_remove_handler(cls, event_type: str, handler: Callable) -> bool:
        """Проверяет, нужно ли удалить обработчик после вызова."""
        handler_id = id(handler)
        one_time_set = cls._one_time_handlers.get(event_type, set())
        if handler_id in one_time_set:
            one_time_set.discard(handler_id)
            return True
        return False
    
    @classmethod
    def clear_all(cls) -> None:
        """Очищает все подписки (для тестов)."""
        cls._handlers.clear()
        cls._async_handlers.clear()
        cls._one_time_handlers.clear()
    
    @classmethod
    def list_handlers(cls, event_type: Optional[str] = None) -> Dict[str, int]:
        """Возвращает количество обработчиков по типам событий."""
        result = {}
        
        all_types = set(cls._handlers.keys()) | set(cls._async_handlers.keys())
        
        for et in all_types:
            if event_type and et != event_type:
                continue
            count = len(cls._handlers.get(et, [])) + len(cls._async_handlers.get(et, []))
            if count > 0:
                result[et] = count
        
        return result


# === Удобные декораторы ===

def on_event(event_type: str, one_time: bool = False):
    """
    Декоратор для подписки функции на событие.
    
    Пример:
        @on_event(EventTypes.SCHEDULE_UPDATED)
        async def handle_schedule_update(event: Event):
            print(f"Schedule updated: {event.data}")
    """
    def decorator(func: Callable) -> Callable:
        EventBus.subscribe(event_type, func, one_time=one_time)
        return func
    return decorator
