"""Full statement coverage tests for the small bot/middlewares modules."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.middlewares.i18n_middleware import I18nMiddleware
from bot.middlewares.logging_middleware import LoggingMiddleware
from bot.middlewares.manager_middleware import ManagerMiddleware
from bot.middlewares.subscription_filter_middleware import (
    SUB_CHECK_CALLBACK,
    SubscriptionFilterMiddleware,
)
from bot.middlewares.user_data_middleware import UserDataMiddleware


def _user(uid=1, lang=None, username="user"):
    return SimpleNamespace(id=uid, username=username, language_code=lang)


# --------------------------------------------------------------------------- #
# I18nMiddleware
# --------------------------------------------------------------------------- #
class TestI18nMiddleware:
    @pytest.mark.asyncio
    async def test_lang_from_db_user_info(self):
        mw = I18nMiddleware()
        handler = AsyncMock(return_value="ok")
        data = {"user_info": SimpleNamespace(language="en")}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(lang="zh")))
        assert await mw(handler, event, data) == "ok"
        assert data["lang"] == "en"
        assert data["i18n_lang"] == "en"
        assert data["i18n"] is not None
        # string_getter is wired up and usable
        assert isinstance(data["_"]("welcome_title"), str)

    @pytest.mark.asyncio
    async def test_lang_from_telegram_user_zh(self):
        mw = I18nMiddleware()
        handler = AsyncMock(return_value="ok")
        data = {}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(lang="zh-Hans")))
        await mw(handler, event, data)
        assert data["lang"] == "zh"

    @pytest.mark.asyncio
    async def test_lang_from_telegram_user_en(self):
        mw = I18nMiddleware()
        handler = AsyncMock(return_value="ok")
        data = {}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(lang="EN_us")))
        await mw(handler, event, data)
        assert data["lang"] == "en"

    @pytest.mark.asyncio
    async def test_lang_defaults_to_ru_without_any_user(self):
        mw = I18nMiddleware()
        handler = AsyncMock(return_value="ok")
        data = {}
        event = SimpleNamespace(message=None, callback_query=None, inline_query=None)
        await mw(handler, event, data)
        assert data["lang"] == "ru"

    @pytest.mark.asyncio
    async def test_user_info_without_language_falls_back(self):
        mw = I18nMiddleware()
        handler = AsyncMock(return_value="ok")
        data = {"user_info": SimpleNamespace(language=None)}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(lang="en")))
        await mw(handler, event, data)
        assert data["lang"] == "en"


# --------------------------------------------------------------------------- #
# LoggingMiddleware
# --------------------------------------------------------------------------- #
class TestLoggingMiddleware:
    @pytest.mark.asyncio
    async def test_with_named_handler_callback(self):
        mw = LoggingMiddleware()
        handler = AsyncMock(return_value="handled")

        def my_callback(event, data):
            return "x"

        data = {"handler": SimpleNamespace(callback=my_callback)}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(), text="hi"), callback_query=None, inline_query=None)
        assert await mw(handler, event, data) == "handled"
        assert "logger" in data

    @pytest.mark.asyncio
    async def test_without_handler_uses_unknown_name(self):
        mw = LoggingMiddleware()
        handler = AsyncMock(return_value="handled")
        event = SimpleNamespace(from_user=_user(), text="plain")
        assert await mw(handler, event, {}) == "handled"

    @pytest.mark.asyncio
    async def test_handler_callback_without_name(self):
        mw = LoggingMiddleware()
        handler = AsyncMock(return_value="handled")
        data = {"handler": SimpleNamespace(callback=MagicMock())}
        event = SimpleNamespace(message=SimpleNamespace(from_user=None, text=None), callback_query=None, inline_query=None)
        assert await mw(handler, event, data) == "handled"

    @pytest.mark.asyncio
    async def test_no_from_user(self):
        mw = LoggingMiddleware()
        handler = AsyncMock(return_value="handled")
        event = SimpleNamespace(message=None, callback_query=None, inline_query=SimpleNamespace(from_user=None, query="q"))
        assert await mw(handler, event, {}) == "handled"


# --------------------------------------------------------------------------- #
# UserDataMiddleware
# --------------------------------------------------------------------------- #
class TestUserDataMiddleware:
    @pytest.mark.asyncio
    async def test_registers_user(self):
        manager = MagicMock()
        user_info = SimpleNamespace(id=7)
        manager.register_user_and_get = AsyncMock(return_value=user_info)
        mw = UserDataMiddleware(manager)
        handler = AsyncMock(return_value="ok")
        data = {}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(7), text="hi"))
        assert await mw(handler, event, data) == "ok"
        assert data["user_info"] is user_info
        assert data["user_data_manager"] is manager
        manager.register_user_and_get.assert_awaited_once_with(7, "user")

    @pytest.mark.asyncio
    async def test_register_returns_none(self):
        manager = MagicMock()
        manager.register_user_and_get = AsyncMock(return_value=None)
        mw = UserDataMiddleware(manager)
        handler = AsyncMock(return_value="ok")
        data = {}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(7), text="hi"))
        await mw(handler, event, data)
        assert "user_info" not in data

    @pytest.mark.asyncio
    async def test_db_exception_is_swallowed(self):
        manager = MagicMock()
        manager.register_user_and_get = AsyncMock(side_effect=Exception("db down"))
        mw = UserDataMiddleware(manager)
        handler = AsyncMock(return_value="ok")
        data = {}
        event = SimpleNamespace(message=SimpleNamespace(from_user=_user(7), text="hi"))
        assert await mw(handler, event, data) == "ok"

    @pytest.mark.asyncio
    async def test_no_user_id_skips_registration(self):
        manager = MagicMock()
        manager.register_user_and_get = AsyncMock()
        mw = UserDataMiddleware(manager)
        handler = AsyncMock(return_value="ok")
        data = {}
        event = SimpleNamespace(message=None, callback_query=None, inline_query=None)
        assert await mw(handler, event, data) == "ok"
        manager.register_user_and_get.assert_not_awaited()


# --------------------------------------------------------------------------- #
# ManagerMiddleware
# --------------------------------------------------------------------------- #
class TestManagerMiddleware:
    @pytest.mark.asyncio
    async def test_uses_self_manager_when_no_refs(self, monkeypatch):
        import bot.scheduler as scheduler_mod

        monkeypatch.setattr(scheduler_mod, "manager_ref", None, raising=False)
        monkeypatch.setattr(scheduler_mod, "global_timetable_manager_instance", None, raising=False)
        manager = MagicMock()
        mw = ManagerMiddleware(manager)
        handler = AsyncMock(return_value="ok")
        data = {}
        assert await mw(handler, SimpleNamespace(), data) == "ok"
        assert data["manager"] is manager

    @pytest.mark.asyncio
    async def test_uses_manager_ref(self, monkeypatch):
        import bot.scheduler as scheduler_mod

        sentinel = MagicMock()
        monkeypatch.setattr(scheduler_mod, "manager_ref", SimpleNamespace(get=lambda: sentinel), raising=False)
        mw = ManagerMiddleware(MagicMock())
        handler = AsyncMock(return_value="ok")
        data = {}
        await mw(handler, SimpleNamespace(), data)
        assert data["manager"] is sentinel

    @pytest.mark.asyncio
    async def test_uses_global_instance(self, monkeypatch):
        import bot.scheduler as scheduler_mod

        sentinel = MagicMock()
        monkeypatch.setattr(scheduler_mod, "manager_ref", None, raising=False)
        monkeypatch.setattr(scheduler_mod, "global_timetable_manager_instance", sentinel, raising=False)
        mw = ManagerMiddleware(MagicMock())
        handler = AsyncMock(return_value="ok")
        data = {}
        await mw(handler, SimpleNamespace(), data)
        assert data["manager"] is sentinel


# --------------------------------------------------------------------------- #
# SubscriptionFilterMiddleware
# --------------------------------------------------------------------------- #
def _message_update(uid=100, chat_id=100):
    msg = SimpleNamespace(
        from_user=_user(uid),
        chat=SimpleNamespace(id=chat_id),
        answer=AsyncMock(),
    )
    return SimpleNamespace(message=msg, callback_query=None)


def _callback_update(uid=100, data=SUB_CHECK_CALLBACK, chat_id=100, with_message=True):
    cb_msg = SimpleNamespace(chat=SimpleNamespace(id=chat_id), delete=AsyncMock()) if with_message else None
    cb = SimpleNamespace(from_user=_user(uid), data=data, message=cb_msg, answer=AsyncMock())
    return SimpleNamespace(message=None, callback_query=cb)


class TestSubscriptionFilterMiddleware:
    def _mw(self):
        mw = SubscriptionFilterMiddleware(MagicMock(), MagicMock())
        return mw

    def test_get_user_id_and_chat_id(self):
        mw = self._mw()
        assert mw._get_user_id_and_chat_id(_message_update(5, 9)) == (5, 9)
        assert mw._get_user_id_and_chat_id(_callback_update(6, "x", 8)) == (6, 8)
        assert mw._get_user_id_and_chat_id(_callback_update(7, "x", with_message=False)) == (7, None)
        assert mw._get_user_id_and_chat_id(SimpleNamespace(message=None, callback_query=None)) == (None, None)

    def test_build_gatekeeper_markup_and_text(self):
        mw = self._mw()
        markup = mw._build_gatekeeper_markup(
            [
                {"title": "T", "invite_link": "https://t.me/t"},
                {"channel_id": "@chan"},
                {},
            ]
        )
        assert len(markup.inline_keyboard) == 4
        text = mw._build_gatekeeper_text([{"title": "T"}, {}])
        assert "Обязательная подписка" in text
        assert "Канал" in text

    @pytest.mark.asyncio
    async def test_no_user_passes_through(self):
        mw = self._mw()
        handler = AsyncMock(return_value="ok")
        event = SimpleNamespace(message=None, callback_query=None)
        assert await mw(handler, event, {}) == "ok"

    @pytest.mark.asyncio
    async def test_admin_passes_through(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [100])
        mw = self._mw()
        handler = AsyncMock(return_value="ok")
        assert await mw(handler, _message_update(100), {}) == "ok"

    @pytest.mark.asyncio
    async def test_sub_check_without_bot(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        handler = AsyncMock()
        event = _callback_update()
        assert await mw(handler, event, {}) is None
        event.callback_query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sub_check_success_delete_fails(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(True, []))
        handler = AsyncMock()
        event = _callback_update()
        event.callback_query.message.delete = AsyncMock(side_effect=Exception("cannot delete"))
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=Exception("cannot send"))
        assert await mw(handler, event, {"bot": bot}) is None

    @pytest.mark.asyncio
    async def test_sub_check_not_subscribed(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@c"}]))
        handler = AsyncMock()
        event = _callback_update()
        assert await mw(handler, event, {"bot": AsyncMock()}) is None
        event.callback_query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_filter_disabled_passes(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=False)
        handler = AsyncMock(return_value="ok")
        assert await mw(handler, _message_update(), {"bot": AsyncMock()}) == "ok"

    @pytest.mark.asyncio
    async def test_filter_enabled_check_raises(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(side_effect=Exception("db"))
        handler = AsyncMock(return_value="ok")
        assert await mw(handler, _message_update(), {"bot": AsyncMock()}) == "ok"

    @pytest.mark.asyncio
    async def test_get_channels_raises(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(side_effect=Exception("db"))
        handler = AsyncMock(return_value="ok")
        assert await mw(handler, _message_update(), {"bot": AsyncMock()}) == "ok"

    @pytest.mark.asyncio
    async def test_no_channels_passes(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[])
        handler = AsyncMock(return_value="ok")
        assert await mw(handler, _message_update(), {"bot": AsyncMock()}) == "ok"

    @pytest.mark.asyncio
    async def test_no_bot_passes(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c"}])
        handler = AsyncMock(return_value="ok")
        assert await mw(handler, _message_update(), {}) == "ok"

    @pytest.mark.asyncio
    async def test_subscribed_passes(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(True, []))
        handler = AsyncMock(return_value="ok")
        assert await mw(handler, _message_update(), {"bot": AsyncMock()}) == "ok"

    @pytest.mark.asyncio
    async def test_callback_not_subscribed_sends_gatekeeper(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c", "title": "C"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@c"}]))
        handler = AsyncMock()
        event = _callback_update(chat_id=77, data="other")
        event.callback_query.answer = AsyncMock(side_effect=Exception("alert fail"))
        bot = AsyncMock()
        assert await mw(handler, event, {"bot": bot}) is None
        bot.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_not_subscribed_without_chat_id(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c", "title": "C"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@c"}]))
        handler = AsyncMock()
        event = _callback_update(with_message=False, data="other")
        bot = AsyncMock()
        assert await mw(handler, event, {"bot": bot}) is None
        bot.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_gatekeeper_send_fails(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c", "title": "C"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@c"}]))
        handler = AsyncMock()
        event = _callback_update(chat_id=77, data="other")
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=Exception("send fail"))
        assert await mw(handler, event, {"bot": bot}) is None

    @pytest.mark.asyncio
    async def test_message_not_subscribed_sends_gatekeeper(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c", "title": "C"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@c"}]))
        handler = AsyncMock()
        event = _message_update()
        assert await mw(handler, event, {"bot": AsyncMock()}) is None
        event.message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_message_gatekeeper_send_fails(self, monkeypatch):
        monkeypatch.setattr("bot.middlewares.subscription_filter_middleware.ADMIN_IDS", [])
        mw = self._mw()
        mw.manager.is_filter_enabled = AsyncMock(return_value=True)
        mw.manager.get_required_channels = AsyncMock(return_value=[{"channel_id": "@c", "title": "C"}])
        mw.manager.check_user_subscription = AsyncMock(return_value=(False, [{"channel_id": "@c"}]))
        handler = AsyncMock()
        event = _message_update()
        event.message.answer = AsyncMock(side_effect=Exception("fail"))
        assert await mw(handler, event, {"bot": AsyncMock()}) is None


# --------------------------------------------------------------------------- #
# event_utils helpers
# --------------------------------------------------------------------------- #
class TestEventUtils:
    def test_extract_message_variants(self):
        from bot.middlewares import event_utils

        plain = SimpleNamespace(from_user=_user(), text="hi")
        assert event_utils.extract_message(plain) is plain
        msg = SimpleNamespace(text="m")
        assert event_utils.extract_message(SimpleNamespace(message=msg)) is msg
        edited = SimpleNamespace(text="e")
        assert event_utils.extract_message(SimpleNamespace(message=None, edited_message=edited)) is edited
        cb_msg = SimpleNamespace(text="c")
        assert event_utils.extract_message(SimpleNamespace(callback_query=SimpleNamespace(message=cb_msg))) is cb_msg
        assert event_utils.extract_message(SimpleNamespace(callback_query=SimpleNamespace(message=None))) is None
        assert event_utils.extract_message(SimpleNamespace()) is None

    def test_extract_text_variants(self):
        from bot.middlewares import event_utils

        assert event_utils.extract_text(SimpleNamespace(message=SimpleNamespace(text="hello"))) == "hello"
        assert event_utils.extract_text(SimpleNamespace(inline_query=SimpleNamespace(query="q"))) == "q"
        assert event_utils.extract_text(SimpleNamespace()) is None

    def test_extract_callback_data_variants(self):
        from bot.middlewares import event_utils

        assert event_utils.extract_callback_data(SimpleNamespace(callback_query=SimpleNamespace(data="btn"))) == "btn"
        # callback present but data None -> falls back to text extraction
        assert (
            event_utils.extract_callback_data(
                SimpleNamespace(callback_query=SimpleNamespace(data=None), message=SimpleNamespace(text="txt"))
            )
            == "txt"
        )
        assert event_utils.extract_callback_data(SimpleNamespace()) is None

    def test_extract_event_type_fallback(self):
        from bot.middlewares import event_utils

        assert event_utils.extract_event_type(SimpleNamespace(from_user=_user(), text="x")) == "SimpleNamespace"
        assert event_utils.extract_event_type(SimpleNamespace(foo=1)) == "SimpleNamespace"
        assert event_utils.extract_event_type(SimpleNamespace(message=SimpleNamespace())) == "Message"
        assert event_utils.extract_event_type(SimpleNamespace(edited_message=SimpleNamespace())) == "Message"
        assert event_utils.extract_event_type(SimpleNamespace(callback_query=SimpleNamespace())) == "CallbackQuery"
        assert event_utils.extract_event_type(SimpleNamespace(inline_query=SimpleNamespace())) == "InlineQuery"

    def test_extract_from_user_direct_and_inner(self):
        from bot.middlewares import event_utils

        user = _user(42)
        assert event_utils.extract_from_user(SimpleNamespace(from_user=user, text="x")) is user
        assert event_utils.extract_from_user(SimpleNamespace(message=SimpleNamespace(from_user=user))) is user
        assert event_utils.extract_from_user(SimpleNamespace()) is None
