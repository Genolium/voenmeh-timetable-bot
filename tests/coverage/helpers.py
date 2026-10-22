"""Shared helpers for the dialog coverage tests.

All Telegram/DB/Redis/image IO is mocked here so that the dialog handlers can be
exercised in isolation.
"""

from unittest.mock import AsyncMock, MagicMock


def make_dm(middleware=None, dialog_data=None, start_data=None, from_user_id=123, event=None):
    """Build a DialogManager-like MagicMock.

    ``current_context().dialog_data`` and ``dialog_manager.dialog_data`` point to
    the same real dict so mutations are observable.
    """
    dm = MagicMock()
    ctx = MagicMock()
    ctx.dialog_data = dialog_data if dialog_data is not None else {}
    dm.current_context = MagicMock(return_value=ctx)
    dm.dialog_data = ctx.dialog_data
    dm.middleware_data = middleware if middleware is not None else {}
    dm.start_data = start_data if start_data is not None else {}
    dm.switch_to = AsyncMock()
    dm.start = AsyncMock()
    dm.done = AsyncMock()
    if event is None:
        event = MagicMock()
        event.from_user = MagicMock()
        event.from_user.id = from_user_id
        event.from_user.username = "tester"
        event.from_user.full_name = "Test User"
    dm.event = event
    return dm


def make_callback(user_id=123, data=None, message=None):
    callback = AsyncMock()
    callback.from_user = MagicMock()
    callback.from_user.id = user_id
    callback.from_user.username = "tester"
    callback.from_user.full_name = "Test User"
    callback.data = data
    callback.message = message if message is not None else AsyncMock()
    callback.answer = AsyncMock()
    return callback


def make_message(text=None, user_id=123):
    message = MagicMock()
    message.text = text
    message.caption = None
    message.photo = None
    message.video = None
    message.document = None
    message.audio = None
    message.voice = None
    message.sticker = None
    message.message_id = 42
    message.chat = MagicMock()
    message.chat.id = 555
    message.from_user = MagicMock()
    message.from_user.id = user_id
    message.from_user.username = "tester"
    message.from_user.full_name = "Test User"
    message.answer = AsyncMock()
    return message


def make_timetable_manager(**attrs):
    """A TimetableManager stand-in that is safe for ``hasattr(..., 'redis')``."""
    tm = MagicMock()
    tm.redis = AsyncMock()
    tm._schedules = {}
    tm._teachers_index = {}
    tm.get_teacher_schedule = AsyncMock(return_value={"lessons": []})
    tm.get_classroom_schedule = AsyncMock(return_value={"lessons": []})
    tm.get_schedule_for_day = AsyncMock(return_value={"lessons": []})
    tm.get_academic_week_type = AsyncMock(return_value=("odd", "Odd week"))
    tm.resolve_canonical_teacher = MagicMock(return_value=None)
    tm.find_teachers = MagicMock(return_value=[])
    tm.find_classrooms = MagicMock(return_value=[])
    tm.find_teachers_fuzzy = MagicMock(return_value=[])
    for key, value in attrs.items():
        setattr(tm, key, value)
    return tm
