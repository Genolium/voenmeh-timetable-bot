import pytest
from unittest.mock import AsyncMock, ANY
from bot.dialogs.feedback_menu import on_feedback_received

@pytest.fixture
def mock_manager(mocker):
    manager = AsyncMock()
    manager.middleware_data = {"bot": AsyncMock(), "session_factory": AsyncMock()}
    return manager

@pytest.mark.asyncio
async def test_on_feedback_received(mock_manager, mocker):
    # Мокируем FEEDBACK_CHAT_ID
    mocker.patch('bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID', '-12345')

    bot = mock_manager.middleware_data["bot"]
    session_factory = mock_manager.middleware_data["session_factory"]

    # Создаем мок для FeedbackManager
    mock_feedback_manager = AsyncMock()
    mock_feedback = AsyncMock()
    mock_feedback_manager.create_feedback.return_value = mock_feedback

    # Мокаем импорт FeedbackManager
    mocker.patch('bot.dialogs.feedback_menu.FeedbackManager', return_value=mock_feedback_manager)

    mock_message = AsyncMock()
    mock_message.from_user.id = 111
    mock_message.from_user.full_name = "Test User"
    mock_message.from_user.username = "testuser"
    mock_message.text = "Test feedback message"

    await on_feedback_received(mock_message, None, mock_manager)

    # Проверяем, что был создан фидбек в БД
    mock_feedback_manager.create_feedback.assert_called_once_with(
        user_id=111,
        username="testuser",
        user_full_name="Test User",
        message_text="Test feedback message",
        message_type="text",
        file_id=None
    )

    # Проверяем, что сообщение было переслано в чат фидбека
    bot.forward_message.assert_called_once_with(chat_id='-12345', from_chat_id=ANY, message_id=ANY)
    # Проверяем, что была отправлена доп. информация в чат фидбека
    assert bot.send_message.call_count >= 1
    assert "Новый фидбэк!" in bot.send_message.call_args_list[0].args[1]
    # Проверяем ответ пользователю
    mock_message.answer.assert_called_once()
    assert "Спасибо! Ваш отзыв отправлен." in mock_message.answer.call_args.args[0]
    # Проверяем, что диалог завершился
    mock_manager.done.assert_called_once()