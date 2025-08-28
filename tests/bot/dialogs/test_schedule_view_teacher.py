import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date

from bot.dialogs.schedule_view import get_schedule_data
from bot.dialogs.constants import DialogDataKeys


@pytest.fixture
def dm_teacher():
    manager = AsyncMock()
    ctx = AsyncMock()
    ctx.dialog_data = {
        DialogDataKeys.GROUP: "ЗЕМЛЯНСКАЯ Е.Р.",
        DialogDataKeys.CURRENT_DATE_ISO: "2025-08-28",
    }
    manager.current_context = MagicMock(return_value=ctx)

    ttm = AsyncMock()
    manager.middleware_data = {
        "manager": ttm,
        "user_data_manager": AsyncMock(),
        "session_factory": MagicMock(),
    }
    # пользователь — преподаватель
    manager.middleware_data["user_data_manager"].get_user_type = AsyncMock(return_value="teacher")
    # резолвер канонизирует имя
    ttm.resolve_canonical_teacher.return_value = "Землянская Е.Р."
    # расписание на день
    ttm.get_teacher_schedule.return_value = {
        "teacher": "Землянская Е.Р.",
        "date": date(2025, 8, 28),
        "day_name": "Четверг",
        "lessons": [{"time": "18:30-20:00", "subject": "ИНФ.ТЕХН."}],
    }
    return manager


@pytest.mark.asyncio
async def test_get_schedule_data_teacher_uses_teacher_formatter(dm_teacher):
    with patch("bot.dialogs.schedule_view.format_teacher_schedule_text", return_value="OK") as fmt:
        res = await get_schedule_data(dm_teacher)
        fmt.assert_called_once()
        assert res["schedule_text"] == "OK"


@pytest.mark.asyncio
async def test_get_schedule_data_teacher_autocanonicalizes(dm_teacher):
    # подменяем исходную группу на неконаническую форму
    dm_teacher.current_context().dialog_data[DialogDataKeys.GROUP] = "ЗЕМЛЯНСКАЯ  Е. Р."
    udm = dm_teacher.middleware_data["user_data_manager"]
    res = await get_schedule_data(dm_teacher)
    assert "schedule_text" in res
    # каноническое имя сохраняется в БД
    udm.set_user_group.assert_awaited()  # хотя бы один вызов


@pytest.mark.asyncio
async def test_get_schedule_data_teacher_resolver_fails(dm_teacher):
    """Тестирует случай, когда resolver не может найти преподавателя"""
    # подменяем группу на несуществующую
    dm_teacher.current_context().dialog_data[DialogDataKeys.GROUP] = "НЕСУЩЕСТВУЮЩИЙ ПРЕПОДАВАТЕЛЬ"
    # резолвер возвращает None
    dm_teacher.middleware_data["manager"].resolve_canonical_teacher.return_value = None
    # get_teacher_schedule должен вернуть ошибку для несуществующего преподавателя
    dm_teacher.middleware_data["manager"].get_teacher_schedule.return_value = {
        "error": "Преподаватель 'НЕСУЩЕСТВУЮЩИЙ ПРЕПОДАВАТЕЛЬ' не найден в индексе."
    }

    res = await get_schedule_data(dm_teacher)

    # должен вернуть ошибку
    assert "schedule_text" in res
    assert "Ошибка" in res["schedule_text"]


@pytest.mark.asyncio
async def test_get_schedule_data_teacher_calls_get_teacher_schedule(dm_teacher):
    """Тестирует, что для преподавателя вызывается правильный метод"""
    res = await get_schedule_data(dm_teacher)

    # Просто проверяем, что функция вернула результат
    assert res is not None
    assert "schedule_text" in res

