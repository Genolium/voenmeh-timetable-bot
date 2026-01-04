from datetime import datetime, timedelta

from aiogram.types import ContentType
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Button, Column, Row, Select, SwitchTo, Url
from aiogram_dialog.widgets.media import StaticMedia
from aiogram_dialog.widgets.text import Const, Format

from core.config import MOSCOW_TZ
from core.events_manager import EventsManager
from core.i18n import i18n
from core.user_data import UserDataManager

from .states import Events


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


def _filter_skip_words(text: str, skip_words: list) -> str:
    """Фильтрует служебные слова из текста"""
    if not text:
        return text

    # Приводим список skip_words к нижнему регистру для сравнения
    skip_words_lower = [word.lower() for word in skip_words]

    words = text.split()
    filtered_words = []

    for word in words:
        # Приводим к нижнему регистру для проверки
        lower_word = word.lower()
        # Проверяем, не является ли слово служебным
        if lower_word not in skip_words_lower:
            filtered_words.append(word)

    return " ".join(filtered_words) if filtered_words else ""


async def get_events_for_user(dialog_manager: DialogManager, **kwargs):
    user_id = dialog_manager.event.from_user.id
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_lang = await user_data_manager.get_user_language(user_id)
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    session_factory = dialog_manager.middleware_data.get("session_factory")
    manager = EventsManager(session_factory)
    page = dialog_manager.dialog_data.get("page", 0)
    time_filter = dialog_manager.dialog_data.get("time_filter")  # None|'today'|'this_week'
    limit = 10
    offset = page * limit

    # Определяем, показывать ли только будущие мероприятия
    # Для всех фильтров показываем только будущие мероприятия (с сегодняшнего дня)
    from_now_only = True  # Всегда True - показываем от сегодняшнего дня для всех фильтров

    items, total = await manager.list_events(
        only_published=True,
        limit=limit,
        offset=offset,
        now=datetime.now(MOSCOW_TZ),
        time_filter=time_filter,
        from_now_only=from_now_only,
    )

    def present(e):
        # Ограничиваем название до 25 символов
        title = e.title[:25] + "..." if len(e.title) > 25 else e.title

        # Фильтруем служебные слова из заголовка
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
        title = _filter_skip_words(title, skip_words).strip()

        # Если после фильтрации заголовок пустой, используем исходный
        if not title:
            title = e.title[:25] + "..." if len(e.title) > 25 else e.title

        # Добавляем дату компактно
        date_part = ""
        if e.start_at:
            date_part = f" {e.start_at.strftime('%d.%m')}"

        # Добавляем локацию очень кратко
        loc_part = ""
        if e.location:
            # Фильтруем служебные слова из локации
            filtered_location = _filter_skip_words(e.location, skip_words).strip()
            if filtered_location:  # Показываем только если после фильтрации что-то осталось
                loc_short = filtered_location[:10] + "..." if len(filtered_location) > 10 else filtered_location
                loc_part = f" @{loc_short}"

        return f"{title}{date_part}{loc_part}"

    return {
        "events": [(present(e), str(e.id)) for e in items],
        "total": total,
        "has_items": bool(items),
        "page": page + 1,
        "has_prev": page > 0,
        "has_next": (offset + limit) < total,
        "time_filter": time_filter or "all",
        "events_available": _("events_available", total=total, page=page + 1),
        "btn_today": _("events_today"),
        "btn_week": _("events_week"),
        "btn_all": _("events_all"),
        "events_empty": _("events_empty"),
    }


async def on_event_selected(callback, widget, manager: DialogManager, item_id: str):
    manager.dialog_data["event_id"] = int(item_id)
    await manager.switch_to(Events.details)


async def get_event_details(dialog_manager: DialogManager, **kwargs):
    user_id = dialog_manager.event.from_user.id
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_lang = await user_data_manager.get_user_language(user_id)
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    session_factory = dialog_manager.middleware_data.get("session_factory")
    manager = EventsManager(session_factory)
    event_id = dialog_manager.dialog_data.get("event_id")
    event = await manager.get_event(event_id) if event_id else None
    if not event:
        return {
            "details": _("events_not_found"),
            "has_link": False,
            "event_link": "",
            "has_image": False,
            "image_file_id": "",
            "btn_back": _("events_back"),
            "btn_reg": _("events_registration"),
        }

    # Формируем детали мероприятия
    text_parts = [f"<b>{event.title}</b>"]

    # Дата/время (если указана и не 00:00)
    if event.start_at:
        if event.start_at.hour == 0 and event.start_at.minute == 0:
            # Показываем только дату без времени
            text_parts.append(f"🗓 {event.start_at.strftime('%d.%m.%Y')}")
        else:
            # Показываем дату и время
            text_parts.append(f"🗓 {event.start_at.strftime('%d.%m.%Y %H:%M')}")

    # Локация (если указана и не является служебным словом)
    if event.location and not _is_empty_field(event.location):
        text_parts.append(f"📍 {event.location}")

    # Описание (если указано и не является служебным словом)
    if event.description and not _is_empty_field(event.description):
        text_parts.append(f"\n{event.description}")

    link = (event.link or "").strip()
    link_valid = link and (link.startswith("http://") or link.startswith("https://") or link.startswith("tg://"))
    return {
        "details": "\n".join(text_parts),
        "has_link": link_valid,
        "event_link": link if link_valid else "",
        "has_image": bool(getattr(event, "image_file_id", None)),
        "image_file_id": getattr(event, "image_file_id", "") or "",
        "btn_back": _("events_back"),
        "btn_reg": _("events_registration"),
    }


async def on_events_prev(callback, button, manager: DialogManager):
    page = manager.dialog_data.get("page", 0)
    if page > 0:
        manager.dialog_data["page"] = page - 1
    await manager.switch_to(Events.list)


async def on_events_next(callback, button, manager: DialogManager):
    page = manager.dialog_data.get("page", 0)
    manager.dialog_data["page"] = page + 1
    await manager.switch_to(Events.list)


async def on_set_filter(callback, button, manager: DialogManager):
    filter_map = {
        "flt_all": None,
        "flt_today": "today",
        "flt_week": "this_week",
    }
    fid = button.widget_id
    manager.dialog_data["time_filter"] = filter_map.get(fid)
    manager.dialog_data["page"] = 0
    await manager.switch_to(Events.list)


events_dialog = Dialog(
    Window(
        Format("{events_available}"),
        Row(
            Button(Format("{btn_today}"), id="flt_today", on_click=on_set_filter),
            Button(Format("{btn_week}"), id="flt_week", on_click=on_set_filter),
            Button(Format("{btn_all}"), id="flt_all", on_click=on_set_filter),
        ),
        Column(
            Select(
                Format("{item[0]}"),
                id="events_select",
                item_id_getter=lambda item: item[1],
                items="events",
                on_click=on_event_selected,
                when="has_items",
            ),
        ),
        Format(
            "{events_empty}",
            when=lambda data, w, m: not data.get("has_items"),
        ),
        Row(
            Button(Const("⬅️"), id="user_prev", on_click=on_events_prev, when="has_prev"),
            Button(Const("➡️"), id="user_next", on_click=on_events_next, when="has_next"),
        ),
        state=Events.list,
        getter=get_events_for_user,
        parse_mode="HTML",
    ),
    Window(
        StaticMedia(
            url=Format("{image_file_id}"),
            type=ContentType.PHOTO,
            when="has_image",
        ),
        Format("{details}"),
        Row(
            Url(
                Format("{btn_reg}"),
                url=Format("{event_link}"),
                when="has_link",
            ),
            SwitchTo(Format("{btn_back}"), id="back_to_list", state=Events.list),
        ),
        state=Events.details,
        getter=get_event_details,
        parse_mode="HTML",
    ),
)
