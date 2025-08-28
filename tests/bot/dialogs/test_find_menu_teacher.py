import pytest
from unittest.mock import AsyncMock

from bot.dialogs.find_menu import get_find_data
from bot.dialogs.constants import DialogDataKeys


@pytest.mark.asyncio
async def test_find_menu_teacher_path_uses_resolver():
    manager = AsyncMock()
    manager.dialog_data = {
        DialogDataKeys.TEACHER_NAME: "ЗЕМЛЯНСКАЯ  Е. Р.",
        DialogDataKeys.CURRENT_DATE_ISO: "2025-08-28",
    }
    ttm = AsyncMock()
    ttm.resolve_canonical_teacher.return_value = "Землянская Е.Р."
    ttm.get_teacher_schedule.return_value = {"teacher": "Землянская Е.Р.", "date": __import__("datetime").date(2025, 8, 28), "day_name": "Четверг", "lessons": []}
    manager.middleware_data = {"manager": ttm}

    res = await get_find_data(manager)
    ttm.resolve_canonical_teacher.assert_called_once()
    ttm.get_teacher_schedule.assert_awaited()
    assert "result_text" in res

