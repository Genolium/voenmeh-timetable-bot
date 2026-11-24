from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers.group_chat_router import (
    _is_chat_admin,
    on_bot_group_membership_changed,
    on_group_command,
    on_group_config_callback,
    on_group_migrated,
)
from core.db.models import Base
from core.group_chat_manager import GroupChatManager


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_is_chat_admin():
    bot = MagicMock()
    with patch("bot.handlers.group_chat_router.ADMIN_IDS", [999]):
        assert await _is_chat_admin(bot, -100, 999) is True

    with patch("bot.handlers.group_chat_router.ADMIN_IDS", []):
        bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="administrator"))
        assert await _is_chat_admin(bot, -100, 1) is True

        bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="member"))
        assert await _is_chat_admin(bot, -100, 2) is False

        bot.get_chat_member = AsyncMock(side_effect=RuntimeError("api err"))
        assert await _is_chat_admin(bot, -100, 3) is False


@pytest.mark.asyncio
async def test_on_bot_group_membership_changed(session_factory):
    gcm = GroupChatManager(session_factory)

    # Added to group -> welcome succeeds
    event_add = MagicMock()
    event_add.chat = SimpleNamespace(id=-1001, title="О735Б")
    event_add.from_user = SimpleNamespace(id=10)
    event_add.old_chat_member = SimpleNamespace(status="left")
    event_add.new_chat_member = SimpleNamespace(status="member")
    event_add.answer = AsyncMock()
    await on_bot_group_membership_changed(event_add, session_factory)
    event_add.answer.assert_awaited_once()
    assert (await gcm.get_chat(-1001)).is_active is True

    # Added with from_user=None and answer raises -> handled silently
    event_add_err = MagicMock()
    event_add_err.chat = SimpleNamespace(id=-1002, title="О735Б-2")
    event_add_err.from_user = None
    event_add_err.old_chat_member = SimpleNamespace(status="kicked")
    event_add_err.new_chat_member = SimpleNamespace(status="administrator")
    event_add_err.answer = AsyncMock(side_effect=RuntimeError("no rights"))
    await on_bot_group_membership_changed(event_add_err, session_factory)
    assert (await gcm.get_chat(-1002)).is_active is True

    # Removed from group -> deactivated
    event_left = MagicMock()
    event_left.chat = SimpleNamespace(id=-1001, title="О735Б")
    event_left.old_chat_member = SimpleNamespace(status="member")
    event_left.new_chat_member = SimpleNamespace(status="kicked")
    await on_bot_group_membership_changed(event_left, session_factory)
    assert (await gcm.get_chat(-1001)).is_active is False

    # Other transition (member -> administrator) -> no-op
    event_other = MagicMock()
    event_other.chat = SimpleNamespace(id=-1002, title="О735Б-2")
    event_other.old_chat_member = SimpleNamespace(status="member")
    event_other.new_chat_member = SimpleNamespace(status="administrator")
    await on_bot_group_membership_changed(event_other, session_factory)


@pytest.mark.asyncio
async def test_on_group_migrated(session_factory):
    gcm = GroupChatManager(session_factory)
    await gcm.set_target_group(-1001, "О735Б")

    msg_empty = SimpleNamespace(chat=SimpleNamespace(id=-1001), migrate_to_chat_id=None)
    await on_group_migrated(msg_empty, session_factory)
    assert await gcm.get_chat(-1001) is not None

    msg_mig = SimpleNamespace(chat=SimpleNamespace(id=-1001), migrate_to_chat_id=-100999)
    await on_group_migrated(msg_mig, session_factory)
    assert await gcm.get_chat(-100999) is not None


@pytest.mark.asyncio
async def test_on_group_command(session_factory):
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="creator"))
    manager = MagicMock()
    manager._schedules = {"О735Б": {}}
    manager.find_groups_fuzzy = MagicMock(return_value=["О735Б"])

    # No from_user
    msg_no_user = MagicMock(from_user=None)
    await on_group_command(msg_no_user, SimpleNamespace(args=""), bot, session_factory, manager)

    # Not admin
    with patch("bot.handlers.group_chat_router.ADMIN_IDS", []):
        bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="member"))
        msg_non_admin = MagicMock(chat=SimpleNamespace(id=-2001, title="Chat"), from_user=SimpleNamespace(id=5))
        msg_non_admin.reply = AsyncMock()
        await on_group_command(msg_non_admin, SimpleNamespace(args="О735Б"), bot, session_factory, manager)
        assert "только администратор" in msg_non_admin.reply.call_args[0][0]

    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="creator"))

    # No args and not yet bound
    msg_unbound = MagicMock(chat=SimpleNamespace(id=-2001, title="Chat"), from_user=SimpleNamespace(id=5))
    msg_unbound.reply = AsyncMock()
    await on_group_command(msg_unbound, SimpleNamespace(args=""), bot, session_factory, manager)
    assert "не привязана" in msg_unbound.reply.call_args[0][0]

    # Bind unknown group with fuzzy suggestion and without
    msg_bad = MagicMock(chat=SimpleNamespace(id=-2001, title="Chat"), from_user=SimpleNamespace(id=5))
    msg_bad.reply = AsyncMock()
    await on_group_command(msg_bad, SimpleNamespace(args="О735"), bot, session_factory, manager)
    assert "Возможно, вы имели в виду" in msg_bad.reply.call_args[0][0]

    manager.find_groups_fuzzy = MagicMock(return_value=[])
    await on_group_command(msg_bad, SimpleNamespace(args="ZZZZ"), bot, session_factory, manager)
    assert "не найдена" in msg_bad.reply.call_args[0][0]

    # Bind valid group
    msg_ok = MagicMock(chat=SimpleNamespace(id=-2001, title="Chat"), from_user=SimpleNamespace(id=5))
    msg_ok.reply = AsyncMock()
    await on_group_command(msg_ok, SimpleNamespace(args="о735б"), bot, session_factory, manager)
    assert "О735Б" in msg_ok.reply.call_args[0][0]

    # No args when already bound -> shows control panel
    msg_show = MagicMock(chat=SimpleNamespace(id=-2001, title="Chat"), from_user=SimpleNamespace(id=5))
    msg_show.reply = AsyncMock()
    await on_group_command(msg_show, SimpleNamespace(args=""), bot, session_factory, manager)
    assert "Режим рассылок для беседы" in msg_show.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_on_group_config_callback(session_factory):
    bot = MagicMock()
    gcm = GroupChatManager(session_factory)
    await gcm.set_target_group(-3001, "О735Б")

    # Missing user or message
    cb_empty = MagicMock(from_user=None, message=None)
    await on_group_config_callback(cb_empty, bot, session_factory)

    # Not admin
    with patch("bot.handlers.group_chat_router.ADMIN_IDS", []):
        bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="member"))
        cb_non_admin = MagicMock(
            from_user=SimpleNamespace(id=1),
            message=MagicMock(chat=SimpleNamespace(id=-3001)),
            data="grp_cfg:morning_summary",
        )
        cb_non_admin.answer = AsyncMock()
        await on_group_config_callback(cb_non_admin, bot, session_factory)
        cb_non_admin.answer.assert_awaited_once()

    bot.get_chat_member = AsyncMock(return_value=SimpleNamespace(status="administrator"))

    # Toggle valid setting
    msg = MagicMock(chat=SimpleNamespace(id=-3001))
    msg.edit_text = AsyncMock()
    cb_toggle = MagicMock(from_user=SimpleNamespace(id=1), message=msg, data="grp_cfg:morning_summary")
    cb_toggle.answer = AsyncMock()
    await on_group_config_callback(cb_toggle, bot, session_factory)
    msg.edit_text.assert_awaited_once()
    cb_toggle.answer.assert_awaited_with("✅ Настройка обновлена")

    # Disable
    msg.edit_text.reset_mock()
    cb_disable = MagicMock(from_user=SimpleNamespace(id=1), message=msg, data="grp_cfg:disable")
    cb_disable.answer = AsyncMock()
    await on_group_config_callback(cb_disable, bot, session_factory)
    msg.edit_text.assert_awaited_once()
    cb_disable.answer.assert_awaited_with("🔕 Рассылки в чате отключены")

    # Unknown action
    cb_unknown = MagicMock(from_user=SimpleNamespace(id=1), message=msg, data="grp_cfg:unknown")
    cb_unknown.answer = AsyncMock()
    await on_group_config_callback(cb_unknown, bot, session_factory)
    cb_unknown.answer.assert_awaited_once()

    # Non-existent chat for disable & toggle (chat_cfg is None branch)
    msg_missing = MagicMock(chat=SimpleNamespace(id=-9999))
    msg_missing.edit_text = AsyncMock()
    cb_miss_dis = MagicMock(from_user=SimpleNamespace(id=1), message=msg_missing, data="grp_cfg:disable")
    cb_miss_dis.answer = AsyncMock()
    await on_group_config_callback(cb_miss_dis, bot, session_factory)
    msg_missing.edit_text.assert_not_called()

    cb_miss_tog = MagicMock(from_user=SimpleNamespace(id=1), message=msg_missing, data="grp_cfg:morning_summary")
    cb_miss_tog.answer = AsyncMock()
    await on_group_config_callback(cb_miss_tog, bot, session_factory)
    msg_missing.edit_text.assert_not_called()
