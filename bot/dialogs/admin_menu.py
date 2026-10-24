import asyncio
import html
import logging
import os
import random
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import BufferedInputFile, CallbackQuery, ContentType, FSInputFile, Message
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import MessageInput, TextInput
from aiogram_dialog.widgets.kbd import Back, Button, Column, Row, Select, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

from bot.dialogs.schedule_view import cleanup_old_cache, get_cache_info
from bot.scheduler import evening_broadcast, morning_summary_broadcast
from bot.tasks import copy_message_task, send_message_task
from bot.text_formatters import generate_reminder_text
from core.config import ADMIN_IDS, MOSCOW_TZ
from core.db.backups import BackupError, create_db_backup, maybe_decompress_gzip, restore_db_backup
from core.feedback_manager import FeedbackManager
from core.manager import TimetableManager
from core.metrics import TASKS_SENT_TO_QUEUE
from core.semester_settings import SemesterSettingsManager
from core.subscription_filter import SubscriptionFilterManager
from core.user_data import UserDataManager

from .constants import WidgetIds
from .states import Admin

logger = logging.getLogger(__name__)

# Генерация полного расписания отключена; переменная сохраняется для совместимости тестов UI
active_generations: dict[Any, Any] = {}


def _is_cancel(text: str) -> bool:
    raw = (text or "").strip().lower().lstrip("/")
    return raw in {"отмена", "cancel", "отменить"}


async def on_override_input(message: Message, message_input: MessageInput, manager: DialogManager) -> None:
    """Админская коррекция: group|YYYY-MM-DD|HH:MM-HH:MM|subject|room|type."""
    parts = [part.strip() for part in (message.text or "").split("|")]
    if len(parts) != 6:
        await message.answer(
            "Формат: <code>группа|YYYY-MM-DD|HH:MM-HH:MM|предмет|аудитория|тип</code>",
            parse_mode="HTML",
        )
        return
    group, date_str, time_str, subject, room, lesson_type = parts
    subject = html.escape(subject, quote=False)
    room = html.escape(room, quote=False)
    lesson_type = html.escape(lesson_type, quote=False)
    try:
        from datetime import datetime as _datetime

        target_date = _datetime.strptime(date_str, "%Y-%m-%d").date()
        start_raw, end_raw = [part.strip() for part in time_str.replace("–", "-").split("-", 1)]
        _datetime.strptime(start_raw, "%H:%M")
        _datetime.strptime(end_raw, "%H:%M")
    except ValueError:
        await message.answer("❌ Неверная дата или время.")
        return
    redis_client = manager.middleware_data.get("redis_client")
    if not redis_client:
        await message.answer("❌ Redis недоступен.")
        return
    from core.schedule_overrides import set_group_override

    await set_group_override(
        redis_client,
        group,
        target_date,
        [
            {
                "time": f"{start_raw}-{end_raw}",
                "start_time_raw": start_raw,
                "end_time_raw": end_raw,
                "subject": subject,
                "type": lesson_type,
                "room": room,
                "teachers": "",
                "groups": [group.upper()],
            }
        ],
    )
    await message.answer(
        f"✅ Коррекция сохранена для <b>{html.escape(group.upper(), quote=False)}</b> на {html.escape(date_str, quote=False)}.",
        parse_mode="HTML",
    )
    await manager.switch_to(Admin.menu)


async def on_test_morning(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    await callback.answer("🚀 Запускаю постановку задач на утреннюю рассылку...")
    await morning_summary_broadcast(user_data_manager, timetable_manager)
    await callback.message.answer("✅ Задачи для утренней рассылки поставлены в очередь.")


async def on_test_evening(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    await callback.answer("🚀 Запускаю постановку задач на вечернюю рассылку...")
    await evening_broadcast(user_data_manager, timetable_manager)
    await callback.message.answer("✅ Задачи для вечерней рассылки поставлены в очередь.")


async def on_test_reminders_for_week(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")

    await callback.answer("🚀 Начинаю тест планировщика напоминаний...")

    test_users = await user_data_manager.get_users_for_lesson_reminders()
    if not test_users:
        await bot.send_message(
            admin_id,
            "❌ Не найдено ни одного пользователя с подпиской на напоминания для теста.",
        )
        return

    test_user_id, test_group_name, _, _ = random.choice(test_users)
    await bot.send_message(
        admin_id,
        f"ℹ️ Тестирую логику для случайного пользователя: <code>{test_user_id}</code> (группа <code>{test_group_name}</code>)",
    )

    for i in range(7):
        test_date = date.today() + timedelta(days=i)
        await bot.send_message(
            admin_id,
            f"--- 🗓️ <b>Тест для даты: {test_date.strftime('%A, %d.%m.%Y')}</b> ---",
        )
        schedule_info = await timetable_manager.get_schedule_for_day(test_group_name, target_date=test_date)

        if not (schedule_info and not schedule_info.get("error") and schedule_info.get("lessons")):
            await bot.send_message(admin_id, "<i>Нет пар — нет напоминаний. ✅</i>")
            continue

        try:
            lessons = sorted(schedule_info["lessons"], key=lambda x: x.get("start_time_raw", ""))
            first = lessons[0]
            preview = generate_reminder_text(first, "first", None, 20, "ru")
            await bot.send_message(admin_id, preview or "<i>Не удалось сформировать напоминание</i>")
        except Exception as e:
            await bot.send_message(admin_id, f"⚠️ Ошибка обработки расписания: {e}")

    await bot.send_message(admin_id, "✅ <b>Тестирование планировщика напоминаний завершено.</b>")


async def on_test_alert(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    await callback.answer("🧪 Отправляю тестовый алёрт...")
    text = (
        "ALERTMANAGER: FIRING (1 alert)\n\n"
        "⚠️ ScheduleStale [critical]\n"
        "No update > 1h\n"
        "source=scheduler\n"
        "startsAt=now"
    )
    await bot.send_message(admin_id, text)


async def on_generate_full_schedule(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Массовая генерация отключена."""
    await callback.answer("❌ Массовая генерация отключена")
    bot: Bot = manager.middleware_data.get("bot")
    try:
        await bot.send_message(
            callback.from_user.id,
            "❌ Массовая генерация изображений отключена. Доступна только генерация по запросу пользователя.",
        )
    except Exception:
        pass


async def on_check_graduated_groups(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Запуск проверки выпустившихся групп."""
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")

    # Создаем правильный Redis-клиент
    import os

    from redis.asyncio import Redis

    redis_url = os.getenv("REDIS_URL")
    redis_password = os.getenv("REDIS_PASSWORD")
    redis_client = None

    await callback.answer("🔍 Запускаю проверку выпустившихся групп...")

    try:
        redis_client = Redis.from_url(redis_url, password=redis_password, decode_responses=False)
        await bot.send_message(admin_id, "🔍 Начинаю проверку выпустившихся групп...")
        # Импортируем функцию из scheduler
        from bot.scheduler import handle_graduated_groups

        await handle_graduated_groups(user_data_manager, timetable_manager, redis_client)
        await bot.send_message(admin_id, "✅ Проверка выпустившихся групп завершена!")
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Ошибка при проверке выпустившихся групп: {e}")
        import traceback

        await bot.send_message(admin_id, f"🔍 Детали ошибки:\n<code>{traceback.format_exc()}</code>")
    finally:
        try:
            if redis_client is not None:
                await redis_client.aclose()
        except Exception:
            pass


async def on_semester_settings(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Переход к настройкам семестров."""
    await manager.switch_to(Admin.semester_settings)


BASE_BACKUP_TEXT = (
    "💾 <b>Резервные копии базы данных</b>\n\n"
    "• <b>Создать бэкап</b> — получите SQL-файл с текущим состоянием БД.\n"
    "• <b>Загрузить бэкап</b> — восстановите базу из SQL или .gz файла. "
    "Текущие данные будут перезаписаны."
)


async def get_backup_menu_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    status = dialog_manager.dialog_data.get("backup_status")
    text = BASE_BACKUP_TEXT
    if status:
        text = f"{BASE_BACKUP_TEXT}\n\n{status}"
    return {"backup_status": text}


async def on_backup_create(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    await callback.answer("⏳ Генерирую бэкап...", show_alert=False)
    await manager.switch_to(Admin.backup_create_confirm)

    backup_path: Path | None = None
    try:
        backup_path = await create_db_backup(output_dir=Path("/app/data"))
        document = FSInputFile(backup_path)
        await callback.message.answer_document(
            document,
            caption=f"✅ Резервная копия создана: <code>{backup_path.name}</code>",
        )
        manager.dialog_data["backup_status"] = (
            "✅ Бэкап успешно создан. Файл отправлен в этот чат. " "Сохраните его в безопасном месте."
        )
    except BackupError as exc:
        error_text = f"❌ Не удалось создать резервную копию: {exc}"
        manager.dialog_data["backup_status"] = error_text
        await callback.message.answer(error_text)
    except Exception as exc:  # pragma: no cover - неожиданные ошибки
        error_text = f"❌ Ошибка создания бэкапа: {exc}"
        manager.dialog_data["backup_status"] = error_text
        await callback.message.answer(error_text)
    finally:
        if backup_path and backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass
        await manager.switch_to(Admin.backup_menu)


MAX_RESTORE_BYTES = 20 * 1024 * 1024
RESTORE_CONFIRM_WORD = "RESTORE"


def _is_sql_backup_name(name: str | None) -> bool:
    lower = (name or "").lower()
    return lower.endswith(".sql") or lower.endswith(".sql.gz") or lower.endswith(".gz")


async def on_backup_upload_received(message: Message, widget: MessageInput, manager: DialogManager) -> None:
    document = message.document
    if not document:
        await message.answer("❌ Отправьте документ с расширением .sql или .sql.gz.")
        return

    file_name = document.file_name or ""
    if not _is_sql_backup_name(file_name):
        await message.answer("❌ Нужен файл .sql или .sql.gz.")
        return

    file_size = getattr(document, "file_size", None) or 0
    if file_size > MAX_RESTORE_BYTES:
        await message.answer("❌ Файл слишком большой (лимит 20 МБ).")
        return

    tmp_dir = Path(tempfile.mkdtemp(prefix="voenmeh_restore_"))
    tmp_path = tmp_dir / (file_name or "backup.sql")
    await message.bot.download(document, destination=tmp_path)
    manager.dialog_data["backup_restore_path"] = str(tmp_path)
    manager.dialog_data["backup_upload_status"] = (
        f"Файл <code>{html.escape(file_name, quote=False)}</code> принят.\n"
        f"Для восстановления базы введите <b>{RESTORE_CONFIRM_WORD}</b>."
    )
    await manager.switch_to(Admin.backup_upload_confirm)


async def on_backup_restore_typed(message: Message, widget: MessageInput, manager: DialogManager) -> None:
    if (message.text or "").strip() != RESTORE_CONFIRM_WORD:
        await message.answer(f"Введите {RESTORE_CONFIRM_WORD} для подтверждения или нажмите Отмена.")
        return

    raw_path = manager.dialog_data.get("backup_restore_path")
    if not raw_path:
        await message.answer("❌ Файл восстановления не найден. Загрузите бэкап заново.")
        await manager.switch_to(Admin.backup_menu)
        return

    tmp_path = Path(raw_path)
    try:
        target_path = maybe_decompress_gzip(tmp_path)
        await restore_db_backup(target_path)
        status = (
            "✅ База данных успешно восстановлена из резервной копии.\n"
            "Перезагрузите сервисы, если изменения не применились автоматически."
        )
    except BackupError as exc:
        status = f"❌ Не удалось применить резервную копию: {exc}"
    except Exception as exc:
        status = f"❌ Не удалось применить резервную копию: {exc}"
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
            tmp_path.parent.rmdir()
        except OSError:
            pass
        manager.dialog_data.pop("backup_restore_path", None)

    manager.dialog_data["backup_upload_status"] = status
    manager.dialog_data["backup_status"] = status
    await message.answer(status)
    await manager.switch_to(Admin.backup_menu)


async def get_backup_upload_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    status = dialog_manager.dialog_data.get(
        "backup_upload_status",
        "ℹ️ Ожидаем результат восстановления...",
    )
    return {"backup_upload_status": status}


async def get_sub_filter_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    session_factory = dialog_manager.middleware_data.get("session_factory")
    redis_client = dialog_manager.middleware_data.get("redis_client")
    mgr = SubscriptionFilterManager(session_factory, redis_client)

    is_enabled = await mgr.is_filter_enabled()
    channels = await mgr.get_required_channels()

    status_text = "🟢 <b>Включен</b>" if is_enabled else "🔴 <b>Выключен</b>"
    toggle_btn_text = "🔴 Выключить фильтр" if is_enabled else "🟢 Включить фильтр"

    if channels:
        lines = []
        for i, ch in enumerate(channels, 1):
            title = ch.get("title", "Без названия")
            link = ch.get("invite_link") or ""
            cid = ch.get("channel_id", "")
            lines.append(f"{i}. <a href='{link}'>{title}</a> (<code>{cid}</code>)")
        channels_text = "\n".join(lines)
    else:
        channels_text = "<i>Каналы ещё не добавлены. При пустом списке фильтр не блокирует пользователей.</i>"

    return {
        "is_enabled": is_enabled,
        "status_text": status_text,
        "toggle_btn_text": toggle_btn_text,
        "channels": channels,
        "channels_count": len(channels),
        "channels_text": channels_text,
    }


async def on_toggle_sub_filter(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    session_factory = manager.middleware_data.get("session_factory")
    redis_client = manager.middleware_data.get("redis_client")
    mgr = SubscriptionFilterManager(session_factory, redis_client)

    current_enabled = await mgr.is_filter_enabled()
    new_state = not current_enabled
    await mgr.set_filter_enabled(new_state)

    state_str = "включен" if new_state else "выключен"
    await callback.answer(f"Фильтр обязательной подписки {state_str}!", show_alert=False)


async def on_add_channel_input(message: Message, widget: TextInput, manager: DialogManager, data: str) -> None:
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.sub_filter_menu)
        return

    session_factory = manager.middleware_data.get("session_factory")
    redis_client = manager.middleware_data.get("redis_client")
    bot: Bot = manager.middleware_data.get("bot")
    mgr = SubscriptionFilterManager(session_factory, redis_client)

    clean_target = raw
    invite_link = None

    if raw.startswith("https://t.me/") or raw.startswith("t.me/"):
        path = raw.split("t.me/", 1)[1].strip("/")
        if path.startswith("+") or path.startswith("joinchat/"):
            invite_link = raw if raw.startswith("http") else f"https://{raw}"
            clean_target = path
        else:
            username = path.split("/")[0]
            clean_target = f"@{username}"
            invite_link = f"https://t.me/{username}"

    title = clean_target
    target_chat_id = clean_target
    if clean_target.startswith("+") or "joinchat/" in raw:
        await message.answer(
            "❌ Для проверки подписки нужен @username канала или его числовой ID. "
            "Пригласительные ссылки (t.me/+...) не позволяют проверить участника."
        )
        return
    if (clean_target.startswith("-") and clean_target[1:].isdigit()) or clean_target.isdigit():
        target_chat_id = int(clean_target)

    if bot and not (clean_target.startswith("+") or "joinchat/" in raw):
        try:
            chat = await bot.get_chat(target_chat_id)
            title = chat.title or chat.username or str(clean_target)
            clean_target = str(chat.id)
            if chat.username:
                invite_link = f"https://t.me/{chat.username}"
            elif chat.invite_link:
                invite_link = chat.invite_link
        except Exception as e:
            logger.warning(f"Failed to get_chat for {clean_target}: {e}")
            await message.answer(
                f"⚠️ <i>Не удалось получить информацию о канале от Telegram API.\nКанал добавлен с введённым значением: {clean_target}</i>",
                parse_mode="HTML",
            )

    if not invite_link:
        if clean_target.startswith("@"):
            invite_link = f"https://t.me/{clean_target.lstrip('@')}"
        else:
            invite_link = raw if raw.startswith("http") else f"https://t.me/{clean_target}"

    await mgr.add_required_channel(channel_id=str(clean_target), title=title, invite_link=invite_link)
    await message.answer(f"✅ Канал «{title}» успешно добавлен в обязательную подписку!", parse_mode="HTML")
    await manager.switch_to(Admin.sub_filter_menu)


async def on_delete_sub_filter_channel(callback: CallbackQuery, widget: Any, manager: DialogManager, item_id: str) -> None:
    session_factory = manager.middleware_data.get("session_factory")
    redis_client = manager.middleware_data.get("redis_client")
    mgr = SubscriptionFilterManager(session_factory, redis_client)

    await mgr.remove_required_channel(item_id)
    await callback.answer("🗑 Канал удалён из списка обязательных", show_alert=False)


async def on_edit_fall_semester(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Переход к редактированию даты осеннего семестра."""
    await manager.switch_to(Admin.edit_fall_semester)


async def on_edit_spring_semester(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Переход к редактированию даты весеннего семестра."""
    await manager.switch_to(Admin.edit_spring_semester)


async def on_fall_semester_input(message: Message, widget: TextInput, manager: DialogManager, data: str) -> None:
    """Обработка ввода даты осеннего семестра."""
    try:
        from datetime import datetime

        # Парсим дату в формате DD.MM.YYYY
        date_obj = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()

        # Получаем менеджер настроек
        session_factory = manager.middleware_data.get("session_factory")
        settings_manager = SemesterSettingsManager(session_factory)

        # Получаем текущие настройки
        current_settings = await settings_manager.get_semester_settings()
        spring_start = current_settings[1] if current_settings else date(2025, 2, 9)

        # Обновляем настройки
        success = await settings_manager.update_semester_settings(date_obj, spring_start, message.from_user.id)

        if success:
            await message.answer("✅ Дата начала осеннего семестра успешно обновлена!")
        else:
            await message.answer("❌ Ошибка при обновлении настроек.")

    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 01.09.2024)")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

    await manager.switch_to(Admin.semester_settings)


async def on_spring_semester_input(message: Message, widget: TextInput, manager: DialogManager, data: str) -> None:
    """Обработка ввода даты весеннего семестра."""
    try:
        from datetime import datetime

        # Парсим дату в формате DD.MM.YYYY
        date_obj = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()

        # Получаем менеджер настроек
        session_factory = manager.middleware_data.get("session_factory")
        settings_manager = SemesterSettingsManager(session_factory)

        # Получаем текущие настройки
        current_settings = await settings_manager.get_semester_settings()
        fall_start = current_settings[0] if current_settings else date(2024, 9, 1)

        # Обновляем настройки
        success = await settings_manager.update_semester_settings(fall_start, date_obj, message.from_user.id)

        if success:
            await message.answer("✅ Дата начала весеннего семестра успешно обновлена!")
        else:
            await message.answer("❌ Ошибка при обновлении настроек.")

    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте формат ДД.ММ.ГГГГ (например, 09.02.2025)")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

    await manager.switch_to(Admin.semester_settings)


# --- Сегментированная рассылка с шаблонами ---
async def build_segment_users(
    user_data_manager: UserDataManager,
    group_prefix: str | None,
    days_active: int | None,
) -> Any:
    """Строит список пользователей для сегментированной рассылки с оптимизацией."""
    group_prefix_up = (group_prefix or "").upper().strip()
    all_ids = await user_data_manager.get_all_user_ids()
    selected_ids: list[int] = []
    from datetime import datetime as dt

    threshold = None
    if days_active and days_active > 0:
        threshold = dt.now(MOSCOW_TZ).replace(tzinfo=None) - timedelta(days=days_active)

    processed_count = 0
    for uid in all_ids:
        processed_count += 1
        info = await user_data_manager.get_full_user_info(uid)
        if not info:
            continue
        if group_prefix_up and not (info.group or "").upper().startswith(group_prefix_up):
            continue
        if threshold and (not info.last_active_date or info.last_active_date < threshold):
            continue
        selected_ids.append(uid)

        # Периодически освобождаем event loop для обработки других событий
        if processed_count % 100 == 0:
            await asyncio.sleep(0)

    return selected_ids


def render_template(template_text: str, user_info: Any) -> str:
    placeholders = {
        "user_id": str(user_info.user_id),
        "username": user_info.username or "N/A",
        "group": user_info.group or "N/A",
        "last_active": (user_info.last_active_date.strftime("%d.%m.%Y") if user_info.last_active_date else "N/A"),
    }
    text = template_text
    for key, value in placeholders.items():
        text = text.replace(f"{{{key}}}", value)
    return text


async def on_segment_criteria_input(message: Message, widget: TextInput, manager: DialogManager, data: str) -> None:
    dialog_data = manager.dialog_data
    # ожидаем ввод в формате: PREFIX|DAYS (например: О7|7). Пусто для всех
    raw = (message.text or "").strip()
    if "|" in raw:
        prefix, days_str = raw.split("|", 1)
        days = None
        try:
            days = int(days_str) if days_str.strip() else None
        except ValueError:
            days = None
        dialog_data["segment_group_prefix"] = prefix.strip()
        dialog_data["segment_days_active"] = days
    else:
        dialog_data["segment_group_prefix"] = raw
        dialog_data["segment_days_active"] = None
    await manager.switch_to(Admin.template_input)


async def on_template_input_message(message: Message, message_input: MessageInput, manager: DialogManager) -> None:
    if message.content_type == ContentType.TEXT:
        manager.dialog_data["segment_template"] = message.text or ""
        manager.dialog_data["segment_message_type"] = "text"
        # Очищаем данные медиа если они были
        manager.dialog_data.pop("segment_message_chat_id", None)
        manager.dialog_data.pop("segment_message_id", None)
    else:
        # Для медиа сообщений сохраняем информацию для копирования
        manager.dialog_data["segment_message_type"] = "media"
        manager.dialog_data["segment_message_chat_id"] = message.chat.id
        manager.dialog_data["segment_message_id"] = message.message_id
        # Очищаем текстовый шаблон если он был
        manager.dialog_data.pop("segment_template", None)
    await manager.switch_to(Admin.preview)


async def get_preview_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    prefix = dialog_manager.dialog_data.get("segment_group_prefix")
    days_active = dialog_manager.dialog_data.get("segment_days_active")
    template = dialog_manager.dialog_data.get("segment_template", "")
    message_type = dialog_manager.dialog_data.get("segment_message_type", "text")
    users = await build_segment_users(user_data_manager, prefix, days_active)

    preview_text = ""
    if users and message_type == "text":
        info = await user_data_manager.get_full_user_info(users[0])
        preview_text = render_template(template, info)
    elif message_type == "media":
        preview_text = "📎 Медиа сообщение будет скопировано всем пользователям сегмента"

    dialog_manager.dialog_data["segment_selected_ids"] = users
    return {
        "preview_text": preview_text or "(не удалось сформировать превью)",
        "selected_count": len(users),
    }


async def on_confirm_segment_send(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    udm: UserDataManager = manager.middleware_data.get("user_data_manager")
    message_type = manager.dialog_data.get("segment_message_type", "text")
    user_ids = manager.dialog_data.get("segment_selected_ids", [])
    await callback.answer("🚀 Рассылка по сегменту поставлена в очередь...")

    # Запускаем постановку задач в фоне, чтобы не блокировать event loop
    async def _process_segment_broadcast():
        count = 0

        if message_type == "text":
            template = manager.dialog_data.get("segment_template", "")
            for uid in user_ids:
                info = await udm.get_full_user_info(uid)
                if not info:
                    continue
                text = render_template(template, info)
                send_message_task.send(uid, text)
                TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
                count += 1

                # Периодически уступаем управление event loop
                if count % 50 == 0:
                    await asyncio.sleep(0)
        else:  # media
            from_chat_id = manager.dialog_data.get("segment_message_chat_id")
            message_id = manager.dialog_data.get("segment_message_id")
            if from_chat_id and message_id:
                for uid in user_ids:
                    copy_message_task.send(uid, from_chat_id, message_id)
                    TASKS_SENT_TO_QUEUE.labels(actor_name="copy_message_task").inc()
                    count += 1

                    # Периодически уступаем управление event loop
                    if count % 50 == 0:
                        await asyncio.sleep(0)

        message_type_text = "текстовых" if message_type == "text" else "медиа"
        await bot.send_message(
            admin_id,
            f"✅ Отправка {message_type_text} сообщений по сегменту запущена. Поставлено задач: {count}",
        )

    # Запускаем в фоне
    asyncio.create_task(_process_segment_broadcast())

    # Сразу возвращаемся в меню
    await manager.switch_to(Admin.menu)


async def on_period_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str) -> None:
    """Обновляет период в `dialog_data` при нажатии на кнопку."""
    manager.dialog_data["stats_period"] = int(item_id)


async def get_stats_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    """Собирает и форматирует все данные для дашборда статистики."""
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    period = dialog_manager.dialog_data.get("stats_period", 7)

    (
        total_users,
        dau,
        wau,
        mau,
        subscribed_total,
        unsubscribed_total,
        subs_breakdown,
        top_groups,
        group_dist,
    ) = await asyncio.gather(
        user_data_manager.get_total_users_count(),
        user_data_manager.get_active_users_by_period(days=1),
        user_data_manager.get_active_users_by_period(days=7),
        user_data_manager.get_active_users_by_period(days=30),
        user_data_manager.get_subscribed_users_count(),
        user_data_manager.get_unsubscribed_count(),
        user_data_manager.get_subscription_breakdown(),
        user_data_manager.get_top_groups(limit=5),
        user_data_manager.get_group_distribution(),
    )
    new_users = await user_data_manager.get_new_users_count(days=period)
    active_users = await user_data_manager.get_active_users_by_period(days=period)

    blocked_count = 0
    if hasattr(user_data_manager, "get_blocked_users_count"):
        blocked_count = await user_data_manager.get_blocked_users_count(days=period)

    users_without_group = 0
    if hasattr(user_data_manager, "get_users_without_group_count"):
        users_without_group = await user_data_manager.get_users_without_group_count()

    total_users_val = total_users if isinstance(total_users, (int, float)) else 0
    new_users_val = new_users if isinstance(new_users, (int, float)) else 0
    blocked_count_val = blocked_count if isinstance(blocked_count, (int, float)) else 0
    users_without_group_val = users_without_group if isinstance(users_without_group, (int, float)) else 0

    users_with_group = max(0, total_users_val - users_without_group_val)
    conv_pct = round((users_with_group / total_users_val * 100), 1) if total_users_val > 0 else 0.0
    net_growth = new_users_val - blocked_count_val
    net_growth_str = f"+{net_growth}" if net_growth >= 0 else str(net_growth)

    # Топ функций
    top_features = []
    try:
        from core.analytics import get_top_features

        redis_client = await user_data_manager._get_redis_client()
        top_features = await get_top_features(redis_client, days=period, limit=4)
    except Exception:
        pass

    if top_features:
        top_features_text = "\n".join([f"  - {name}: <b>{count}</b>" for _, name, count in top_features])
    else:
        top_features_text = "  - Нет данных"

    period_map = {1: "День", 7: "Неделя", 30: "Месяц"}

    top_groups_list = top_groups if isinstance(top_groups, list) else []
    top_groups_text = "\n".join([f"  - {g or 'Не указана'}: {c}" for g, c in top_groups_list])

    subs_breakdown_dict = subs_breakdown if isinstance(subs_breakdown, dict) else {}
    subs_breakdown_text = (
        f"  - Вечер: {subs_breakdown_dict.get('evening', 0)}\n"
        f"  - Утро: {subs_breakdown_dict.get('morning', 0)}\n"
        f"  - Пары: {subs_breakdown_dict.get('reminders', 0)}"
    )

    group_dist_dict = group_dist if isinstance(group_dist, dict) else {}
    group_dist_text = "\n".join([f"  - {category}: {count}" for category, count in group_dist_dict.items()])

    stats_text = (
        f"📊 <b>Статистика бота</b> (Период: <b>{period_map.get(period, '')}</b>)\n\n"
        f"👤 <b>Общая картина</b>\n"
        f"  - Всего пользователей: <b>{total_users}</b>\n"
        f"  - Новых за период: <b>{new_users}</b>\n\n"
        f"🏃‍♂️ <b>Активность</b>\n"
        f"  - Активных за период: <b>{active_users}</b>\n"
        f"  - DAU / WAU / MAU: <b>{dau} / {wau} / {mau}</b>\n\n"
        f"🚪 <b>Отток и воронка</b>\n"
        f"  - Заблокировали за период: <b>{blocked_count}</b>\n"
        f"  - Чистый прирост: <b>{net_growth_str}</b>\n"
        f"  - Без группы: <b>{users_without_group}</b> (онбординг: <b>{conv_pct}%</b>)\n\n"
        f"🔔 <b>Вовлеченность</b>\n"
        f"  - С подписками: <b>{subscribed_total}</b>\n"
        f"  - Отписались от всего: <b>{unsubscribed_total}</b>\n"
        f"  <u>Разбивка по подпискам:</u>\n{subs_breakdown_text}\n\n"
        f"🏆 <b>Топ функций</b>\n"
        f"{top_features_text}\n\n"
        f"🎓 <b>Группы</b>\n"
        f"  <u>Топ-5 групп:</u>\n{top_groups_text}\n"
        f"  <u>Распределение по размеру:</u>\n{group_dist_text}"
    )

    return {
        "stats_text": stats_text,
        "period": period,
        "periods": [("День", 1), ("Неделя", 7), ("Месяц", 30)],
    }


async def on_send_stats_chart(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Генерирует и отправляет график динамики за выбранный период."""
    await callback.answer("⏳ Генерирую график...", show_alert=False)
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    period = manager.dialog_data.get("stats_period", 7)
    period_title = {1: "1 день", 7: "7 дней", 30: "30 дней"}.get(period, f"{period} дн.")

    dates: list[Any] = []
    new_users: list[int] = []
    active_users: list[int] = []
    if user_data_manager and hasattr(user_data_manager, "get_activity_history"):
        try:
            dates, new_users, active_users = await user_data_manager.get_activity_history(days=period)
        except Exception as e:
            logger.error(f"Error fetching activity history: {e}")

    try:
        from core.analytics import generate_dynamics_chart

        chart_buf = generate_dynamics_chart(
            dates=dates,
            new_users=new_users,
            active_users=active_users,
            period_title=period_title,
        )
        photo = BufferedInputFile(chart_buf.getvalue(), filename=f"stats_chart_{period}d.png")
        await callback.message.answer_photo(
            photo=photo,
            caption=f"📈 <b>График динамики активности за {period_title}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error generating stats chart: {e}")
        await callback.message.answer("❌ Не удалось сгенерировать график. Проверьте логи.")


async def on_broadcast_received(*args, **kwargs):
    # Support both aiogram-dialog callback signature (message, message_input, manager)
    # and test signature (message, manager)
    if len(args) == 2:
        message, manager = args
    elif len(args) == 3:
        message, _message_input, manager = args
    else:
        # Fallback for unexpected signature
        message = kwargs.get("message")
        manager = kwargs.get("manager")

    bot: Bot = manager.middleware_data.get("bot")
    admin_id = message.from_user.id
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")

    if message.content_type == ContentType.TEXT:
        template = message.text
        # Получаем всех пользователей
        all_users = await user_data_manager.get_all_user_ids()
        await message.reply("🚀 Рассылка поставлена в очередь...")

        # Запускаем постановку задач в фоне, чтобы не блокировать event loop
        async def _process_broadcast():
            sent_count = 0
            for user_id in all_users:
                user_info = await user_data_manager.get_full_user_info(user_id)
                if not user_info:
                    continue
                text = render_template(template, user_info)
                send_message_task.send(user_id, text)
                TASKS_SENT_TO_QUEUE.labels(actor_name="send_message_task").inc()
                sent_count += 1

                # Периодически уступаем управление event loop
                if sent_count % 50 == 0:
                    await asyncio.sleep(0)

            await bot.send_message(admin_id, f"✅ Рассылка завершена. Поставлено задач: {sent_count}")

        # Запускаем в фоне
        asyncio.create_task(_process_broadcast())

        # Сразу возвращаемся в меню
        await manager.switch_to(Admin.menu)
    else:
        # Обработка медиа: Ставим задачи на копирование сообщения всем пользователям
        all_users = await user_data_manager.get_all_user_ids()
        await message.reply(f"🚀 Начинаю постановку задач на медиа-рассылку для {len(all_users)} пользователей...")

        # Запускаем постановку задач в фоне, чтобы не блокировать event loop
        async def _process_media_broadcast():
            try:
                count = 0
                for user_id in all_users:
                    copy_message_task.send(user_id, message.chat.id, message.message_id)
                    TASKS_SENT_TO_QUEUE.labels(actor_name="copy_message_task").inc()
                    count += 1

                    # Периодически уступаем управление event loop
                    if count % 50 == 0:
                        await asyncio.sleep(0)

                await bot.send_message(admin_id, f"✅ Медиа-рассылка завершена! Поставлено задач: {count}")
            except Exception as e:
                await bot.send_message(admin_id, f"❌ Ошибка при медиа-рассылке: {e}")

        # Запускаем в фоне
        asyncio.create_task(_process_media_broadcast())

        # Сразу возвращаемся в меню
        await manager.switch_to(Admin.menu)


async def on_user_id_input(message: Message, widget: TextInput, manager: DialogManager, data: str) -> None:
    try:
        user_id = int(message.text)
    except (ValueError, TypeError):
        await message.answer("❌ ID пользователя должно быть числом.")
        return

    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_info = await user_data_manager.get_full_user_info(user_id)

    if not user_info:
        await message.answer(f"❌ Пользователь с ID <code>{user_id}</code> не найден в базе.")
        return

    manager.dialog_data["found_user_info"] = {
        "user_id": user_info.user_id,
        "username": user_info.username,
        "group": user_info.group,
        "reg_date": user_info.registration_date.strftime("%Y-%m-%d %H:%M"),
        "last_active": user_info.last_active_date.strftime("%Y-%m-%d %H:%M"),
    }
    await manager.switch_to(Admin.user_manage)


async def on_new_group_input(message: Message, widget: TextInput, manager: DialogManager, data: str) -> None:
    new_group = message.text.upper()
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")

    if new_group not in timetable_manager._schedules:
        await message.answer(f"❌ Группа <b>{new_group}</b> не найдена в расписании.")
        return

    user_id = manager.dialog_data["found_user_info"]["user_id"]
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")

    await user_data_manager.set_user_group(user_id, new_group)
    await message.answer(f"✅ Группа для пользователя <code>{user_id}</code> успешно изменена на <b>{new_group}</b>.")

    manager.dialog_data["found_user_info"]["group"] = new_group
    await manager.switch_to(Admin.user_manage)


async def get_user_manage_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    user_info = dialog_manager.dialog_data.get("found_user_info", {})
    if not user_info:
        return {}

    return {
        "user_info_text": (
            f"👤 <b>Пользователь:</b> <code>{user_info.get('user_id')}</code> (@{user_info.get('username')})\n"
            f"🎓 <b>Группа:</b> {user_info.get('group') or 'Не установлена'}\n"
            f"📅 <b>Регистрация:</b> {user_info.get('reg_date')}\n"
            f"🏃‍♂️ <b>Последняя активность:</b> {user_info.get('last_active')}"
        )
    }


async def get_semester_settings_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    """Получает данные для окна настроек семестров."""
    session_factory = dialog_manager.middleware_data.get("session_factory")
    if not session_factory:
        return {"semester_settings_text": "❌ Ошибка: не удалось подключиться к базе данных."}

    settings_manager = SemesterSettingsManager(session_factory)
    settings_text = await settings_manager.get_formatted_settings()

    return {"semester_settings_text": settings_text}


async def on_force_update_schedule(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Принудительное обновление расписания."""
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id
    user_data_manager = manager.middleware_data.get("user_data_manager")
    redis_client = manager.middleware_data.get("redis_client")

    from bot.scheduler import monitor_schedule_changes
    from core import parser
    from core.config import REDIS_SCHEDULE_HASH_KEY

    await callback.answer("🚀 Запущено принудительное обновление...", show_alert=False)

    async def _process_force_update():
        try:
            await bot.send_message(admin_id, "🔄 <b>Принудительное обновление</b>\n\n1. Сбрасываю хеш и кэш заголовков...")
            # Сбрасываем хеш в Redis, чтобы бот "увидел" изменения
            await redis_client.delete(REDIS_SCHEDULE_HASH_KEY)
            # Сбрасываем заголовки ETag/Last-Modified и кэш voenmeh.su, чтобы сервер не вернул 304
            parser._LAST_ETAG = None
            parser._LAST_MODIFIED = None
            parser._LAST_VOENMEH_SU_UPDATED_AT = None

            await bot.send_message(admin_id, "2. Запрашиваю и обрабатываю новое расписание (это может занять время)...")
            await monitor_schedule_changes(user_data_manager, redis_client, bot)

            await bot.send_message(
                admin_id,
                "✅ <b>Обновление завершено!</b>\nЕсли были изменения, уведомления отправлены админам.",
            )
        except Exception as e:
            await bot.send_message(admin_id, f"❌ Критическая ошибка при обновлении: {e}")

    asyncio.create_task(_process_force_update())


async def on_clear_cache(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    bot: Bot = manager.middleware_data.get("bot")
    admin_id = callback.from_user.id

    await callback.answer("🧹 Очищаю кэш картинок...")

    # Получаем информацию о кэше до очистки
    cache_info_before = await get_cache_info()

    # Очищаем кэш
    await cleanup_old_cache()

    # Получаем информацию о кэше после очистки
    cache_info_after = await get_cache_info()

    if "error" in cache_info_before or "error" in cache_info_after:
        await bot.send_message(admin_id, "❌ Ошибка при работе с кэшем")
        return

    freed_space = cache_info_before["total_size_mb"] - cache_info_after["total_size_mb"]
    freed_files = cache_info_before["total_files"] - cache_info_after["total_files"]

    # Формируем список файлов для отображения (ограничиваем до 5 файлов)
    files_before = cache_info_before.get("files", [])
    files_after = cache_info_after.get("files", [])

    # Показываем только первые 5 файлов
    files_to_show = files_before[:5]
    files_text_before = "\n".join([f"   • {f}" for f in files_to_show]) if files_to_show else "   • Нет файлов"
    if len(files_before) > 5:
        files_text_before += f"\n   ... и еще {len(files_before) - 5} файлов"

    files_text_after = "\n".join([f"   • {f}" for f in files_after]) if files_after else "   • Нет файлов"

    # Добавляем информацию о Redis кэше
    redis_before = cache_info_before.get("redis_keys", 0)
    redis_after = cache_info_after.get("redis_keys", 0)
    redis_freed = redis_before - redis_after

    message = (
        f"✅ <b>Кэш очищен!</b>\n\n"
        f"📊 <b>До очистки:</b>\n"
        f"   • Файлов: {cache_info_before['total_files']}\n"
        f"   • Размер файлов: {cache_info_before['total_size_mb']} MB\n"
        f"   • Redis ключей: {redis_before}\n"
        f"   • Файлы:\n{files_text_before}\n\n"
        f"🧹 <b>После очистки:</b>\n"
        f"   • Файлов: {cache_info_after['total_files']}\n"
        f"   • Размер файлов: {cache_info_after['total_size_mb']} MB\n"
        f"   • Redis ключей: {redis_after}\n\n"
        f"💾 <b>Освобождено:</b>\n"
        f"   • Файлов: {freed_files}\n"
        f"   • Места: {freed_space} MB\n"
        f"   • Redis ключей: {redis_freed}"
    )

    await bot.send_message(admin_id, message, parse_mode="HTML")


async def on_cancel_generation(callback: CallbackQuery) -> None:
    """Отменяет генерацию изображений."""
    admin_id = callback.from_user.id

    if admin_id in active_generations:
        active_generations[admin_id]["cancelled"] = True
        await callback.answer("⏹️ Отмена генерации...")

        # Обновляем сообщение
        try:
            status_msg_id = active_generations[admin_id]["status_msg_id"]
            await callback.message.edit_text(
                "⏹️ <b>Генерация отменена</b>\n\n" "Процесс остановлен пользователем.",
                parse_mode="HTML",
            )
        except:
            pass

        # Удаляем из активных генераций
        del active_generations[admin_id]
    else:
        await callback.answer("❌ Нет активной генерации для отмены")


# --- Функция отправки сообщения пользователю ---
async def on_send_message_to_user(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Переход к вводу сообщения для отправки пользователю."""
    await manager.switch_to(Admin.send_message_text)


async def on_message_to_user_input(message: Message, message_input: MessageInput, manager: DialogManager) -> None:
    """Обработчик ввода сообщения для отправки пользователю."""
    bot: Bot = manager.middleware_data.get("bot")
    user_id = manager.dialog_data.get("found_user_info", {}).get("user_id")

    if not user_id:
        await message.answer("❌ Ошибка: пользователь не найден")
        await manager.switch_to(Admin.enter_user_id)
        return

    try:
        # Отправляем сообщение пользователю
        if message.content_type == ContentType.TEXT:
            await bot.send_message(user_id, message.text)
        else:
            # Копируем медиа-сообщение
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )

        await message.answer(f"✅ Сообщение отправлено пользователю {user_id}")
        await manager.switch_to(Admin.user_manage)
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке: {str(e)}")


async def on_admin_dialog_start(start_data: object, manager: DialogManager) -> None:
    user = getattr(manager.event, "from_user", None)
    if not user or user.id not in ADMIN_IDS:
        await manager.done()


admin_dialog = Dialog(
    # Главный экран: разделы
    Window(
        Const("👑 <b>Админ-панель</b>\n\nВыберите раздел:"),
        Row(
            SwitchTo(
                Const("📬 Рассылки"),
                id="section_broadcasts",
                state=Admin.broadcast_menu,
            ),
            SwitchTo(
                Const("🧪 Диагностика"),
                id="section_diagnostics",
                state=Admin.diagnostics_menu,
            ),
        ),
        Row(
            SwitchTo(Const("🧹 Кэш и генерация"), id="section_cache", state=Admin.cache_menu),
            SwitchTo(
                Const("⚙️ Настройки"),
                id="section_settings",
                state=Admin.semester_settings,
            ),
        ),
        Row(
            SwitchTo(Const("👤 Пользователи"), id="section_users", state=Admin.enter_user_id),
            SwitchTo(Const("✏️ Коррекция расписания"), id="section_override", state=Admin.schedule_override),
        ),
        Row(
            SwitchTo(Const("📊 Статистика"), id=WidgetIds.STATS, state=Admin.stats),
            SwitchTo(Const("💾 Бэкапы"), id="section_backups", state=Admin.backup_menu),
        ),
        Row(
            SwitchTo(Const("📢 Обязательная подписка"), id="section_sub_filter", state=Admin.sub_filter_menu),
        ),
        state=Admin.menu,
    ),
    # Раздел: Рассылки
    Window(
        Const("📬 Раздел ‘Рассылки’"),
        SwitchTo(Const("📣 Массовая рассылка"), id="go_broadcast", state=Admin.broadcast),
        SwitchTo(Const("🎯 Сегментированная"), id="go_segment", state=Admin.segment_menu),
        SwitchTo(Const("◀️ Назад к разделам"), id="back_sections_broadcasts", state=Admin.menu),
        state=Admin.broadcast_menu,
    ),
    # Раздел: Диагностика
    Window(
        Const("🧪 Раздел ‘Диагностика’"),
        Button(
            Const("⚙️ Тест утренней"),
            id=WidgetIds.TEST_MORNING,
            on_click=on_test_morning,
        ),
        Button(
            Const("⚙️ Тест вечерней"),
            id=WidgetIds.TEST_EVENING,
            on_click=on_test_evening,
        ),
        Button(
            Const("🧪 Тест напоминаний"),
            id=WidgetIds.TEST_REMINDERS,
            on_click=on_test_reminders_for_week,
        ),
        Button(Const("🧪 Тест алёрта"), id="test_alert2", on_click=on_test_alert),
        SwitchTo(Const("◀️ Назад к разделам"), id="back_sections_diag", state=Admin.menu),
        state=Admin.diagnostics_menu,
    ),
    # Раздел: Кэш и генерация
    Window(
        Const("🧹 Раздел 'Кэш и генерация'"),
        Button(Const("🗑️ Очистить кэш картинок"), id="clear_cache2", on_click=on_clear_cache),
        Button(
            Const("🔄 Обновить расписание"),
            id="force_update_sched",
            on_click=on_force_update_schedule,
        ),
        Button(
            Const("👥 Проверить выпустившиеся группы"),
            id="check_graduated2",
            on_click=on_check_graduated_groups,
        ),
        SwitchTo(Const("◀️ Назад к разделам"), id="back_sections_cache", state=Admin.menu),
        state=Admin.cache_menu,
    ),
    Window(
        Format("{stats_text}"),
        Row(
            Select(
                Jinja("{% if item[1] == period %}" "🔘 {{ item[0] }}" "{% else %}" "⚪️ {{ item[0] }}" "{% endif %}"),
                id="select_stats_period",
                item_id_getter=lambda item: str(item[1]),
                items="periods",
                on_click=on_period_selected,
            )
        ),
        Button(Const("📈 График динамики"), id="stats_send_chart", on_click=on_send_stats_chart),
        SwitchTo(Const("◀️ В админ-панель"), id="stats_back", state=Admin.menu),
        getter=get_stats_data,
        state=Admin.stats,
        parse_mode="HTML",
    ),
    Window(
        Format("{backup_status}"),
        Button(Const("📥 Создать бэкап"), id="backup_create_btn", on_click=on_backup_create),
        SwitchTo(Const("📤 Загрузить бэкап"), id="backup_upload_btn", state=Admin.backup_upload_wait),
        SwitchTo(Const("◀️ В админ-панель"), id="back_from_backup_menu", state=Admin.menu),
        getter=get_backup_menu_data,
        state=Admin.backup_menu,
        parse_mode="HTML",
    ),
    Window(
        Const("⏳ Получаю файл бэкапа..."),
        SwitchTo(Const("◀️ Отмена"), id="cancel_backup_create", state=Admin.backup_menu),
        state=Admin.backup_create_confirm,
    ),
    Window(
        Const("📤 Отправьте SQL-файл резервной копии базы данных (поддерживается .sql и .gz)."),
        MessageInput(on_backup_upload_received, content_types=[ContentType.DOCUMENT]),
        SwitchTo(Const("◀️ Отмена"), id="cancel_backup_upload", state=Admin.backup_menu),
        state=Admin.backup_upload_wait,
    ),
    Window(
        Format("{backup_upload_status}"),
        MessageInput(on_backup_restore_typed, content_types=[ContentType.TEXT]),
        SwitchTo(Const("◀️ Отмена"), id="back_from_backup_status", state=Admin.backup_menu),
        getter=get_backup_upload_data,
        state=Admin.backup_upload_confirm,
        parse_mode="HTML",
    ),
    Window(
        Const("Введите критерии сегментации в формате PREFIX|DAYS (например: О7|7). Пусто — все."),
        TextInput(id="segment_input", on_success=on_segment_criteria_input),
        SwitchTo(Const("◀️ В админ-панель"), id="segment_back", state=Admin.menu),
        state=Admin.segment_menu,
    ),
    Window(
        Const("Введите шаблон сообщения. Доступные плейсхолдеры: {user_id}, {username}, {group}"),
        MessageInput(on_template_input_message, content_types=[ContentType.TEXT]),
        SwitchTo(Const("◀️ Назад"), id="template_back", state=Admin.segment_menu),
        state=Admin.template_input,
    ),
    Window(
        Format("Предпросмотр (1-й получатель):\n\n{preview_text}\n\nВсего получателей: {selected_count}"),
        Button(
            Const("🚀 Отправить"),
            id="confirm_segment_send",
            on_click=on_confirm_segment_send,
        ),
        SwitchTo(Const("◀️ Назад"), id="preview_back", state=Admin.template_input),
        getter=get_preview_data,
        state=Admin.preview,
        parse_mode="HTML",
    ),
    Window(
        Const(
            "Введите сообщение для рассылки. Можно отправить текст, фото, видео или стикер. Для текста поддерживаются плейсхолдеры: {user_id}, {username}, {group}"
        ),
        MessageInput(on_broadcast_received, content_types=[ContentType.ANY]),
        SwitchTo(Const("◀️ В админ-панель"), id="broadcast_back", state=Admin.menu),
        state=Admin.broadcast,
    ),
    Window(
        Const("✏️ Коррекция расписания\n\n" "Введите: группа|YYYY-MM-DD|HH:MM-HH:MM|предмет|аудитория|тип"),
        MessageInput(on_override_input, content_types=[ContentType.TEXT]),
        SwitchTo(Const("◀️ В админ-панель"), id="override_back", state=Admin.menu),
        state=Admin.schedule_override,
    ),
    Window(
        Const("Введите ID пользователя для управления:"),
        TextInput(id="input_user_id", on_success=on_user_id_input),
        SwitchTo(Const("◀️ В админ-панель"), id="user_id_back", state=Admin.menu),
        state=Admin.enter_user_id,
    ),
    Window(
        Format("{user_info_text}"),
        Row(
            SwitchTo(
                Const("🔄 Сменить группу"),
                id="change_group",
                state=Admin.change_group_confirm,
            ),
            Button(
                Const("✉️ Отправить сообщение"),
                id="send_msg",
                on_click=on_send_message_to_user,
            ),
        ),
        SwitchTo(Const("◀️ Новый поиск"), id="back_to_user_search", state=Admin.enter_user_id),
        state=Admin.user_manage,
        getter=get_user_manage_data,
        parse_mode="HTML",
    ),
    Window(
        Const("✉️ Введите сообщение для отправки пользователю (текст или медиа):"),
        MessageInput(on_message_to_user_input, content_types=[ContentType.ANY]),
        SwitchTo(Const("◀️ Назад"), id="send_msg_back", state=Admin.user_manage),
        state=Admin.send_message_text,
    ),
    Window(
        Const("Введите новый номер группы для пользователя:"),
        TextInput(id="input_new_group", on_success=on_new_group_input),
        SwitchTo(Const("◀️ Назад"), id="change_group_back", state=Admin.user_manage),
        state=Admin.change_group_confirm,
    ),
    Window(
        Format("{semester_settings_text}"),
        Button(
            Const("🍂 Изменить осенний семестр"),
            id="edit_fall_semester",
            on_click=on_edit_fall_semester,
        ),
        Button(
            Const("🌸 Изменить весенний семестр"),
            id="edit_spring_semester",
            on_click=on_edit_spring_semester,
        ),
        SwitchTo(Const("◀️ В админ-панель"), id="semester_back", state=Admin.menu),
        getter=get_semester_settings_data,
        state=Admin.semester_settings,
        parse_mode="HTML",
    ),
    Window(
        Const("Введите дату начала осеннего семестра в формате ДД.ММ.ГГГГ (например, 01.09.2024):"),
        TextInput(id="fall_semester_input", on_success=on_fall_semester_input),
        SwitchTo(Const("◀️ Назад"), id="fall_semester_back", state=Admin.semester_settings),
        state=Admin.edit_fall_semester,
    ),
    Window(
        Const("Введите дату начала весеннего семестра в формате ДД.ММ.ГГГГ (например, 09.02.2025):"),
        TextInput(id="spring_semester_input", on_success=on_spring_semester_input),
        SwitchTo(Const("◀️ Назад"), id="spring_semester_back", state=Admin.semester_settings),
        state=Admin.edit_spring_semester,
    ),
    # Меню обязательной подписки на каналы
    Window(
        Format(
            "<b>📢 Фильтр обязательной подписки на каналы</b>\n\n"
            "Статус: {status_text}\n\n"
            "📋 <b>Обязательные каналы ({channels_count}):</b>\n"
            "{channels_text}\n\n"
            "<i>Для удаления канала нажмите на кнопку с его названием ниже:</i>"
        ),
        Button(
            Format("{toggle_btn_text}"),
            id="toggle_sub_filter_btn",
            on_click=on_toggle_sub_filter,
        ),
        SwitchTo(
            Const("➕ Добавить канал"),
            id="go_add_channel",
            state=Admin.sub_filter_add_channel,
        ),
        Column(
            Select(
                Format("❌ {item[title]}"),
                id="del_sub_channel",
                item_id_getter=lambda x: str(x["channel_id"]),
                items="channels",
                on_click=on_delete_sub_filter_channel,
            ),
        ),
        SwitchTo(Const("◀️ В админ-панель"), id="back_from_sub_filter", state=Admin.menu),
        state=Admin.sub_filter_menu,
        getter=get_sub_filter_data,
        parse_mode="HTML",
    ),
    # Добавление обязательного канала
    Window(
        Const(
            "<b>➕ Добавление обязательного канала</b>\n\n"
            "Введите ссылку на канал (например, <code>@my_channel</code> или <code>https://t.me/my_channel</code>) "
            "или числовой ID канала (например, <code>-1001234567890</code>):\n\n"
            "⚠️ <i>Убедитесь, что бот предварительно добавлен в администраторы канала!</i>\n\n"
            "Отправьте /cancel для отмены."
        ),
        TextInput(id="add_sub_channel_input", on_success=on_add_channel_input),
        SwitchTo(Const("◀️ Назад"), id="back_add_sub_channel", state=Admin.sub_filter_menu),
        state=Admin.sub_filter_add_channel,
        parse_mode="HTML",
    ),
    on_start=on_admin_dialog_start,
)
