"""Роутер режима групповых чатов: только привязка учебной группы и рассылки без спама диалогами."""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.config import ADMIN_IDS
from core.group_chat_manager import ALLOWED_GROUP_SETTINGS, GroupChatManager
from core.manager import TimetableManager
from core.text_utils import normalize_group_name

logger = logging.getLogger(__name__)

group_chat_router = Router(name="group_chat_router")

_GROUP_CHAT_TYPES = ("group", "supergroup")


def _build_group_control_markup(chat_cfg: Any) -> InlineKeyboardMarkup:
    def _icon(val: bool) -> str:
        return "✅" if val else "❌"

    rows = [
        [
            InlineKeyboardButton(
                text=f"{_icon(bool(chat_cfg.morning_summary))} Утренняя ({chat_cfg.morning_time})",
                callback_data="grp_cfg:morning_summary",
            ),
            InlineKeyboardButton(
                text=f"{_icon(bool(chat_cfg.evening_notify))} Вечерняя ({chat_cfg.evening_time})",
                callback_data="grp_cfg:evening_notify",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"{_icon(bool(chat_cfg.schedule_diff_notify))} Изменения",
                callback_data="grp_cfg:schedule_diff_notify",
            ),
            InlineKeyboardButton(
                text=f"{_icon(bool(chat_cfg.lesson_reminders))} Напоминания",
                callback_data="grp_cfg:lesson_reminders",
            ),
        ],
        [
            InlineKeyboardButton(text="🔕 Отключить рассылки в чате", callback_data="grp_cfg:disable"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_group_control_text(chat_cfg: Any) -> str:
    group_label = html.escape(str(chat_cfg.target_group or "не выбрана"))
    status = "✅ Активны" if chat_cfg.is_active else "🔕 Отключены"
    return (
        f"👥 <b>Режим рассылок для беседы</b>\n\n"
        f"🎓 Учебная группа: <b>{group_label}</b>\n"
        f"📡 Статус: <b>{status}</b>\n\n"
        f"<i>Сменить группу: <code>/group О735Б</code>. В беседе приходят только рассылки — меню и диалоги отключены.</i>"
    )


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return str(getattr(member, "status", "")) in {"creator", "administrator"}
    except Exception:
        return False


@group_chat_router.my_chat_member(F.chat.type.in_(_GROUP_CHAT_TYPES))
async def on_bot_group_membership_changed(event: ChatMemberUpdated, session_factory: Any) -> None:
    """Приветствие при добавлении в беседу и отключение при удалении."""
    new_status = str(getattr(event.new_chat_member, "status", ""))
    old_status = str(getattr(event.old_chat_member, "status", ""))
    gcm = GroupChatManager(session_factory)

    if new_status in {"member", "administrator"} and old_status in {"left", "kicked"}:
        added_by = event.from_user.id if event.from_user else None
        await gcm.upsert_chat(
            event.chat.id,
            title=getattr(event.chat, "title", None),
            added_by_user_id=added_by,
            is_active=True,
        )
        try:
            await event.answer(
                "👋 <b>Привет! Я бот расписания БГТУ «ВОЕНМЕХ».</b>\n\n"
                "В беседах я работаю <b>только в режиме рассылок</b> (утренние/вечерние сводки и изменения расписания), "
                "не засоряя чат кнопками и меню.\n\n"
                "📌 Чтобы привязать расписание, администратор чата должен написать:\n"
                "<code>/group О735Б</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.debug("Failed to send group welcome in %s: %s", event.chat.id, e)
    elif new_status in {"left", "kicked"}:
        await gcm.set_active(event.chat.id, False)


@group_chat_router.message(F.migrate_to_chat_id)
async def on_group_migrated(message: Message, session_factory: Any) -> None:
    if message.migrate_to_chat_id:
        gcm = GroupChatManager(session_factory)
        await gcm.migrate_chat(message.chat.id, message.migrate_to_chat_id)


@group_chat_router.message(F.chat.type.in_(_GROUP_CHAT_TYPES), Command("group"))
async def on_group_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    session_factory: Any,
    manager: TimetableManager,
) -> None:
    """Привязка учебной группы и пульт управления рассылками чата."""
    if not message.from_user:
        return
    if not await _is_chat_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("⚠️ Настраивать рассылку группы может только администратор чата.")
        return

    gcm = GroupChatManager(session_factory)
    raw_arg = (command.args or "").strip()
    if raw_arg:
        norm = normalize_group_name(raw_arg)
        schedules = getattr(manager, "_schedules", {}) or {}
        if norm and norm in schedules:
            chat_cfg = await gcm.set_target_group(
                message.chat.id,
                norm,
                title=getattr(message.chat, "title", None),
            )
            await message.reply(
                _format_group_control_text(chat_cfg),
                reply_markup=_build_group_control_markup(chat_cfg),
                parse_mode="HTML",
            )
            return

        suggestions = (
            manager.find_groups_fuzzy(raw_arg, limit=3, score_cutoff=45) if hasattr(manager, "find_groups_fuzzy") else []
        )
        escaped_suggestions = ", ".join(html.escape(str(s), quote=False) for s in suggestions)
        hint = f"\nВозможно, вы имели в виду: <b>{escaped_suggestions}</b>" if suggestions else ""
        await message.reply(f"❌ Группа <b>{html.escape(raw_arg)}</b> не найдена.{hint}", parse_mode="HTML")
        return

    existing_cfg = await gcm.get_chat(message.chat.id)
    if not existing_cfg or not existing_cfg.target_group:
        await message.reply(
            "📌 Группа ещё не привязана.\nНапишите: <code>/group О735Б</code>",
            parse_mode="HTML",
        )
        return

    await message.reply(
        _format_group_control_text(existing_cfg),
        reply_markup=_build_group_control_markup(existing_cfg),
        parse_mode="HTML",
    )


@group_chat_router.callback_query(F.message.chat.type.in_(_GROUP_CHAT_TYPES), F.data.startswith("grp_cfg:"))
async def on_group_config_callback(callback: CallbackQuery, bot: Bot, session_factory: Any) -> None:
    if not callback.from_user or not callback.message:
        return
    msg: Any = callback.message
    chat_id = msg.chat.id
    if not await _is_chat_admin(bot, chat_id, callback.from_user.id):
        await callback.answer("⚠️ Только администратор чата может менять настройки.", show_alert=True)
        return

    action = (callback.data or "").split(":", 1)[1]
    gcm = GroupChatManager(session_factory)
    if action == "disable":
        await gcm.set_active(chat_id, False)
        chat_cfg = await gcm.get_chat(chat_id)
        if chat_cfg:
            await msg.edit_text(
                _format_group_control_text(chat_cfg),
                reply_markup=_build_group_control_markup(chat_cfg),
                parse_mode="HTML",
            )
        await callback.answer("🔕 Рассылки в чате отключены")
        return

    if action in ALLOWED_GROUP_SETTINGS:
        await gcm.toggle_setting(chat_id, action)
        chat_cfg = await gcm.get_chat(chat_id)
        if chat_cfg:
            await msg.edit_text(
                _format_group_control_text(chat_cfg),
                reply_markup=_build_group_control_markup(chat_cfg),
                parse_mode="HTML",
            )
        await callback.answer("✅ Настройка обновлена")
        return

    await callback.answer()
