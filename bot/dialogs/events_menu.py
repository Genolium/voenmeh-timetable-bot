from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.text import Const, Format
from aiogram_dialog.widgets.kbd import Button, SwitchTo, Select, Row, Url, Column
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.media import StaticMedia
from aiogram.types import ContentType

from .states import Events
from core.events_manager import EventsManager
from datetime import datetime, timedelta


async def get_events_for_user(dialog_manager: DialogManager, **kwargs):
    session_factory = dialog_manager.middleware_data.get("session_factory")
    manager = EventsManager(session_factory)
    page = dialog_manager.dialog_data.get('page', 0)
    time_filter = dialog_manager.dialog_data.get('time_filter')  # None|'today'|'this_week'
    limit = 10
    offset = page * limit
    # по умолчанию показываем только "сегодня и позже"
    items, total = await manager.list_events(
        only_published=True,
        limit=limit,
        offset=offset,
        now=datetime.utcnow(),
        time_filter=time_filter,
        from_now_only=(time_filter is None),
    )
    def present(e):
        # Ограничиваем название до 25 символов
        title = e.title[:25] + "..." if len(e.title) > 25 else e.title
        
        # Добавляем дату компактно
        date_part = ""
        if e.start_at:
            date_part = f" {e.start_at.strftime('%d.%m')}"
        
        # Добавляем локацию очень кратко
        loc_part = ""
        if e.location:
            loc_short = e.location[:10] + "..." if len(e.location) > 10 else e.location
            loc_part = f" @{loc_short}"
            
        return f"{title}{date_part}{loc_part}"
    return {
        "events": [(present(e), str(e.id)) for e in items],
        "total": total,
        "has_items": bool(items),
        "page": page,
        "has_prev": page > 0,
        "has_next": (offset + limit) < total,
        "time_filter": time_filter or "all",
    }


async def on_event_selected(callback, widget, manager: DialogManager, item_id: str):
    manager.dialog_data['event_id'] = int(item_id)
    await manager.switch_to(Events.details)


async def get_event_details(dialog_manager: DialogManager, **kwargs):
    session_factory = dialog_manager.middleware_data.get("session_factory")
    manager = EventsManager(session_factory)
    event_id = dialog_manager.dialog_data.get('event_id')
    event = await manager.get_event(event_id) if event_id else None
    if not event:
        return {"details": "Событие не найдено", "has_link": False, "event_link": "", "has_image": False, "image_file_id": ""}
    
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
    
    # Локация (если указана)
    if event.location and event.location.strip():
        text_parts.append(f"📍 {event.location}")

    # Описание (если указано)
    if event.description and event.description.strip():
        text_parts.append(f"\n{event.description}")
    
    link = (event.link or "").strip()
    link_valid = link and (link.startswith("http://") or link.startswith("https://") or link.startswith("tg://"))
    return {
        "details": "\n".join(text_parts),
        "has_link": link_valid,
        "event_link": link if link_valid else "",
        "has_image": bool(getattr(event, "image_file_id", None)),
        "image_file_id": getattr(event, "image_file_id", "") or "",
    }


async def on_events_prev(callback, button, manager: DialogManager):
    page = manager.dialog_data.get('page', 0)
    if page > 0:
        manager.dialog_data['page'] = page - 1
    await manager.switch_to(Events.list)


async def on_events_next(callback, button, manager: DialogManager):
    page = manager.dialog_data.get('page', 0)
    manager.dialog_data['page'] = page + 1
    await manager.switch_to(Events.list)


async def on_set_filter(callback, button, manager: DialogManager):
    filter_map = {
        'flt_all': None,
        'flt_today': 'today',
        'flt_week': 'this_week',
    }
    fid = button.widget_id
    manager.dialog_data['time_filter'] = filter_map.get(fid)
    manager.dialog_data['page'] = 0
    await manager.switch_to(Events.list)


events_dialog = Dialog(
    Window(
        Format("🎉 <b>Мероприятия</b> (доступно: {total})\nСтр. {page}"),
        Row(
            Button(Const("Сегодня"), id="flt_today", on_click=on_set_filter),
            Button(Const("Неделя"), id="flt_week", on_click=on_set_filter),
            Button(Const("Все"), id="flt_all", on_click=on_set_filter),
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
        Format("Пока нет опубликованных мероприятий", when=lambda data, w, m: not data.get('has_items')), 
        Row(
            Button(Const("⬅️"), id="user_prev", on_click=on_events_prev, when="has_prev"),
            Button(Const("➡️"), id="user_next", on_click=on_events_next, when="has_next"),
        ),
        state=Events.list,
        getter=get_events_for_user,
        parse_mode="HTML"
    ),
    Window(
        StaticMedia(
            url=Format("{image_file_id}"),
            type=ContentType.PHOTO,
            when="has_image",
        ),
        Format("{details}"),
        Row(
            Url(Const("🔗 Регистрация/ссылка"), url=Format("{event_link}"), when="has_link"),
            SwitchTo(Const("◀️ Назад"), id="back_to_list", state=Events.list)
        ),
        state=Events.details,
        getter=get_event_details,
        parse_mode="HTML"
    )
)


