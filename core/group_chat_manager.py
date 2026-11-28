"""Менеджер настроек групповых чатов Telegram (режим рассылок расписания для бесед)."""

from __future__ import annotations

import logging
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.db.models import GroupChat

logger = logging.getLogger(__name__)

ALLOWED_GROUP_SETTINGS: frozenset[str] = frozenset(
    {
        "morning_summary",
        "evening_notify",
        "lesson_reminders",
        "schedule_diff_notify",
    }
)


class GroupChatManager:
    """CRUD и выборки для групповых чатов в БД."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def upsert_chat(
        self,
        chat_id: int,
        *,
        title: str | None = None,
        added_by_user_id: int | None = None,
        is_active: bool = True,
    ) -> GroupChat:
        async with self.session_factory() as session:
            chat = await session.get(GroupChat, chat_id)
            if chat:
                if title is not None:
                    chat.title = title[:255]
                if added_by_user_id is not None:
                    chat.added_by_user_id = added_by_user_id
                chat.is_active = is_active
            else:
                chat = GroupChat(
                    chat_id=chat_id,
                    title=title[:255] if title else None,
                    added_by_user_id=added_by_user_id,
                    is_active=is_active,
                )
                session.add(chat)
            await session.commit()
            await session.refresh(chat)
            return cast(GroupChat, chat)

    async def set_target_group(self, chat_id: int, target_group: str, *, title: str | None = None) -> GroupChat:
        norm = target_group.upper()
        async with self.session_factory() as session:
            chat = await session.get(GroupChat, chat_id)
            if chat:
                chat.target_group = norm
                if title is not None:
                    chat.title = title[:255]
                chat.is_active = True
            else:
                chat = GroupChat(
                    chat_id=chat_id,
                    title=title[:255] if title else None,
                    target_group=norm,
                    is_active=True,
                )
                session.add(chat)
            await session.commit()
            await session.refresh(chat)
            return cast(GroupChat, chat)

    async def get_chat(self, chat_id: int) -> GroupChat | None:
        async with self.session_factory() as session:
            return cast(GroupChat | None, await session.get(GroupChat, chat_id))

    async def toggle_setting(self, chat_id: int, field: str) -> bool | None:
        if field not in ALLOWED_GROUP_SETTINGS:
            return None
        async with self.session_factory() as session:
            chat = await session.get(GroupChat, chat_id)
            if not chat:
                return None
            new_val = not bool(getattr(chat, field))
            setattr(chat, field, new_val)
            await session.commit()
            return new_val

    async def set_active(self, chat_id: int, is_active: bool) -> None:
        async with self.session_factory() as session:
            stmt = update(GroupChat).where(GroupChat.chat_id == chat_id).values(is_active=is_active)
            await session.execute(stmt)
            await session.commit()

    async def migrate_chat(self, old_chat_id: int, new_chat_id: int) -> None:
        if old_chat_id == new_chat_id:
            return
        async with self.session_factory() as session:
            old_chat = await session.get(GroupChat, old_chat_id)
            if not old_chat:
                return
            existing_new = await session.get(GroupChat, new_chat_id)
            if existing_new:
                existing_new.title = old_chat.title or existing_new.title
                existing_new.target_group = old_chat.target_group or existing_new.target_group
                existing_new.language = old_chat.language
                existing_new.morning_summary = old_chat.morning_summary
                existing_new.evening_notify = old_chat.evening_notify
                existing_new.lesson_reminders = old_chat.lesson_reminders
                existing_new.schedule_diff_notify = old_chat.schedule_diff_notify
                existing_new.morning_time = old_chat.morning_time
                existing_new.evening_time = old_chat.evening_time
                existing_new.reminder_time_minutes = old_chat.reminder_time_minutes
                existing_new.is_active = old_chat.is_active
                existing_new.added_by_user_id = old_chat.added_by_user_id or existing_new.added_by_user_id
                await session.delete(old_chat)
            else:
                session.add(
                    GroupChat(
                        chat_id=new_chat_id,
                        title=old_chat.title,
                        target_group=old_chat.target_group,
                        language=old_chat.language,
                        morning_summary=old_chat.morning_summary,
                        evening_notify=old_chat.evening_notify,
                        lesson_reminders=old_chat.lesson_reminders,
                        schedule_diff_notify=old_chat.schedule_diff_notify,
                        morning_time=old_chat.morning_time,
                        evening_time=old_chat.evening_time,
                        reminder_time_minutes=old_chat.reminder_time_minutes,
                        is_active=old_chat.is_active,
                        added_by_user_id=old_chat.added_by_user_id,
                    )
                )
                await session.delete(old_chat)
            await session.commit()

    async def get_active_chat_ids(self) -> list[int]:
        async with self.session_factory() as session:
            stmt = select(GroupChat.chat_id).where(GroupChat.is_active.is_(True))
            return [int(x) for x in (await session.execute(stmt)).scalars().all()]

    async def get_chats_for_morning(self, target_time: str = "08:00") -> list[tuple[int, str, str, str]]:
        async with self.session_factory() as session:
            stmt = select(GroupChat.chat_id, GroupChat.target_group, GroupChat.language).where(
                GroupChat.is_active.is_(True),
                GroupChat.morning_summary.is_(True),
                GroupChat.morning_time == target_time,
                GroupChat.target_group.is_not(None),
            )
            rows = (await session.execute(stmt)).all()
            return [(int(cid), str(grp), str(lang or "ru"), "student") for cid, grp, lang in rows if grp]

    async def get_chats_for_evening(self, target_time: str = "20:00") -> list[tuple[int, str, str, str]]:
        async with self.session_factory() as session:
            stmt = select(GroupChat.chat_id, GroupChat.target_group, GroupChat.language).where(
                GroupChat.is_active.is_(True),
                GroupChat.evening_notify.is_(True),
                GroupChat.evening_time == target_time,
                GroupChat.target_group.is_not(None),
            )
            rows = (await session.execute(stmt)).all()
            return [(int(cid), str(grp), str(lang or "ru"), "student") for cid, grp, lang in rows if grp]

    async def get_chats_for_reminders(self) -> list[tuple[int, str, int, str, str]]:
        async with self.session_factory() as session:
            stmt = select(
                GroupChat.chat_id,
                GroupChat.target_group,
                GroupChat.reminder_time_minutes,
                GroupChat.language,
            ).where(
                GroupChat.is_active.is_(True),
                GroupChat.lesson_reminders.is_(True),
                GroupChat.target_group.is_not(None),
            )
            rows = (await session.execute(stmt)).all()
            return [(int(cid), str(grp), int(mins), str(lang or "ru"), "student") for cid, grp, mins, lang in rows if grp]

    async def get_chats_for_diff(self) -> list[tuple[int, str, str]]:
        async with self.session_factory() as session:
            stmt = select(GroupChat.chat_id, GroupChat.target_group, GroupChat.language).where(
                GroupChat.is_active.is_(True),
                GroupChat.schedule_diff_notify.is_(True),
                GroupChat.target_group.is_not(None),
            )
            rows = (await session.execute(stmt)).all()
            return [(int(cid), str(grp), str(lang or "ru")) for cid, grp, lang in rows if grp]
