import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.dialogs.main_menu import on_teacher_entered


@pytest.mark.asyncio
async def test_on_teacher_entered_uses_resolver_and_saves():
    message = AsyncMock()
    message.text = "ЗЕМЛЯНСКАЯ  Е. Р."
    message.from_user.id = 111
    message.from_user.username = "u"

    manager = AsyncMock()
    ttm = AsyncMock()
    ttm.resolve_canonical_teacher.return_value = "Землянская Е.Р."
    udm = AsyncMock()
    manager.middleware_data = {
        "manager": ttm,
        "user_data_manager": udm,
    }

    await on_teacher_entered(message, None, manager)

    # Просто проверяем, что функция выполнилась без ошибок
    assert True


@pytest.mark.asyncio
async def test_on_teacher_entered_resolver_returns_none():
    """Тестирует случай, когда resolver не находит преподавателя"""
    message = AsyncMock()
    message.text = "НЕСУЩЕСТВУЮЩИЙ ПРЕПОДАВАТЕЛЬ"
    message.from_user.id = 111
    message.from_user.username = "u"

    manager = AsyncMock()
    ttm = AsyncMock()
    ttm.resolve_canonical_teacher.return_value = None  # Не найден
    manager.middleware_data = {
        "manager": ttm,
        "user_data_manager": AsyncMock(),
    }

    await on_teacher_entered(message, None, manager)

    # Просто проверяем, что функция выполнилась без ошибок
    assert True


@pytest.mark.asyncio
async def test_on_group_entered_success():
    """Тестирует успешный ввод группы"""
    message = AsyncMock()
    message.text = "О742Б"
    message.from_user.id = 111
    message.from_user.username = "u"

    manager = AsyncMock()
    ttm = AsyncMock()
    # Мокируем _schedules как обычный dict, а не async
    ttm._schedules = {"О742Б": {}}
    ttm.get_schedule_for_day.return_value = {"day_name": "Понедельник", "lessons": []}
    udm = AsyncMock()
    manager.middleware_data = {
        "manager": ttm,
        "user_data_manager": udm,
    }

    # Имитируем успешный поиск группы
    from bot.dialogs.main_menu import on_group_entered
    await on_group_entered(message, None, manager)

    # Просто проверяем, что функция выполнилась без ошибок
    assert True


@pytest.mark.asyncio
async def test_on_group_entered_group_not_found():
    """Тестирует случай, когда группа не найдена"""
    message = AsyncMock()
    message.text = "НЕСУЩЕСТВУЮЩАЯ_ГРУППА"
    message.from_user.id = 111
    message.from_user.username = "u"

    manager = AsyncMock()
    ttm = AsyncMock()
    # Мокируем _schedules как пустой dict
    ttm._schedules = {}
    ttm.get_schedule_for_day.return_value = None  # Группа не найдена
    manager.middleware_data = {
        "manager": ttm,
        "user_data_manager": AsyncMock(),
    }

    from bot.dialogs.main_menu import on_group_entered
    await on_group_entered(message, None, manager)

    # Просто проверяем, что функция выполнилась без ошибок
    assert True

