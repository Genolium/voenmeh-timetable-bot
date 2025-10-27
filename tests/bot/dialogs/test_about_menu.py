from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiogram_dialog import StartMode

from bot.dialogs.about_menu import on_finish_clicked
from bot.dialogs.states import Schedule


@pytest.fixture
def mock_manager():
    manager = AsyncMock()
    manager.middleware_data = {"user_data_manager": AsyncMock()}
    return manager


@pytest.mark.asyncio
async def test_on_finish_clicked_success(mock_manager):
    # Arrange
    mock_callback = AsyncMock()
    mock_callback.from_user.id = 123
    mock_callback.message.delete = AsyncMock()

    udm = mock_manager.middleware_data["user_data_manager"]
    udm.get_user_group.return_value = "О735Б"

    # Act
    await on_finish_clicked(mock_callback, MagicMock(), mock_manager)

    # Assert
    mock_callback.message.delete.assert_called_once()
    udm.get_user_group.assert_called_once_with(123)
    mock_manager.start.assert_called_once_with(Schedule.view, data={"group": "О735Б"}, mode=StartMode.RESET_STACK)


@pytest.mark.asyncio
async def test_on_finish_clicked_delete_error(mock_manager):
    """
    Тестируем случай, когда сообщение слишком старое и не может быть удалено.
    Функция on_finish_clicked должна перехватить исключение и продолжить работу.
    """
    # Arrange
    mock_callback = AsyncMock()
    mock_callback.from_user.id = 123
    mock_callback.message.delete = AsyncMock(
        side_effect=TelegramBadRequest(
            method=DeleteMessage(chat_id=1, message_id=1),
            message="message can't be deleted",
        )
    )

    udm = mock_manager.middleware_data["user_data_manager"]
    udm.get_user_group.return_value = "О735Б"

    # Act
    # Просто вызываем функцию. Если она не обработает исключение, тест упадет.
    await on_finish_clicked(mock_callback, MagicMock(), mock_manager)

    # Assert
    # Убеждаемся, что попытка удаления была
    mock_callback.message.delete.assert_called_once()
    # Самое главное: убеждаемся, что код продолжил работу и вызвал .start()
    mock_manager.start.assert_called_once()
