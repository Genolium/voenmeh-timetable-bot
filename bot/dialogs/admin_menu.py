import asyncio
import os
import random
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.types import CallbackQuery, ContentType, FSInputFile, Message
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import MessageInput, TextInput
from aiogram_dialog.widgets.kbd import Back, Button, Column, Row, Select, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

from bot.dialogs.schedule_view import cleanup_old_cache, get_cache_info
from bot.scheduler import evening_broadcast, morning_summary_broadcast
from bot.tasks import copy_message_task, send_message_task
from bot.text_formatters import generate_reminder_text
from core.config import MOSCOW_TZ
from core.db.backups import BackupError, create_db_backup, maybe_decompress_gzip, restore_db_backup
from core.events_manager import EventsManager
from core.feedback_manager import FeedbackManager
from core.manager import TimetableManager
from core.metrics import TASKS_SENT_TO_QUEUE
from core.semester_settings import SemesterSettingsManager
from core.user_data import UserDataManager

from .constants import WidgetIds
from .states import Admin

# Генерация полного расписания отключена; переменная сохраняется для совместимости тестов UI
active_generations = {}
EVENTS_PAGE_SIZE = 10


def _is_empty_field(value: str) -> bool:
    """Проверяет, является ли поле пустым или содержит служебные слова"""
    if not value or not value.strip():
        return True

    # Приводим к нижнему регистру для проверки
    lower_value = value.strip().lower()
    skip_words = [
        "пропустить",
        "пропуск",
        "skip",
        "отмена",
        "отменить",
        "cancel",
        "нет",
        "no",
        "none",
        "-",
        "—",
        "–",
        ".",
        "пусто",
        "empty",
        "null",
    ]

    return lower_value in skip_words


def _is_cancel(text: str) -> bool:
    raw = (text or "").strip().lower()
    return raw in {"отмена", "cancel", "отменить"}


def _is_skip(text: str) -> bool:
    raw = (text or "").strip().lower()
    return raw in {"пропустить", "skip", "-", "пусто", "empty", ""}


async def on_test_morning(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    await callback.answer("🚀 Запускаю постановку задач на утреннюю рассылку...")
    await morning_summary_broadcast(user_data_manager, timetable_manager)
    await callback.message.answer("✅ Задачи для утренней рассылки поставлены в очередь.")


async def on_test_evening(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager = manager.middleware_data.get("user_data_manager")
    timetable_manager = manager.middleware_data.get("manager")
    await callback.answer("🚀 Запускаю постановку задач на вечернюю рассылку...")
    await evening_broadcast(user_data_manager, timetable_manager)
    await callback.message.answer("✅ Задачи для вечерней рассылки поставлены в очередь.")


async def on_test_reminders_for_week(callback: CallbackQuery, button: Button, manager: DialogManager):
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
        else:
            try:
                pass
            except Exception as e:
                await bot.send_message(admin_id, f"⚠️ Ошибка обработки расписания: {e}")

    await bot.send_message(admin_id, "✅ <b>Тестирование планировщика напоминаний завершено.</b>")


async def on_test_alert(callback: CallbackQuery, button: Button, manager: DialogManager):
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


async def on_generate_full_schedule(callback: CallbackQuery, button: Button, manager: DialogManager):
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


async def on_check_graduated_groups(callback: CallbackQuery, button: Button, manager: DialogManager):
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
    redis_client = Redis.from_url(redis_url, password=redis_password, decode_responses=False)

    await callback.answer("🔍 Запускаю проверку выпустившихся групп...")
    await bot.send_message(admin_id, "🔍 Начинаю проверку выпустившихся групп...")

    try:
        # Импортируем функцию из scheduler
        from bot.scheduler import handle_graduated_groups

        await handle_graduated_groups(user_data_manager, timetable_manager, redis_client)
        await bot.send_message(admin_id, "✅ Проверка выпустившихся групп завершена!")
    except Exception as e:
        await bot.send_message(admin_id, f"❌ Ошибка при проверке выпустившихся групп: {e}")
        import traceback

        await bot.send_message(admin_id, f"🔍 Детали ошибки:\n<code>{traceback.format_exc()}</code>")


async def on_semester_settings(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переход к настройкам семестров."""
    await manager.switch_to(Admin.semester_settings)


async def on_admin_categories(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.switch_to(Admin.categories_menu)


async def on_admin_events(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.switch_to(Admin.events_menu)


BASE_BACKUP_TEXT = (
    "💾 <b>Резервные копии базы данных</b>\n\n"
    "• <b>Создать бэкап</b> — получите SQL-файл с текущим состоянием БД.\n"
    "• <b>Загрузить бэкап</b> — восстановите базу из SQL или .gz файла. "
    "Текущие данные будут перезаписаны."
)


async def get_backup_menu_data(dialog_manager: DialogManager, **kwargs):
    status = dialog_manager.dialog_data.get("backup_status")
    text = BASE_BACKUP_TEXT
    if status:
        text = f"{BASE_BACKUP_TEXT}\n\n{status}"
    return {"backup_status": text}


async def on_backup_create(callback: CallbackQuery, button: Button, manager: DialogManager):
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
            "✅ Бэкап успешно создан. Файл отправлен в этот чат. "
            "Сохраните его в безопасном месте."
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


async def on_backup_upload_received(message: Message, widget: MessageInput, manager: DialogManager):
    document = message.document
    if not document:
        await message.answer("❌ Отправьте документ с расширением .sql или .sql.gz.")
        return

    await message.answer("⏳ Получен файл. Начинаю восстановление, это может занять несколько минут...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / (document.file_name or "backup.sql")
        await message.bot.download(document, destination=tmp_path)
        target_path = maybe_decompress_gzip(tmp_path)

        try:
            await restore_db_backup(target_path)
        except BackupError as exc:
            status = f"❌ Не удалось применить резервную копию: {exc}"
            manager.dialog_data["backup_status"] = status
            manager.dialog_data["backup_upload_status"] = status
            await message.answer(status)
            await manager.switch_to(Admin.backup_upload_confirm)
            return

    success_status = (
        "✅ База данных успешно восстановлена из резервной копии.\n"
        "Перезагрузите сервисы, если изменения не применились автоматически."
    )
    manager.dialog_data["backup_upload_status"] = success_status
    manager.dialog_data["backup_status"] = success_status
    await message.answer(success_status)
    await manager.switch_to(Admin.backup_upload_confirm)


async def get_backup_upload_data(dialog_manager: DialogManager, **kwargs):
    status = dialog_manager.dialog_data.get(
        "backup_upload_status",
        "ℹ️ Ожидаем результат восстановления...",
    )
    return {"backup_upload_status": status}

async def get_categories_list(dialog_manager: DialogManager, **kwargs):
    session_factory = dialog_manager.middleware_data.get("session_factory")
    events = EventsManager(session_factory)
    categories = await events.list_categories(only_active=False)
    lines = []
    for c in categories:
        prefix = "— " if c.parent_id else ""
        status = "✅" if c.is_active else "🚫"
        lines.append(f"{status} {prefix}<b>{c.name}</b> (id={c.id})")
    text = "\n".join(lines) or "Категории не созданы"
    return {"categories_text": text}


async def get_events_list(dialog_manager: DialogManager, **kwargs):
    session_factory = dialog_manager.middleware_data.get("session_factory")
    events = EventsManager(session_factory)
    page = dialog_manager.dialog_data.get("events_page", 0)
    offset = page * EVENTS_PAGE_SIZE

    # Получаем фильтр публикации
    pub_filter = dialog_manager.dialog_data.get("events_pub_filter", "all")

    if pub_filter == "published":
        only_published = True
    elif pub_filter == "hidden":
        only_published = False
    else:  # 'all'
        only_published = None

    # Показываем мероприятия начиная с сегодняшнего дня (без прошедших)
    from datetime import datetime as _dt

    items, total = await events.list_events(
        only_published=only_published,
        limit=EVENTS_PAGE_SIZE,
        offset=offset,
        now=_dt.now(MOSCOW_TZ),
        from_now_only=True,
    )
    # Применяем поисковый фильтр если есть
    search_query = dialog_manager.dialog_data.get("events_search", "").strip().lower()
    if search_query:
        filtered_items = []
        for item in items:
            if (
                search_query in item.title.lower()
                or (item.description and search_query in item.description.lower())
                or (item.location and search_query in item.location.lower())
            ):
                filtered_items.append(item)
        items = filtered_items
        total = len(items)
        # Применяем пагинацию после фильтрации
        items = items[offset : offset + EVENTS_PAGE_SIZE]

    # Фильтрация служебных слов из заголовков
    skip_words = {
        "пропустить",
        "пропуск",
        "skip",
        "отмена",
        "отменить",
        "cancel",
        "нет",
        "no",
        "none",
        "-",
        "—",
        "–",
        ".",
        "пусто",
        "empty",
        "null",
    }

    def _clean_title(title: str) -> str:
        if not title:
            return title
        filtered = " ".join(w for w in title.split() if w.lower() not in skip_words).strip()
        return filtered or title

    lines = [f"{('✅' if e.is_published else '🚫')} <b>{_clean_title(e.title)}</b> (id={e.id})" for e in items]
    return {
        "events_text": ("\n".join(lines) or "Мероприятий нет"),
        "total_events": total,
        "page": page,
        "has_prev": page > 0,
        "has_next": (offset + EVENTS_PAGE_SIZE) < total,
        "events_items": [(f"{('✅' if e.is_published else '🚫')} {_clean_title(e.title)}", str(e.id)) for e in items],
    }


async def on_events_prev(callback: CallbackQuery, button: Button, manager: DialogManager):
    page = manager.dialog_data.get("events_page", 0)
    if page > 0:
        manager.dialog_data["events_page"] = page - 1
    await manager.switch_to(Admin.events_menu)


async def on_events_next(callback: CallbackQuery, button: Button, manager: DialogManager):
    page = manager.dialog_data.get("events_page", 0)
    manager.dialog_data["events_page"] = page + 1
    await manager.switch_to(Admin.events_menu)


async def on_event_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str):
    manager.dialog_data["selected_event_id"] = int(item_id)
    await manager.switch_to(Admin.event_details)


async def on_events_set_filter(callback: CallbackQuery, button: Button, manager: DialogManager):
    btn_id = button.widget_id
    if btn_id == "evt_filter_all":
        manager.dialog_data["events_pub_filter"] = "all"
    elif btn_id == "evt_filter_pub":
        manager.dialog_data["events_pub_filter"] = "published"
    elif btn_id == "evt_filter_hidden":
        manager.dialog_data["events_pub_filter"] = "hidden"
    manager.dialog_data["events_page"] = 0
    await manager.switch_to(Admin.events_menu)


async def on_events_search_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.events_menu)
        return
    if _is_skip(raw) or not raw:
        manager.dialog_data["events_search"] = ""
        await message.answer("🔍 Поиск очищен")
    else:
        manager.dialog_data["events_search"] = raw
        await message.answer(f"🔍 Поиск установлен: {raw}")
    manager.dialog_data["events_page"] = 0
    await manager.switch_to(Admin.events_menu)


async def get_event_admin_details(dialog_manager: DialogManager, **kwargs):
    session_factory = dialog_manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    event_id = dialog_manager.dialog_data.get("selected_event_id")
    item = await ev.get_event(event_id) if event_id else None
    if not item:
        return {"event_text": "Событие не найдено"}
    text_parts = [
        f"<b>{item.title}</b>",
        f"🆔 {item.id}",
        f"Статус: {'✅ Опубликовано' if item.is_published else '🚫 Скрыто'}",
    ]

    # Дата/время (если указана и не 00:00)
    if item.start_at:
        if item.start_at.hour == 0 and item.start_at.minute == 0:
            text_parts.append(f"🗓 {item.start_at.strftime('%d.%m.%Y')}")
        else:
            text_parts.append(f"🗓 {item.start_at.strftime('%d.%m.%Y %H:%M')}")

    # Локация (если указана и не является служебным словом)
    if item.location and not _is_empty_field(item.location):
        text_parts.append(f"📍 {item.location}")

    # Ссылка (если указана и не является служебным словом)
    if item.link and not _is_empty_field(item.link):
        text_parts.append(f"🔗 {item.link}")

    # Изображение (если есть)
    if getattr(item, "image_file_id", None):
        text_parts.append("🖼 Изображение: добавлено")

    # Описание (если указано и не является служебным словом)
    if item.description and not _is_empty_field(item.description):
        text_parts.append(f"\n{item.description}")

    text = "\n".join(text_parts)
    return {
        "event_text": text,
        "is_published": item.is_published,
        "has_image": bool(getattr(item, "image_file_id", None)),
    }


async def on_event_delete(callback: CallbackQuery, button: Button, manager: DialogManager):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    event_id = manager.dialog_data.get("selected_event_id")
    if event_id:
        await ev.delete_event(event_id)
        await callback.answer("🗑️ Удалено")
    await manager.switch_to(Admin.events_menu)


async def on_event_toggle_publish(callback: CallbackQuery, button: Button, manager: DialogManager):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    event_id = manager.dialog_data.get("selected_event_id")
    item = await ev.get_event(event_id)
    if item:
        await ev.update_event(event_id, is_published=not item.is_published)
        await callback.answer("Статус обновлён")
    await manager.switch_to(Admin.event_details)


async def on_event_edit_menu(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.switch_to(Admin.event_edit_menu)


async def on_event_edit_title(message: Message, widget: TextInput, manager: DialogManager, data: str):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.event_details)
        return
    if not raw:
        await message.answer("❌ Заголовок не может быть пустым")
        return
    if len(raw) > 255:
        await message.answer("❌ Заголовок слишком длинный (максимум 255 символов)")
        return
    await ev.update_event(eid, title=raw)
    await message.answer("✅ Заголовок обновлён")
    await manager.switch_to(Admin.event_details)


async def on_event_edit_datetime(message: Message, widget: TextInput, manager: DialogManager, data: str):
    from datetime import datetime as dt

    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.event_details)
        return
    if not raw:
        await ev.update_event(eid, start_at=None)
        await message.answer("✅ Дата/время очищены")
        await manager.switch_to(Admin.event_details)
        return
    try:
        # Проверяем, есть ли уже сохранённая дата из предыдущего шага
        base_date_str = manager.dialog_data.get("edit_date")

        # Если пользователь ввёл только время (ЧЧ:ММ) и есть сохранённая дата
        if ":" in raw and "." not in raw and base_date_str:
            try:
                # Десериализуем дату из ISO строки
                base_date = dt.fromisoformat(base_date_str)
                hh, mm = raw.split(":", 1)
                result = base_date.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                await ev.update_event(eid, start_at=result)
                await message.answer("✅ Дата и время обновлены")
                manager.dialog_data.pop("edit_date", None)  # Очищаем сохранённую дату
                await manager.switch_to(Admin.event_details)
                return
            except (ValueError, TypeError):
                await message.answer("❌ Неверный формат времени. Используйте ЧЧ:ММ")
                return

        # Если пользователь ввёл дату и время вместе (ДД.ММ.ГГГГ ЧЧ:ММ)
        if " " in raw:
            d_part, t_part = raw.split(" ", 1)
            d_val = dt.strptime(d_part.strip(), "%d.%m.%Y")
            hh, mm = t_part.strip().split(":", 1)
            result = d_val.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            await ev.update_event(eid, start_at=result)
            await message.answer("✅ Дата и время обновлены")
            manager.dialog_data.pop("edit_date", None)  # Очищаем сохранённую дату
            await manager.switch_to(Admin.event_details)
            return

        # Если пользователь ввёл только дату (ДД.ММ.ГГГГ)
        else:
            date_val = dt.strptime(raw, "%d.%m.%Y")
            # Сохраняем дату как ISO строку для JSON сериализации
            manager.dialog_data["edit_date"] = date_val.isoformat()
            await message.answer("✅ Дата принята. Теперь введите время в формате ЧЧ:ММ (или пусто)")
            await manager.switch_to(Admin.event_edit_time)
            return

    except Exception:
        # Если есть сохранённая дата и пользователь ввёл только время
        base_date_str = manager.dialog_data.get("edit_date")
        if base_date_str and ":" in raw and "." not in raw:
            await message.answer("❌ Неверный формат времени. Используйте ЧЧ:ММ")
        else:
            await message.answer(
                "❌ Неверный формат. Используйте ДД.ММ.ГГГГ, ДД.ММ.ГГГГ ЧЧ:ММ, или просто ЧЧ:ММ если дата уже задана"
            )


async def on_event_edit_time(message: Message, widget: TextInput, manager: DialogManager, data: str):
    from datetime import datetime as dt
    from datetime import time as dtime

    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    base_date_str = manager.dialog_data.get("edit_date")
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.event_details)
        return
    if not base_date_str:
        await message.answer("⚠️ Сначала введите дату")
        await manager.switch_to(Admin.event_edit_datetime)
        return

    # Десериализуем дату из ISO строки
    try:
        base_date = dt.fromisoformat(base_date_str)
    except (ValueError, TypeError):
        await message.answer("⚠️ Ошибка с сохранённой датой. Введите дату заново")
        await manager.switch_to(Admin.event_edit_datetime)
        return

    if raw and not _is_skip(raw) and raw.lower() not in {"пусто", "empty"}:
        try:
            hh, mm = raw.split(":", 1)
            hh_i, mm_i = int(hh), int(mm)
            result = base_date.replace(hour=hh_i, minute=mm_i, second=0, microsecond=0)
        except Exception:
            await message.answer("❌ Неверный формат времени. Используйте ЧЧ:ММ")
            return
    else:
        result = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
    await ev.update_event(eid, start_at=result)
    await message.answer("✅ Дата и время обновлены")
    manager.dialog_data.pop("edit_date", None)  # Очищаем сохранённую дату
    await manager.switch_to(Admin.event_details)


async def on_event_edit_location(message: Message, widget: TextInput, manager: DialogManager, data: str):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.event_details)
        return
    if _is_skip(raw):
        await message.answer("↩️ Без изменений")
        await manager.switch_to(Admin.event_details)
        return
    if raw.lower() in {"очистить", "clear"}:
        await ev.update_event(eid, location=None)
    else:
        # Ограничиваем длину локации
        if len(raw) > 255:
            await message.answer("❌ Локация слишком длинная (максимум 255 символов)")
            return
        await ev.update_event(eid, location=raw)
    await message.answer("✅ Локация обновлена")
    await manager.switch_to(Admin.event_details)


async def on_event_edit_description(message: Message, widget: TextInput, manager: DialogManager, data: str):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.event_details)
        return
    if _is_skip(raw):
        await message.answer("↩️ Без изменений")
        await manager.switch_to(Admin.event_details)
        return
    if raw.lower() in {"очистить", "clear"}:
        await ev.update_event(eid, description=None)
    else:
        await ev.update_event(eid, description=raw)
    await message.answer("✅ Описание обновлено")
    await manager.switch_to(Admin.event_details)


async def on_event_edit_link(message: Message, widget: TextInput, manager: DialogManager, data: str):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.event_details)
        return
    if _is_skip(raw):
        await message.answer("↩️ Без изменений")
        await manager.switch_to(Admin.event_details)
        return
    if raw.lower() in {"очистить", "clear"}:
        await ev.update_event(eid, link=None)
    else:
        # Проверяем длину ссылки
        if len(raw) > 512:
            await message.answer("❌ Ссылка слишком длинная (максимум 512 символов)")
            return
        # Проверяем формат ссылки (базовая валидация)
        if raw and not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("tg://")):
            await message.answer("⚠️ Предупреждение: ссылка не начинается с http://, https:// или tg://")
        await ev.update_event(eid, link=raw)
    await message.answer("✅ Ссылка обновлена")
    await manager.switch_to(Admin.event_details)


async def on_event_edit_image(message: Message, message_input: MessageInput, manager: DialogManager):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    file_id = None

    # Проверяем фото
    if getattr(message, "photo", None):
        try:
            file_id = message.photo[-1].file_id
        except (IndexError, AttributeError):
            file_id = None

    # Проверяем документ (если не фото)
    if not file_id and getattr(message, "document", None):
        try:
            doc = message.document
            # Проверяем размер файла (максимум 20MB для Telegram Bot API)
            if doc.file_size and doc.file_size > 20 * 1024 * 1024:
                await message.answer("❌ Файл слишком большой (максимум 20MB)")
                return
            # Проверяем MIME-type
            if (doc.mime_type or "").startswith("image/"):
                file_id = doc.file_id
            else:
                await message.answer("❌ Документ должен быть изображением")
                return
        except Exception:
            file_id = None

    if not file_id:
        await message.answer("❌ Пришлите фото или изображение документом")
        return

    try:
        await ev.update_event(eid, image_file_id=file_id)
        await message.answer("✅ Изображение сохранено")
    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения: {e}")
    await manager.switch_to(Admin.event_details)


async def on_category_create(message: Message, widget: TextInput, manager: DialogManager, data: str):
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.categories_menu)
        return
    if not raw:
        await message.answer("❌ Пустое название категории")
        return
    name = raw
    parent_id = None
    if "|" in raw:
        try:
            name, parent_str = raw.split("|", 1)
            name = name.strip()
            parent_id = int(parent_str.strip()) if parent_str.strip() else None
        except ValueError:
            await message.answer("❌ Неверный формат parent_id. Используйте число или оставьте пустым")
            return
    if not name:
        await message.answer("❌ Название категории не может быть пустым")
        return
    if len(name) > 255:
        await message.answer("❌ Название категории слишком длинное (максимум 255 символов)")
        return
    try:
        await ev.create_category(name=name, parent_id=parent_id)
        await message.answer("✅ Категория создана")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await manager.switch_to(Admin.categories_menu)


async def on_event_create(message: Message, widget: TextInput, manager: DialogManager, data: str):
    from datetime import datetime as dt

    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    raw = (message.text or "").strip()
    # Формат: Заголовок|YYYY-MM-DD HH:MM|Локация|КатегорияID|Ссылка
    try:
        if _is_cancel(raw):
            await message.answer("↩️ Отменено")
            await manager.switch_to(Admin.events_menu)
            return
        parts = [p.strip() for p in raw.split("|")]
        title = parts[0]
        start_at = dt.strptime(parts[1], "%Y-%m-%d %H:%M") if len(parts) > 1 and parts[1] else None
        location = parts[2] if len(parts) > 2 else None
        link = parts[3] if len(parts) > 3 else None
        await ev.create_event(title=title, start_at=start_at, location=location, link=link)
        await message.answer("✅ Мероприятие создано")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await manager.switch_to(Admin.events_menu)


# --- Создание события (пошагово) ---
async def on_cr_title(message: Message, widget: TextInput, manager: DialogManager, data: str):
    title = (message.text or "").strip()
    if _is_cancel(title):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.events_menu)
        return
    if not title:
        await message.answer("❌ Введите заголовок или отмените создание")
        return
    if len(title) > 255:
        await message.answer("❌ Заголовок слишком длинный (максимум 255 символов)")
        return
    manager.dialog_data["cr_title"] = title
    await manager.switch_to(Admin.event_create_datetime)


async def on_cr_date(message: Message, widget: TextInput, manager: DialogManager, data: str):
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.events_menu)
        return
    if raw:
        try:
            from datetime import datetime as dt

            if " " in raw:
                d_part, t_part = raw.split(" ", 1)
                d_val = dt.strptime(d_part.strip(), "%d.%m.%Y")
                hh, mm = t_part.strip().split(":", 1)
                result_dt = d_val.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                # Сохраняем дату с временем как ISO строку
                manager.dialog_data["cr_dt"] = result_dt.isoformat()
                manager.dialog_data["cr_time"] = (int(hh), int(mm))
            else:
                date_val = dt.strptime(raw, "%d.%m.%Y")
                # Сохраняем дату как ISO строку для JSON сериализации
                manager.dialog_data["cr_dt"] = date_val.isoformat()
        except ValueError:
            await message.answer("❌ Неверный формат. Используйте ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ")
            return
    else:
        manager.dialog_data["cr_dt"] = None
    # Если дата указана — спрашиваем время, иначе переходим к локации
    if manager.dialog_data.get("cr_dt") and not manager.dialog_data.get("cr_time"):
        await manager.switch_to(Admin.event_create_time)
    else:
        await manager.switch_to(Admin.event_create_location)


async def on_cr_location(message: Message, widget: TextInput, manager: DialogManager, data: str):
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.events_menu)
        return
    if raw and len(raw) > 255:
        await message.answer("❌ Локация слишком длинная (максимум 255 символов)")
        return
    manager.dialog_data["cr_loc"] = raw
    await manager.switch_to(Admin.event_create_description)


async def on_cr_desc(message: Message, widget: TextInput, manager: DialogManager, data: str):
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.events_menu)
        return
    manager.dialog_data["cr_desc"] = raw
    await manager.switch_to(Admin.event_create_link)


async def on_cr_link(message: Message, widget: TextInput, manager: DialogManager, data: str):
    txt = (message.text or "").strip()
    if _is_cancel(txt):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.events_menu)
        return
    if txt.lower() in {"пропустить", "skip", "-"}:
        manager.dialog_data["cr_link"] = ""
    else:
        if txt and len(txt) > 512:
            await message.answer("❌ Ссылка слишком длинная (максимум 512 символов)")
            return
        if txt and not (txt.startswith("http://") or txt.startswith("https://") or txt.startswith("tg://")):
            await message.answer("⚠️ Предупреждение: ссылка не начинается с http://, https:// или tg://")
        manager.dialog_data["cr_link"] = txt
    await manager.switch_to(Admin.event_create_confirm)


async def on_cr_time(message: Message, widget: TextInput, manager: DialogManager, data: str):
    raw = (message.text or "").strip()
    if _is_cancel(raw):
        await message.answer("↩️ Отменено")
        await manager.switch_to(Admin.events_menu)
        return
    if _is_skip(raw) or raw.lower() in {"пусто", "empty"}:
        manager.dialog_data["cr_time"] = (0, 0)
        await manager.switch_to(Admin.event_create_location)
        return
    try:
        hh, mm = raw.split(":", 1)
        manager.dialog_data["cr_time"] = (int(hh), int(mm))
        await manager.switch_to(Admin.event_create_location)
    except Exception:
        await message.answer("❌ Неверный формат. Используйте ЧЧ:ММ, 'пропустить' или 'пусто'")


async def on_cr_confirm(callback: CallbackQuery, button: Button, manager: DialogManager):
    session_factory = manager.middleware_data.get("session_factory")
    evm = EventsManager(session_factory)
    from datetime import datetime as dt

    title = manager.dialog_data.get("cr_title")
    date_str = manager.dialog_data.get("cr_dt")
    time_tuple = manager.dialog_data.get("cr_time") or (0, 0)

    if date_str is None:
        start_at = None
    else:
        try:
            # Десериализуем дату из ISO строки
            date_obj = dt.fromisoformat(date_str)
            # Если время уже было установлено при парсинге (дата+время), используем как есть
            if date_obj.hour != 0 or date_obj.minute != 0:
                start_at = date_obj
            else:
                # Иначе добавляем время из отдельного поля
                start_at = date_obj.replace(hour=time_tuple[0], minute=time_tuple[1], second=0, microsecond=0)
        except (ValueError, TypeError):
            start_at = None

    location = manager.dialog_data.get("cr_loc") or None
    description = manager.dialog_data.get("cr_desc") or None
    link = manager.dialog_data.get("cr_link") or None
    await evm.create_event(
        title=title,
        start_at=start_at,
        location=location,
        description=description,
        link=link,
        admin_id=callback.from_user.id,
    )
    await callback.answer("✅ Создано")
    await manager.switch_to(Admin.events_menu)


async def on_event_show_image(callback: CallbackQuery, button: Button, manager: DialogManager):
    bot: Bot = manager.middleware_data.get("bot")
    session_factory = manager.middleware_data.get("session_factory")
    ev = EventsManager(session_factory)
    eid = manager.dialog_data.get("selected_event_id")
    item = await ev.get_event(eid) if eid else None
    if not item or not getattr(item, "image_file_id", None):
        await callback.answer("❌ Изображение не задано", show_alert=True)
        return

    # Формируем текст мероприятия
    text = f"<b>{item.title}</b>\n"
    if item.start_at:
        text += f"🗓 {item.start_at.strftime('%d.%m.%Y %H:%M')}\n"
    if item.location:
        text += f"📍 {item.location}\n"

    if item.link:
        text += f"🔗 {item.link}\n"
    if item.description:
        text += f"\n{item.description}"

    try:
        # Пытаемся отправить как фото с подписью
        await bot.send_photo(callback.from_user.id, item.image_file_id, caption=text, parse_mode="HTML")
    except Exception:
        try:
            # Если не получается как фото, отправляем как документ с подписью
            await bot.send_document(
                callback.from_user.id,
                item.image_file_id,
                caption=text,
                parse_mode="HTML",
            )
        except Exception:
            # Если совсем не получается, отправляем отдельно изображение и текст
            try:
                await bot.send_photo(callback.from_user.id, item.image_file_id)
                await bot.send_message(callback.from_user.id, text, parse_mode="HTML")
            except Exception:
                await callback.answer("❌ Не удалось отправить изображение", show_alert=True)


async def get_create_preview(dialog_manager: DialogManager, **kwargs):
    title = dialog_manager.dialog_data.get("cr_title")
    date_str = dialog_manager.dialog_data.get("cr_dt")
    time_tuple = dialog_manager.dialog_data.get("cr_time") or (0, 0)
    location = dialog_manager.dialog_data.get("cr_loc")
    description = dialog_manager.dialog_data.get("cr_desc")
    link = dialog_manager.dialog_data.get("cr_link")

    # Формируем текст предпросмотра
    text_parts = ["<b>Предпросмотр</b>\n"]

    # Название (обязательное)
    if title:
        text_parts.append(f"Название: <b>{title}</b>")

    # Дата/время (если указана)
    if date_str is not None:
        try:
            from datetime import datetime as dt

            date_obj = dt.fromisoformat(date_str)
            # Если время уже установлено в дате, используем его
            if date_obj.hour != 0 or date_obj.minute != 0:
                dt_text = date_obj.strftime("%d.%m.%Y %H:%M")
            else:
                # Иначе добавляем время из отдельного поля
                dt_full = date_obj.replace(hour=time_tuple[0], minute=time_tuple[1], second=0, microsecond=0)
                # Если время 00:00, показываем только дату
                if dt_full.hour == 0 and dt_full.minute == 0:
                    dt_text = dt_full.strftime("%d.%m.%Y")
                else:
                    dt_text = dt_full.strftime("%d.%m.%Y %H:%M")
            text_parts.append(f"Дата/время: <b>{dt_text}</b>")
        except Exception:
            text_parts.append("Дата/время: <b>(ошибка формата)</b>")

    # Локация (если указана и не является служебным словом)
    if location and not _is_empty_field(location):
        text_parts.append(f"Локация: <b>{location}</b>")

    # Ссылка (если указана и не является служебным словом)
    if link and not _is_empty_field(link):
        text_parts.append(f"Ссылка: <b>{link}</b>")

    # Описание (если указано и не является служебным словом)
    if description and not _is_empty_field(description):
        text_parts.append(f"\nОписание:\n{description}")

    return {"create_preview": "\n".join(text_parts)}


async def on_edit_fall_semester(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переход к редактированию даты осеннего семестра."""
    await manager.switch_to(Admin.edit_fall_semester)


async def on_edit_spring_semester(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переход к редактированию даты весеннего семестра."""
    await manager.switch_to(Admin.edit_spring_semester)


async def on_fall_semester_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
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


async def on_spring_semester_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
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
):
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


def render_template(template_text: str, user_info) -> str:
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


async def on_segment_criteria_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
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


async def on_template_input_message(message: Message, message_input: MessageInput, manager: DialogManager):
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


async def get_preview_data(dialog_manager: DialogManager, **kwargs):
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


async def on_confirm_segment_send(callback: CallbackQuery, button: Button, manager: DialogManager):
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


async def on_period_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str):
    """Обновляет период в `dialog_data` при нажатии на кнопку."""
    manager.dialog_data["stats_period"] = int(item_id)


async def get_stats_data(dialog_manager: DialogManager, **kwargs):
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

    period_map = {1: "День", 7: "Неделя", 30: "Месяц"}

    top_groups_text = "\n".join([f"  - {g or 'Не указана'}: {c}" for g, c in top_groups])
    subs_breakdown_text = (
        f"  - Вечер: {subs_breakdown.get('evening', 0)}\n"
        f"  - Утро: {subs_breakdown.get('morning', 0)}\n"
        f"  - Пары: {subs_breakdown.get('reminders', 0)}"
    )
    group_dist_text = "\n".join([f"  - {category}: {count}" for category, count in group_dist.items()])

    stats_text = (
        f"📊 <b>Статистика бота</b> (Период: <b>{period_map.get(period, '')}</b>)\n\n"
        f"👤 <b>Общая картина</b>\n"
        f"  - Всего пользователей: <b>{total_users}</b>\n"
        f"  - Новых за период: <b>{new_users}</b>\n\n"
        f"🏃‍♂️ <b>Активность</b>\n"
        f"  - Активных за период: <b>{active_users}</b>\n"
        f"  - DAU / WAU / MAU: <b>{dau} / {wau} / {mau}</b>\n\n"
        f"🔔 <b>Вовлеченность</b>\n"
        f"  - С подписками: <b>{subscribed_total}</b>\n"
        f"  - Отписались от всего: <b>{unsubscribed_total}</b>\n"
        f"  <u>Разбивка по подпискам:</u>\n{subs_breakdown_text}\n\n"
        f"🎓 <b>Группы</b>\n"
        f"  <u>Топ-5 групп:</u>\n{top_groups_text}\n"
        f"  <u>Распределение по размеру:</u>\n{group_dist_text}"
    )

    return {
        "stats_text": stats_text,
        "period": period,
        "periods": [("День", 1), ("Неделя", 7), ("Месяц", 30)],
    }


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


async def on_user_id_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
    try:
        user_id = int(message.text)
    except ValueError:
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


async def on_new_group_input(message: Message, widget: TextInput, manager: DialogManager, data: str):
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


async def get_user_manage_data(dialog_manager: DialogManager, **kwargs):
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


async def get_semester_settings_data(dialog_manager: DialogManager, **kwargs):
    """Получает данные для окна настроек семестров."""
    session_factory = dialog_manager.middleware_data.get("session_factory")
    if not session_factory:
        return {"semester_settings_text": "❌ Ошибка: не удалось подключиться к базе данных."}

    settings_manager = SemesterSettingsManager(session_factory)
    settings_text = await settings_manager.get_formatted_settings()

    return {"semester_settings_text": settings_text}


async def on_force_update_schedule(callback: CallbackQuery, button: Button, manager: DialogManager):
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
            await bot.send_message(
                admin_id, "🔄 <b>Принудительное обновление</b>\n\n1. Сбрасываю хеш и кэш заголовков..."
            )
            # Сбрасываем хеш в Redis, чтобы бот "увидел" изменения
            await redis_client.delete(REDIS_SCHEDULE_HASH_KEY)
            # Сбрасываем заголовки ETag/Last-Modified, чтобы сервер не вернул 304
            parser._LAST_ETAG = None
            parser._LAST_MODIFIED = None

            await bot.send_message(
                admin_id, "2. Запрашиваю и обрабатываю новое расписание (это может занять время)..."
            )
            await monitor_schedule_changes(user_data_manager, redis_client, bot)

            await bot.send_message(
                admin_id,
                "✅ <b>Обновление завершено!</b>\nЕсли были изменения, уведомления отправлены админам.",
            )
        except Exception as e:
            await bot.send_message(admin_id, f"❌ Критическая ошибка при обновлении: {e}")

    asyncio.create_task(_process_force_update())



async def on_clear_cache(callback: CallbackQuery, button: Button, manager: DialogManager):
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


async def on_cancel_generation(callback: CallbackQuery):
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
async def on_send_message_to_user(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переход к вводу сообщения для отправки пользователю."""
    await manager.switch_to(Admin.send_message_text)


async def on_message_to_user_input(message: Message, message_input: MessageInput, manager: DialogManager):
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
            SwitchTo(Const("🎉 Мероприятия"), id="section_events", state=Admin.events_menu),
        ),
        Row(
            SwitchTo(Const("📊 Статистика"), id=WidgetIds.STATS, state=Admin.stats),
            SwitchTo(Const("💾 Бэкапы"), id="section_backups", state=Admin.backup_menu),
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
        SwitchTo(Const("◀️ В меню бэкапов"), id="back_from_backup_status", state=Admin.backup_menu),
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
    # --- Категории (админ) ---
    Window(
        Format("🗂 <b>Категории мероприятий</b>\n\n{categories_text}"),
        SwitchTo(Const("➕ Создать"), id="cat_create", state=Admin.category_create),
        SwitchTo(Const("◀️ Назад"), id="cat_back", state=Admin.menu),
        state=Admin.categories_menu,
        getter=get_categories_list,
        parse_mode="HTML",
    ),
    Window(
        Const("Введите название категории (или напишите 'отмена'). Для подкатегории укажите: Название|parent_id"),
        TextInput(id="cat_create_input", on_success=on_category_create),
        SwitchTo(Const("◀️ Назад"), id="cat_create_back", state=Admin.categories_menu),
        state=Admin.category_create,
    ),
    # --- Мероприятия (админ) ---
    Window(
        Format(
            "🎫 <b>Мероприятия</b> (всего: {total_events})\nСтр. {page}\n\n🔍 Поиск: введите текст или 'отмена'/'пропустить' для сброса"
        ),
        Row(
            Button(
                Const("Показать все"),
                id="evt_filter_all",
                on_click=on_events_set_filter,
            ),
            Button(
                Const("Опубликованные"),
                id="evt_filter_pub",
                on_click=on_events_set_filter,
            ),
            Button(Const("Скрытые"), id="evt_filter_hidden", on_click=on_events_set_filter),
        ),
        # Поле ввода не является клавиатурным виджетом, его нельзя вкладывать в Row
        TextInput(id="evt_search_input", on_success=on_events_search_input),
        Row(
            Button(Const("⬅️"), id="evt_prev", on_click=on_events_prev, when="has_prev"),
            Button(Const("➡️"), id="evt_next", on_click=on_events_next, when="has_next"),
        ),
        Column(
            Select(
                Format("{item[0]}"),
                id="admin_events_select",
                item_id_getter=lambda item: item[1],
                items="events_items",
                on_click=on_event_selected,
            ),
        ),
        Row(
            SwitchTo(Const("➕ Создать"), id="evt_create", state=Admin.event_create_title),
            SwitchTo(Const("◀️ Назад"), id="evt_back", state=Admin.menu),
        ),
        state=Admin.events_menu,
        getter=get_events_list,
        parse_mode="HTML",
    ),
    Window(
        Format("{event_text}"),
        Row(
            Button(
                Const("👁️/🙈 Публиковать"),
                id="evt_toggle",
                on_click=on_event_toggle_publish,
            ),
        ),
        Row(
            Button(
                Const("🖼 Предпросмотр"),
                id="evt_show_image",
                on_click=on_event_show_image,
                when="has_image",
            ),
        ),
        Row(
            SwitchTo(
                Const("📝 Заголовок"),
                id="evt_quick_title",
                state=Admin.event_edit_title,
            ),
            SwitchTo(
                Const("📅 Дата/время"),
                id="evt_quick_dt",
                state=Admin.event_edit_datetime,
            ),
        ),
        Row(
            SwitchTo(Const("📍 Локация"), id="evt_quick_loc", state=Admin.event_edit_location),
        ),
        Row(
            SwitchTo(
                Const("📝 Описание"),
                id="evt_quick_desc",
                state=Admin.event_edit_description,
            ),
            SwitchTo(Const("🔗 Ссылка"), id="evt_quick_link", state=Admin.event_edit_link),
        ),
        Row(
            SwitchTo(Const("🖼 Изображение"), id="evt_quick_img", state=Admin.event_edit_image),
        ),
        Row(
            SwitchTo(
                Const("🗑️ Удалить"),
                id="evt_delete_confirm",
                state=Admin.event_delete_confirm,
            ),
            SwitchTo(Const("◀️ Назад"), id="evt_details_back", state=Admin.events_menu),
        ),
        state=Admin.event_details,
        getter=get_event_admin_details,
        parse_mode="HTML",
    ),
    Window(
        Const("Удалить это мероприятие? Это действие необратимо."),
        Row(
            Button(Const("✅ Да, удалить"), id="evt_delete", on_click=on_event_delete),
            SwitchTo(Const("◀️ Отмена"), id="evt_delete_cancel", state=Admin.event_details),
        ),
        state=Admin.event_delete_confirm,
    ),
    Window(
        Const("Введите новый заголовок (или напишите 'отмена'):"),
        TextInput(id="evt_edit_title_input", on_success=on_event_edit_title),
        SwitchTo(Const("◀️ Назад"), id="evt_edit_title_back", state=Admin.event_details),
        state=Admin.event_edit_title,
    ),
    Window(
        Const("Отправьте фото или изображение документом. Будет сохранён file_id для отправки пользователям."),
        MessageInput(on_event_edit_image, content_types=[ContentType.PHOTO, ContentType.DOCUMENT]),
        SwitchTo(Const("◀️ Назад"), id="evt_edit_image_back", state=Admin.event_details),
        state=Admin.event_edit_image,
    ),
    Window(
        Const(
            "Введите дату в формате ДД.ММ.ГГГГ, дату и время ДД.ММ.ГГГГ ЧЧ:ММ, или только время ЧЧ:ММ если дата уже задана (пусто для очистки, 'отмена' для отмены):"
        ),
        TextInput(id="evt_edit_dt_input", on_success=on_event_edit_datetime),
        SwitchTo(Const("◀️ Назад"), id="evt_edit_dt_back", state=Admin.event_details),
        state=Admin.event_edit_datetime,
    ),
    Window(
        Const("Введите время в формате ЧЧ:ММ (или 'пусто'/'пропустить' для 00:00, или 'отмена'):"),
        TextInput(id="evt_edit_time_input", on_success=on_event_edit_time),
        SwitchTo(Const("◀️ Назад"), id="evt_edit_time_back", state=Admin.event_edit_datetime),
        state=Admin.event_edit_time,
    ),
    Window(
        Const("Введите локацию (или пусто для очистки, или напишите 'отмена'):"),
        TextInput(id="evt_edit_loc_input", on_success=on_event_edit_location),
        SwitchTo(Const("◀️ Назад"), id="evt_edit_loc_back", state=Admin.event_details),
        state=Admin.event_edit_location,
    ),
    Window(
        Const("Введите описание (или пусто для очистки, или напишите 'отмена'):"),
        TextInput(id="evt_edit_desc_input", on_success=on_event_edit_description),
        SwitchTo(Const("◀️ Назад"), id="evt_edit_desc_back", state=Admin.event_details),
        state=Admin.event_edit_description,
    ),
    Window(
        Const("Введите ссылку (или пусто для очистки, или напишите 'отмена'):"),
        TextInput(id="evt_edit_link_input", on_success=on_event_edit_link),
        SwitchTo(Const("◀️ Назад"), id="evt_edit_link_back", state=Admin.event_details),
        state=Admin.event_edit_link,
    ),
    Window(
        Const("Шаг 1/6: Введите заголовок (или напишите 'отмена'):"),
        TextInput(id="evt_cr_title", on_success=on_cr_title),
        SwitchTo(Const("◀️ Назад"), id="evt_cr_title_back", state=Admin.events_menu),
        state=Admin.event_create_title,
    ),
    Window(
        Const("Шаг 2/7: Введите дату в формате ДД.ММ.ГГГГ (или напишите 'пропустить' для без даты, или 'отмена'):"),
        TextInput(id="evt_cr_dt", on_success=on_cr_date),
        SwitchTo(Const("◀️ Назад"), id="evt_cr_dt_back", state=Admin.event_create_title),
        state=Admin.event_create_datetime,
    ),
    Window(
        Const("Шаг 3/7: Введите время в формате ЧЧ:ММ (или 'пропустить'/'пусто' для 00:00, или 'отмена'):"),
        TextInput(id="evt_cr_time", on_success=on_cr_time),
        SwitchTo(Const("◀️ Назад"), id="evt_cr_time_back", state=Admin.event_create_datetime),
        state=Admin.event_create_time,
    ),
    Window(
        Const("Шаг 4/6: Введите локацию (или напишите 'пропустить' / 'очистить', или 'отмена'):"),
        TextInput(id="evt_cr_loc", on_success=on_cr_location),
        SwitchTo(Const("◀️ Назад"), id="evt_cr_loc_back", state=Admin.event_create_time),
        state=Admin.event_create_location,
    ),
    Window(
        Const("Шаг 5/6: Введите описание (или напишите 'пропустить' / 'очистить', или 'отмена'):"),
        TextInput(id="evt_cr_desc", on_success=on_cr_desc),
        SwitchTo(Const("◀️ Назад"), id="evt_cr_desc_back", state=Admin.event_create_location),
        state=Admin.event_create_description,
    ),
    Window(
        Const("Шаг 6/6: Введите ссылку (или напишите 'пропустить' / 'очистить', или 'отмена'):"),
        TextInput(id="evt_cr_link", on_success=on_cr_link),
        SwitchTo(
            Const("◀️ Назад"),
            id="evt_cr_link_back",
            state=Admin.event_create_description,
        ),
        state=Admin.event_create_link,
    ),
    Window(
        Format("{create_preview}"),
        Row(
            Button(Const("✅ Создать"), id="evt_cr_confirm", on_click=on_cr_confirm),
            SwitchTo(Const("◀️ Назад"), id="evt_cr_conf_back", state=Admin.event_create_link),
        ),
        state=Admin.event_create_confirm,
        getter=get_create_preview,
        parse_mode="HTML",
    ),
)
