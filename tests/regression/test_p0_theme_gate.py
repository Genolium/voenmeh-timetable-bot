from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.dialogs import settings_menu as sm
from bot.dialogs.states import SettingsMenu


@pytest.mark.asyncio
async def test_theme_gate_fails_closed_for_non_subscriber(monkeypatch):
    monkeypatch.setattr("core.config.SUBSCRIPTION_CHANNEL", "@channel")
    callback = AsyncMock()
    callback.from_user.id = 1
    callback.message = AsyncMock()
    manager = MagicMock()
    manager.middleware_data = {"bot": AsyncMock()}
    manager.switch_to = AsyncMock()
    member = MagicMock(status="left")
    manager.middleware_data["bot"].get_chat_member = AsyncMock(return_value=member)

    await sm.on_theme_button_click(callback, MagicMock(), manager)

    manager.switch_to.assert_awaited_with(SettingsMenu.theme_subscription_gate)
    assert SettingsMenu.choose_theme not in [c.args[0] for c in manager.switch_to.await_args_list]


@pytest.mark.asyncio
async def test_theme_gate_allows_subscriber(monkeypatch):
    monkeypatch.setattr("core.config.SUBSCRIPTION_CHANNEL", "@channel")
    callback = AsyncMock()
    callback.from_user.id = 1
    manager = MagicMock()
    manager.middleware_data = {"bot": AsyncMock()}
    manager.switch_to = AsyncMock()
    member = MagicMock(status="member")
    manager.middleware_data["bot"].get_chat_member = AsyncMock(return_value=member)

    await sm.on_theme_button_click(callback, MagicMock(), manager)

    manager.switch_to.assert_awaited_with(SettingsMenu.choose_theme)
