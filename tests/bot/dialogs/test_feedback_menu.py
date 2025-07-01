import pytest
from unittest.mock import AsyncMock, ANY
from bot.dialogs.feedback_menu import on_feedback_received

@pytest.fixture
def mock_manager(mocker):
    manager = AsyncMock()
    manager.middleware_data = {"bot": AsyncMock()}
    return manager

@pytest.mark.asyncio
async def test_on_feedback_received(mock_manager, mocker):
    # Мокируем FEEDBACK_CHAT_ID
    mocker.patch('bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID', '-12345')
    
    bot = mock_manager.middleware_data["bot"]
    mock_message = AsyncMock()
    mock_message.from_user.id = 111
    mock_message.from_user.full_name = "Test User"
    mock_message.from_user.username = "testuser"
    
    await on_feedback_received(mock_message, None, mock_manager)
    
    # Проверяем, что сообщение было переслано
    bot.forward_message.assert_called_once_with(chat_id='-12345', from_chat_id=ANY, message_id=ANY)
    # Проверяем, что была отправлена доп. информация
    bot.send_message.assert_called_once_with('-12345', ANY)
    assert "Новый фидбэк!" in bot.send_message.call_args.args[1]
    # Проверяем ответ пользователю
    mock_message.answer.assert_called_once()
    assert "Спасибо! Ваш отзыв отправлен." in mock_message.answer.call_args.args[0]
    # Проверяем, что диалог завершился
    mock_manager.done.assert_called_once()