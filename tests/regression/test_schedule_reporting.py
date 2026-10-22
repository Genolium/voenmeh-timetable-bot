import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs.admin_menu import on_override_input
from bot.dialogs.schedule_view import on_report_error_click
from bot.dialogs.states import Feedback
from core.manager import TimetableManager
from core.schedule_overrides import clear_group_override, get_group_override, set_group_override
from main import override_command_handler


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)


@pytest.mark.asyncio
async def test_override_is_applied_to_group_schedule():
    redis = FakeRedis()
    data = {
        "__metadata__": {},
        "__teachers_index__": {},
        "__classrooms_index__": {},
        "G1": {"odd": {}, "even": {"Четверг": [{"time": "09:00-10:30", "subject": "Old"}]}},
    }
    manager = TimetableManager(data, redis)
    target = date(2026, 10, 22)
    lesson = {"time": "11:00-12:30", "start_time_raw": "11:00", "end_time_raw": "12:30", "subject": "New"}
    await set_group_override(redis, "G1", target, [lesson])

    result = await manager.get_schedule_for_day("G1", target)

    assert result["lessons"] == [lesson]
    assert await get_group_override(redis, "G1", target) == [lesson]
    redis.data[f"schedule_override:G1:{target.isoformat()}"] = json.dumps([lesson]).encode()
    assert await get_group_override(redis, "G1", target) == [lesson]
    await clear_group_override(redis, "G1", target)
    assert await get_group_override(redis, "G1", target) is None


@pytest.mark.asyncio
async def test_admin_override_command_parses_and_saves():
    redis = FakeRedis()
    dm = MagicMock()
    dm.middleware_data = {"redis_client": redis}
    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()
    command = MagicMock(args="G1|2026-10-22|11:00-12:30|New|101|лек")

    with patch("main.ADMIN_IDS", [1]):
        await override_command_handler(message, command, dm)

    stored = await get_group_override(redis, "G1", date(2026, 10, 22))
    assert stored[0]["subject"] == "New"
    assert stored[0]["room"] == "101"
    message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_report_error_opens_feedback_with_context():
    dm = MagicMock()
    dm.current_context.return_value.dialog_data = {
        "group": "G1",
        "current_date_iso": "2026-10-22",
    }
    dm.start = AsyncMock()

    await on_report_error_click(MagicMock(), MagicMock(), dm)

    dm.start.assert_awaited_once()
    assert dm.start.call_args.args[0] is Feedback.enter_feedback
    assert "G1" in dm.start.call_args.kwargs["data"]["feedback_context"]


@pytest.mark.asyncio
async def test_admin_override_ui_window_handler_saves():
    redis = FakeRedis()
    dm = MagicMock()
    dm.middleware_data = {"redis_client": redis}
    dm.switch_to = AsyncMock()
    message = MagicMock()
    message.text = "G1|2026-10-22|11:00-12:30|New|101|лек"
    message.answer = AsyncMock()

    await on_override_input(message, MagicMock(), dm)

    stored = await get_group_override(redis, "G1", date(2026, 10, 22))
    assert stored[0]["subject"] == "New"
    dm.switch_to.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_override_ui_rejects_bad_format():
    dm = MagicMock()
    dm.middleware_data = {"redis_client": FakeRedis()}
    message = MagicMock()
    message.text = "broken"
    message.answer = AsyncMock()

    await on_override_input(message, MagicMock(), dm)

    message.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_override_ui_rejects_bad_time_and_missing_redis():
    dm = MagicMock()
    dm.middleware_data = {"redis_client": FakeRedis()}
    message = MagicMock()
    message.answer = AsyncMock()
    message.text = "G1|2026-10-22|bad|New|101|лек"
    await on_override_input(message, MagicMock(), dm)
    message.answer.assert_awaited_once()

    message.answer.reset_mock()
    dm.middleware_data = {}
    message.text = "G1|2026-10-22|11:00-12:30|New|101|лек"
    await on_override_input(message, MagicMock(), dm)
    message.answer.assert_awaited_once()
