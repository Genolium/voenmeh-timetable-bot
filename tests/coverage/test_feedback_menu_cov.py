from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs import feedback_menu as fm
from tests.coverage.helpers import make_dm, make_message


def _manager():
    udm = AsyncMock()
    udm.get_user_language.return_value = "ru"
    bot = AsyncMock()
    dm = make_dm(
        middleware={
            "bot": bot,
            "session_factory": MagicMock(),
            "user_data_manager": udm,
        }
    )
    return dm, bot


@pytest.fixture(autouse=True)
def _patch_feedback_manager():
    manager_instance = AsyncMock()
    with patch("bot.dialogs.feedback_menu.FeedbackManager", return_value=manager_instance) as m:
        yield m


class TestGetFeedbackData:
    async def test_returns_enter_text(self):
        udm = AsyncMock()
        udm.get_user_language.return_value = "en"
        dm = make_dm(middleware={"user_data_manager": udm})
        data = await fm.get_feedback_data(dm)
        assert "feedback_enter" in data


class TestOnFeedbackReceived:
    async def test_missing_chat_id(self):
        dm, bot = _manager()
        message = make_message(text="hello")
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", None):
            await fm.on_feedback_received(message, MagicMock(), dm)
        dm.done.assert_awaited_once()
        bot.forward_message.assert_not_awaited()
        message.answer.assert_awaited()

    async def test_text(self):
        dm, bot = _manager()
        message = make_message(text="hello world")
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        fm.FeedbackManager.return_value.create_feedback.assert_awaited_once()
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "text"
        assert kwargs["file_id"] is None
        bot.forward_message.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        dm.done.assert_awaited_once()

    async def test_photo(self):
        dm, bot = _manager()
        message = make_message()
        message.photo = [MagicMock(file_id="small"), MagicMock(file_id="big")]
        message.caption = "my caption"
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "photo"
        assert kwargs["file_id"] == "big"

    async def test_photo_without_caption(self):
        dm, bot = _manager()
        message = make_message()
        message.photo = [MagicMock(file_id="only")]
        message.caption = None
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "photo"

    async def test_video(self):
        dm, bot = _manager()
        message = make_message()
        message.video = MagicMock(file_id="vid")
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "video"
        assert kwargs["file_id"] == "vid"

    async def test_document(self):
        dm, bot = _manager()
        message = make_message()
        message.document = MagicMock(file_id="doc", file_name="report.pdf")
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "document"
        assert kwargs["file_id"] == "doc"

    async def test_audio(self):
        dm, bot = _manager()
        message = make_message()
        message.audio = MagicMock(file_id="aud")
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "audio"
        assert kwargs["file_id"] == "aud"

    async def test_voice(self):
        dm, bot = _manager()
        message = make_message()
        message.voice = MagicMock(file_id="voi")
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "voice"
        assert kwargs["file_id"] == "voi"

    async def test_sticker(self):
        dm, bot = _manager()
        message = make_message()
        message.sticker = MagicMock(file_id="stk", emoji="😀")
        with patch("bot.dialogs.feedback_menu.FEEDBACK_CHAT_ID", "-100"):
            await fm.on_feedback_received(message, MagicMock(), dm)
        kwargs = fm.FeedbackManager.return_value.create_feedback.await_args.kwargs
        assert kwargs["message_type"] == "sticker"
        assert kwargs["file_id"] == "stk"
