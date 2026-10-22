from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.dialogs import settings_menu as sm
from bot.dialogs.states import SettingsMenu
from tests.coverage.helpers import make_callback, make_dm

THEME_ID = "standard"


# ---------------------------------------------------------------------------
# helpers / pure functions
# ---------------------------------------------------------------------------


def test_get_status_text_and_button_text():
    assert sm.get_status_text(True, "ru")
    assert sm.get_status_text(False, "ru")
    assert sm.get_button_text(True, "evening", "ru")
    assert sm.get_button_text(False, "reminders", "ru")


@pytest.mark.parametrize(
    "channel,expected",
    [
        ("@my_channel", "https://t.me/my_channel"),
        ("-1001234567890", "tg://resolve?domain=-1001234567890"),
        ("plain_channel", "https://t.me/plain_channel"),
        ("https://t.me/already", "https://t.me/already"),
    ],
)
def test_build_channel_link(channel, expected):
    assert sm._build_channel_link(channel) == expected


# ---------------------------------------------------------------------------
# _is_theme_subscribed
# ---------------------------------------------------------------------------


class TestIsThemeSubscribed:
    async def test_no_channel_configured_returns_true(self):
        with patch("core.config.SUBSCRIPTION_CHANNEL", None):
            assert await sm._is_theme_subscribed(1, make_dm()) is True

    async def test_no_bot_returns_false(self):
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            assert await sm._is_theme_subscribed(1, make_dm(middleware={})) is False

    async def test_member(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="creator")
        dm = make_dm(middleware={"bot": bot})
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            assert await sm._is_theme_subscribed(1, dm) is True

    async def test_error_returns_false(self):
        bot = AsyncMock()
        bot.get_chat_member.side_effect = RuntimeError("boom")
        dm = make_dm(middleware={"bot": bot})
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            assert await sm._is_theme_subscribed(1, dm) is False


# ---------------------------------------------------------------------------
# get_settings_data
# ---------------------------------------------------------------------------


class TestGetSettingsData:
    def _dm(self, settings, lang="ru"):
        udm = AsyncMock()
        udm.get_user_settings.return_value = settings
        udm.get_user_language.return_value = lang
        return make_dm(middleware={"user_data_manager": udm})

    async def test_all_enabled_ru(self):
        dm = self._dm(
            {
                "evening_notify": True,
                "morning_summary": True,
                "lesson_reminders": True,
                "reminder_time_minutes": 30,
                "theme": "DARK",
                "morning_time": "07:00",
                "evening_time": "21:00",
            }
        )
        data = await sm.get_settings_data(dm)
        assert data["current_theme"] == "dark"
        assert data["current_lang_code"] == "ru"
        assert data["ru_checked"] == "✅"

    async def test_all_disabled_en(self):
        dm = self._dm({}, lang="en")
        data = await sm.get_settings_data(dm)
        assert data["en_checked"] == "✅"
        assert data["evening_status_text"]

    async def test_unknown_language_falls_back(self):
        dm = self._dm({"theme": "standard"}, lang="de")
        data = await sm.get_settings_data(dm)
        assert data["lang_ru"]


# ---------------------------------------------------------------------------
# toggles and selectors
# ---------------------------------------------------------------------------


class TestSelectors:
    async def test_on_toggle_setting(self):
        udm = AsyncMock()
        udm.get_user_settings.return_value = {"evening_notify": False}
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        button = MagicMock(widget_id="evening_notify")

        await sm.on_toggle_setting(callback, button, dm)

        udm.update_setting.assert_awaited_once_with(123, "evening_notify", True)
        dm.switch_to.assert_awaited_with(SettingsMenu.main)

    async def test_on_time_selected(self):
        udm = AsyncMock()
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        await sm.on_time_selected(callback, MagicMock(), dm, "45")
        udm.set_reminder_time.assert_awaited_once_with(123, 45)
        dm.switch_to.assert_awaited_with(SettingsMenu.main)

    async def test_on_morning_time_selected(self):
        udm = AsyncMock()
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        await sm.on_morning_time_selected(callback, MagicMock(), dm, "06:00")
        udm.set_morning_time.assert_awaited_once_with(123, "06:00")

    async def test_on_evening_time_selected(self):
        udm = AsyncMock()
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        await sm.on_evening_time_selected(callback, MagicMock(), dm, "22:00")
        udm.set_evening_time.assert_awaited_once_with(123, "22:00")

    async def test_on_lang_selected(self):
        udm = AsyncMock()
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        await sm.on_lang_selected(callback, MagicMock(), dm, "en")
        udm.set_user_language.assert_awaited_once_with(123, "en")
        dm.switch_to.assert_awaited_with(SettingsMenu.main)

    async def test_on_back_click(self):
        dm = make_dm()
        await sm.on_back_click(make_callback(), MagicMock(), dm)
        dm.done.assert_awaited_once()


# ---------------------------------------------------------------------------
# theme button / gate
# ---------------------------------------------------------------------------


class TestThemeButton:
    async def test_not_subscribed_shows_gate(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_button_click(callback, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(SettingsMenu.theme_subscription_gate)
        callback.message.answer.assert_awaited()

    async def test_not_subscribed_answer_raises(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        callback.message.answer.side_effect = RuntimeError("cannot answer")
        callback.answer.side_effect = RuntimeError("cannot answer")
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_button_click(callback, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(SettingsMenu.theme_subscription_gate)

    async def test_subscribed_goes_to_choose_theme(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = make_dm(middleware={"bot": bot})
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_button_click(make_callback(), MagicMock(), dm)
        dm.switch_to.assert_awaited_with(SettingsMenu.choose_theme)

    async def test_no_channel_goes_to_choose_theme(self):
        dm = make_dm()
        with patch("core.config.SUBSCRIPTION_CHANNEL", None):
            await sm.on_theme_button_click(make_callback(), MagicMock(), dm)
        dm.switch_to.assert_awaited_with(SettingsMenu.choose_theme)


class TestCheckThemeSubscription:
    async def test_subscribed(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_check_theme_subscription(callback, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(SettingsMenu.choose_theme)

    async def test_subscribed_answer_raises(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("boom")
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_check_theme_subscription(callback, MagicMock(), dm)
        dm.switch_to.assert_awaited_with(SettingsMenu.choose_theme)

    async def test_not_subscribed(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_check_theme_subscription(callback, MagicMock(), dm)
        callback.answer.assert_awaited()

    async def test_not_subscribed_answer_raises(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("boom")
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_check_theme_subscription(callback, MagicMock(), dm)
        callback.answer.assert_awaited()


# ---------------------------------------------------------------------------
# on_theme_selected
# ---------------------------------------------------------------------------


class TestThemeSelected:
    async def test_not_subscribed_returns_after_alert(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="left")
        dm = make_dm(middleware={"bot": bot, "user_data_manager": AsyncMock()})
        callback = make_callback()
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_selected(callback, MagicMock(), dm, THEME_ID)
        callback.answer.assert_awaited()
        dm.switch_to.assert_not_awaited()

    async def test_subscribed_saves_theme(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        udm = AsyncMock()
        dm = make_dm(middleware={"bot": bot, "user_data_manager": udm})
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_selected(make_callback(), MagicMock(), dm, THEME_ID)
        udm.set_user_theme.assert_awaited_once_with(123, THEME_ID)

    async def test_register_user_failure_is_ignored(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        udm = AsyncMock()
        udm.register_user.side_effect = RuntimeError("db down")
        dm = make_dm(middleware={"bot": bot, "user_data_manager": udm})
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_selected(make_callback(), MagicMock(), dm, THEME_ID)
        udm.set_user_theme.assert_awaited_once_with(123, THEME_ID)

    async def test_no_user_data_manager(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        dm = make_dm(middleware={"bot": bot})
        callback = make_callback()
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_selected(callback, MagicMock(), dm, THEME_ID)
        callback.answer.assert_awaited()

    async def test_outer_exception_answered(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        udm = AsyncMock()
        udm.set_user_theme.side_effect = RuntimeError("save failed")
        dm = make_dm(middleware={"bot": bot, "user_data_manager": udm})
        callback = make_callback()
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_selected(callback, MagicMock(), dm, THEME_ID)
        dm.switch_to.assert_awaited_with(SettingsMenu.main)

    async def test_outer_exception_answer_also_raises(self):
        bot = AsyncMock()
        bot.get_chat_member.return_value = MagicMock(status="member")
        udm = AsyncMock()
        udm.set_user_theme.side_effect = RuntimeError("save failed")
        dm = make_dm(middleware={"bot": bot, "user_data_manager": udm})
        callback = make_callback()
        callback.answer.side_effect = RuntimeError("answer failed")
        with patch("core.config.SUBSCRIPTION_CHANNEL", "@chan"):
            await sm.on_theme_selected(callback, MagicMock(), dm, THEME_ID)
        dm.switch_to.assert_awaited_with(SettingsMenu.main)


# ---------------------------------------------------------------------------
# get_theme_data
# ---------------------------------------------------------------------------


class TestGetThemeData:
    async def test_with_user_theme(self):
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        udm.get_user_theme.return_value = "dark"
        dm = make_dm(middleware={"user_data_manager": udm})
        data = await sm.get_theme_data(dm)
        assert any(t["is_current"] for t in data["themes"])

    async def test_without_user_data_manager(self):
        dm = make_dm(middleware={})
        data = await sm.get_theme_data(dm)
        assert data["themes"]

    async def test_theme_lookup_error_falls_back(self):
        udm = AsyncMock()
        udm.get_user_language.return_value = "en"
        udm.get_user_theme.side_effect = RuntimeError("boom")
        dm = make_dm(middleware={"user_data_manager": udm})
        data = await sm.get_theme_data(dm)
        assert data["current_theme"]


# ---------------------------------------------------------------------------
# on_news_clicked
# ---------------------------------------------------------------------------


class TestNewsClicked:
    async def test_success(self):
        udm = AsyncMock()
        udm.get_user_language.return_value = "ru"
        dm = make_dm(middleware={"user_data_manager": udm})
        callback = make_callback()
        await sm.on_news_clicked(callback, MagicMock(), dm)
        callback.message.answer.assert_awaited()

    async def test_language_lookup_error_uses_middleware(self):
        udm = AsyncMock()
        udm.get_user_language.side_effect = RuntimeError("boom")
        dm = make_dm(middleware={"user_data_manager": udm, "i18n_lang": "en"})
        callback = make_callback()
        await sm.on_news_clicked(callback, MagicMock(), dm)
        callback.message.answer.assert_awaited()
