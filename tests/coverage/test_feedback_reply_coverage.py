"""Full statement coverage tests for bot/handlers/feedback_reply_handler.py.

The handler is only defined at import time when FEEDBACK_CHAT_ID is set, so the
module is executed with a controlled ``core.config.FEEDBACK_CHAT_ID`` value.
"""

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.config as config
from bot.handlers import feedback_reply_handler as frh

_MODULE_PATH = pathlib.Path(frh.__file__).resolve()


def _load(feedback_chat_id):
    original = config.FEEDBACK_CHAT_ID
    config.FEEDBACK_CHAT_ID = feedback_chat_id
    try:
        source = _MODULE_PATH.read_text()
        code = compile(source, str(_MODULE_PATH), "exec")
        namespace = {"__name__": "feedback_reply_handler_cov", "__file__": str(_MODULE_PATH)}
        exec(code, namespace)
        return namespace
    finally:
        config.FEEDBACK_CHAT_ID = original


@pytest.fixture
def handler_module():
    return _load("-1001234567890")


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


def _session_factory(feedback):
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=feedback)))
    return lambda: _SessionCtx(session)


def _message(
    reply_to=None,
    text="Ответ",
    caption=None,
    content_type="text",
    chat_id=-1001234567890,
    message_id=10,
):
    return SimpleNamespace(
        reply_to_message=reply_to,
        text=text,
        caption=caption,
        content_type=content_type,
        chat=SimpleNamespace(id=chat_id),
        message_id=message_id,
        from_user=SimpleNamespace(id=999),
        reply=AsyncMock(),
    )


class TestModuleImportBranches:
    def test_invalid_feedback_chat_id(self):
        ns = _load("not-a-number")
        assert ns["FEEDBACK_CHAT_ID_INT"] is None
        assert "handle_feedback_reply" not in ns

    def test_none_feedback_chat_id(self):
        ns = _load(None)
        assert ns["FEEDBACK_CHAT_ID_INT"] is None
        assert "handle_feedback_reply" not in ns


class TestHandler:
    @pytest.mark.asyncio
    async def test_no_reply_to_message(self, handler_module):
        handler = handler_module["handle_feedback_reply"]
        message = _message(reply_to=None)
        await handler(message, AsyncMock(), _session_factory(None))
        message.reply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forward_from_text(self, handler_module, monkeypatch):
        handler = handler_module["handle_feedback_reply"]
        manager = MagicMock()
        manager.answer_feedback = AsyncMock()
        monkeypatch.setitem(handler_module, "FeedbackManager", MagicMock(return_value=manager))
        feedback = SimpleNamespace(id=5)
        replied = SimpleNamespace(
            forward_from=SimpleNamespace(id=42),
            text="hello",
            caption=None,
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied)
        bot = AsyncMock()
        await handler(message, bot, _session_factory(feedback))
        bot.send_message.assert_awaited_once()
        manager.answer_feedback.assert_awaited_once()
        message.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_text_id_pattern(self, handler_module, monkeypatch):
        handler = handler_module["handle_feedback_reply"]
        manager = MagicMock()
        manager.answer_feedback = AsyncMock()
        monkeypatch.setitem(handler_module, "FeedbackManager", MagicMock(return_value=manager))
        replied = SimpleNamespace(
            forward_from=None,
            text="User ID: <code>777</code>",
            caption=None,
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied)
        bot = AsyncMock()
        await handler(message, bot, _session_factory(None))
        assert bot.send_message.call_args.kwargs["chat_id"] == 777

    @pytest.mark.asyncio
    async def test_caption_id_pattern(self, handler_module, monkeypatch):
        handler = handler_module["handle_feedback_reply"]
        manager = MagicMock()
        manager.answer_feedback = AsyncMock()
        monkeypatch.setitem(handler_module, "FeedbackManager", MagicMock(return_value=manager))
        replied = SimpleNamespace(
            forward_from=None,
            text=None,
            caption="ID: 888",
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied)
        bot = AsyncMock()
        await handler(message, bot, _session_factory(None))
        assert bot.send_message.call_args.kwargs["chat_id"] == 888

    @pytest.mark.asyncio
    async def test_hidden_sender_name(self, handler_module):
        handler = handler_module["handle_feedback_reply"]
        replied = SimpleNamespace(
            forward_from=None,
            text=None,
            caption=None,
            forward_sender_name="Hidden",
            forward_from_chat=None,
        )
        message = _message(reply_to=replied)
        await handler(message, AsyncMock(), _session_factory(None))
        assert "скрыл пересылку" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_hidden_forward_from_chat(self, handler_module):
        handler = handler_module["handle_feedback_reply"]
        replied = SimpleNamespace(
            forward_from=None,
            text=None,
            caption=None,
            forward_sender_name=None,
            forward_from_chat=SimpleNamespace(id=1),
        )
        message = _message(reply_to=replied)
        await handler(message, AsyncMock(), _session_factory(None))
        assert "скрыл пересылку" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cannot_determine_user(self, handler_module):
        handler = handler_module["handle_feedback_reply"]
        replied = SimpleNamespace(
            forward_from=None,
            text="no id here",
            caption=None,
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied)
        await handler(message, AsyncMock(), _session_factory(None))
        assert "Не удалось определить" in message.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_media_copy_message(self, handler_module, monkeypatch):
        handler = handler_module["handle_feedback_reply"]
        monkeypatch.setitem(handler_module, "FeedbackManager", MagicMock(return_value=MagicMock(answer_feedback=AsyncMock())))
        replied = SimpleNamespace(
            forward_from=SimpleNamespace(id=42),
            text=None,
            caption=None,
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied, text=None, caption="pic", content_type="photo")
        bot = AsyncMock()
        await handler(message, bot, _session_factory(None))
        bot.copy_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_admin_response_when_no_text(self, handler_module, monkeypatch):
        handler = handler_module["handle_feedback_reply"]
        monkeypatch.setitem(handler_module, "FeedbackManager", MagicMock(return_value=MagicMock(answer_feedback=AsyncMock())))
        replied = SimpleNamespace(
            forward_from=SimpleNamespace(id=42),
            text="x",
            caption=None,
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied, text=None, caption=None, content_type="text")
        bot = AsyncMock()
        await handler(message, bot, _session_factory(None))
        assert "Администратор прислал вам сообщение" in bot.send_message.call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_mark_answered_error_is_logged(self, handler_module, monkeypatch):
        handler = handler_module["handle_feedback_reply"]
        monkeypatch.setitem(
            handler_module,
            "FeedbackManager",
            MagicMock(side_effect=Exception("db down")),
        )
        replied = SimpleNamespace(
            forward_from=SimpleNamespace(id=42),
            text="x",
            caption=None,
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied)
        bot = AsyncMock()
        await handler(message, bot, _session_factory(None))
        message.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_error_replies_with_failure(self, handler_module):
        handler = handler_module["handle_feedback_reply"]
        replied = SimpleNamespace(
            forward_from=SimpleNamespace(id=42),
            text="x",
            caption=None,
            forward_sender_name=None,
            forward_from_chat=None,
        )
        message = _message(reply_to=replied)
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=Exception("telegram down"))
        await handler(message, bot, _session_factory(None))
        assert "Ошибка при отправке ответа" in message.reply.call_args[0][0]
