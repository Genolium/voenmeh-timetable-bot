import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
async def test_group_chat_manager_full_lifecycle(session_factory):
    gcm = GroupChatManager(session_factory)

    # Create & update
    chat = await gcm.upsert_chat(-1001, title="ИВТ-21", added_by_user_id=42, is_active=True)
    assert chat.chat_id == -1001
    assert chat.title == "ИВТ-21"

    chat2 = await gcm.upsert_chat(-1001, title="ИВТ-21 new", added_by_user_id=43, is_active=True)
    assert chat2.title == "ИВТ-21 new"
    assert chat2.added_by_user_id == 43
    await gcm.upsert_chat(-1001, title=None, added_by_user_id=None, is_active=True)

    # set_target_group on new and existing (with and without title)
    await gcm.set_target_group(-1001, "о735б", title="О735Б Чат")
    await gcm.set_target_group(-1001, "о735б", title=None)
    await gcm.set_target_group(-1002, "а164б")
    fetched = await gcm.get_chat(-1001)
    assert fetched is not None
    assert fetched.target_group == "О735Б"

    # Toggle settings & invalid field
    assert await gcm.toggle_setting(-1001, "invalid_field") is None
    assert await gcm.toggle_setting(-9999, "morning_summary") is None
    toggled = await gcm.toggle_setting(-1001, "lesson_reminders")
    assert toggled is True

    # Queries for scheduler & broadcast
    assert set(await gcm.get_active_chat_ids()) == {-1001, -1002}
    assert len(await gcm.get_chats_for_morning("08:00")) == 2
    assert len(await gcm.get_chats_for_evening("20:00")) == 2
    assert len(await gcm.get_chats_for_reminders()) == 1
    assert len(await gcm.get_chats_for_diff()) == 2

    # Deactivate
    await gcm.set_active(-1002, False)
    assert await gcm.get_active_chat_ids() == [-1001]

    # Migrate group -> supergroup (new target, existing target, missing source, same id)
    await gcm.migrate_chat(-1001, -1001)
    await gcm.migrate_chat(-9999, -8888)
    await gcm.migrate_chat(-1001, -1001001)
    assert await gcm.get_chat(-1001) is None
    migrated = await gcm.get_chat(-1001001)
    assert migrated is not None and migrated.target_group == "О735Б"

    # Migrate into already-existing chat
    await gcm.set_active(-1002, True)
    await gcm.migrate_chat(-1001001, -1002)
    assert (await gcm.get_chat(-1002)).target_group == "О735Б"
