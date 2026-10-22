from datetime import date, datetime, timedelta
from typing import Any

from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, StartMode, Window
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Back, Button, Column, Row, Select, SwitchTo
from aiogram_dialog.widgets.link_preview import LinkPreview
from aiogram_dialog.widgets.media import StaticMedia
from aiogram_dialog.widgets.text import Const, Format

from bot.text_formatters import esc, format_classroom_schedule_text, format_teacher_schedule_text
from core.config import CLASSROOM_IMAGE_PATH, MOSCOW_TZ, SEARCH_IMAGE_PATH, TEACHER_IMAGE_PATH
from core.i18n import i18n
from core.manager import TimetableManager
from core.text_utils import normalize_group_name, transliterate_failed_search
from core.user_data import UserDataManager

from .constants import DialogDataKeys, WidgetIds
from .states import FindMenu, Schedule


async def get_find_data(dialog_manager: DialogManager, **kwargs):
    if not dialog_manager.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO):
        dialog_manager.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()

    current_date = date.fromisoformat(dialog_manager.dialog_data[DialogDataKeys.CURRENT_DATE_ISO])
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id

    # CRITICAL: Fetch user language from DB for proper localization
    user_lang = await user_data_manager.get_user_language(user_id) if user_data_manager else "ru"
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    data = {"found_items": dialog_manager.dialog_data.get(DialogDataKeys.FOUND_ITEMS, [])}

    # Add translated keys to data
    data.update(
        {
            "find_title": _("find_title"),
            "find_btn_group": _("find_btn_group"),
            "find_btn_teacher": _("find_btn_teacher"),
            "find_btn_classroom": _("find_btn_classroom"),
            "find_history": _("find_history"),
            "btn_back": _("btn_back"),
            "find_enter_group": _("find_enter_group"),
            "find_enter_teacher": _("find_enter_teacher"),
            "find_enter_classroom": _("find_enter_classroom"),
            "find_select_match": _("find_select_match"),
            "find_new_search": _("find_new_search"),
        }
    )

    if teacher_name := dialog_manager.dialog_data.get(DialogDataKeys.TEACHER_NAME):
        canonical = manager.resolve_canonical_teacher(teacher_name) or teacher_name
        schedule_info = await manager.get_teacher_schedule(canonical, current_date)
        data["result_text"] = format_teacher_schedule_text(schedule_info, lang=user_lang)
    elif classroom_number := dialog_manager.dialog_data.get(DialogDataKeys.CLASSROOM_NUMBER):
        schedule_info = await manager.get_classroom_schedule(classroom_number, current_date)
        data["result_text"] = format_classroom_schedule_text(schedule_info, lang=user_lang)

    return data


async def get_history_data(dialog_manager: DialogManager, **kwargs):
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    user_lang = await user_data_manager.get_user_language(user_id) if user_data_manager else "ru"
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)
    items = []
    if user_data_manager:
        for item_type in ("group", "teacher", "room"):
            for value in await user_data_manager.get_history(user_id, item_type):
                items.append(f"{item_type}:{value}")
    return {
        "history_items": items,
        "find_history_title": _("find_history_title"),
        "find_history_empty": _("find_history_empty"),
        "btn_back": _("btn_back"),
    }


async def on_history_selected(callback: CallbackQuery, widget: Any, manager: DialogManager, item_id: str):
    item_type, value = item_id.split(":", 1)
    if item_type == "group":
        await manager.start(Schedule.view, data={DialogDataKeys.GROUP: value}, mode=StartMode.RESET_STACK)
    elif item_type == "teacher":
        manager.dialog_data[DialogDataKeys.TEACHER_NAME] = value
        await manager.switch_to(FindMenu.view_result)
    elif item_type == "room":
        manager.dialog_data[DialogDataKeys.CLASSROOM_NUMBER] = value
        await manager.switch_to(FindMenu.view_result)


async def on_group_input(message: Message, message_input: MessageInput, manager: DialogManager):
    """Открывает расписание найденной группы, не меняя сохранённую группу пользователя."""
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    _ = manager.middleware_data.get("_", lambda k, **kw: k)
    query = (message.text or "").strip()
    if not query:
        await message.answer(_("find_group_not_found"))
        return

    normalized = normalize_group_name(query) or query.upper()
    found = timetable_manager.find_groups(normalized)
    if not found:
        found = timetable_manager.find_groups_fuzzy(normalized)
    if not found:
        await message.answer(_("find_group_not_found"))
        return

    if len(found) == 1:
        await manager.start(
            Schedule.view,
            data={DialogDataKeys.GROUP: found[0]},
            mode=StartMode.RESET_STACK,
        )
        return

    manager.dialog_data[DialogDataKeys.SEARCH_TYPE] = "group"
    manager.dialog_data[DialogDataKeys.FOUND_ITEMS] = found[:20]
    await manager.switch_to(FindMenu.select_item)


async def on_teacher_input(message: Message, message_input: MessageInput, manager: DialogManager):
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    _ = manager.middleware_data.get("_", lambda k, **kw: k)

    query = message.text.strip()

    # 1. Поиск "в лоб"
    found_teachers = timetable_manager.find_teachers(query)

    # 2. Если не найдено, пробуем транслитерацию (Ivanov -> Иванов)
    if not found_teachers and query.isascii():
        translit_query = transliterate_failed_search(query)
        found_teachers = timetable_manager.find_teachers(translit_query)

    if not found_teachers:
        await message.answer(_("find_teacher_not_found"))
        return

    manager.dialog_data[DialogDataKeys.SEARCH_TYPE] = "teacher"

    try:
        from core.analytics import track_feature_click

        if timetable_manager and hasattr(timetable_manager, "redis"):
            await track_feature_click(timetable_manager.redis, "teacher_search")
    except Exception:
        pass

    if len(found_teachers) == 1:
        manager.dialog_data[DialogDataKeys.TEACHER_NAME] = found_teachers[0]
        manager.dialog_data.pop(DialogDataKeys.CLASSROOM_NUMBER, None)
        # Сохраняем в историю
        user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
        if user_data_manager and message.from_user:
            await user_data_manager.add_to_history(message.from_user.id, "teacher", found_teachers[0])
        await manager.switch_to(FindMenu.view_result)
    else:
        manager.dialog_data[DialogDataKeys.FOUND_ITEMS] = found_teachers[:20]
        await manager.switch_to(FindMenu.select_item)


async def on_classroom_input(message: Message, message_input: MessageInput, manager: DialogManager):
    _ = manager.middleware_data.get("_", lambda k, **kw: k)
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")

    query = message.text.strip()
    # Normalize classroom (e.g. convert cyrillic letters if used, mainly number based though)
    # Most classrooms are numbers, but some have letters (e.g. 521a)
    found_classrooms = timetable_manager.find_classrooms(query)

    if not found_classrooms:
        await message.answer(_("find_classroom_not_found"))
        return

    manager.dialog_data[DialogDataKeys.SEARCH_TYPE] = "classroom"

    try:
        from core.analytics import track_feature_click

        if timetable_manager and hasattr(timetable_manager, "redis"):
            await track_feature_click(timetable_manager.redis, "classroom_search")
    except Exception:
        pass

    if len(found_classrooms) == 1:
        manager.dialog_data[DialogDataKeys.CLASSROOM_NUMBER] = found_classrooms[0]
        manager.dialog_data.pop(DialogDataKeys.TEACHER_NAME, None)
        # Сохраняем в историю
        user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
        if user_data_manager and message.from_user:
            await user_data_manager.add_to_history(message.from_user.id, "room", found_classrooms[0])
        await manager.switch_to(FindMenu.view_result)
    else:
        manager.dialog_data[DialogDataKeys.FOUND_ITEMS] = found_classrooms[:20]
        await manager.switch_to(FindMenu.select_item)


async def on_item_selected(callback: CallbackQuery, widget: Any, manager: DialogManager, item_id: str):
    search_type = manager.dialog_data.get(DialogDataKeys.SEARCH_TYPE)

    if search_type == "group":
        user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
        if user_data_manager and callback.from_user:
            await user_data_manager.add_to_history(callback.from_user.id, "group", item_id)
        await manager.start(
            Schedule.view,
            data={DialogDataKeys.GROUP: item_id},
            mode=StartMode.RESET_STACK,
        )
        return

    if search_type == "teacher":
        manager.dialog_data[DialogDataKeys.TEACHER_NAME] = item_id
        history_type = "teacher"
    elif search_type == "classroom":
        manager.dialog_data[DialogDataKeys.CLASSROOM_NUMBER] = item_id
        history_type = "room"
    else:
        history_type = None

    # Сохраняем в историю
    if history_type:
        user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
        if user_data_manager and callback.from_user:
            await user_data_manager.add_to_history(callback.from_user.id, history_type, item_id)

    await manager.switch_to(FindMenu.view_result)


async def on_find_date_shift(callback: CallbackQuery, button: Button, manager: DialogManager, days: int):
    current_date = date.fromisoformat(manager.dialog_data[DialogDataKeys.CURRENT_DATE_ISO])
    manager.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = (current_date + timedelta(days=days)).isoformat()


async def on_find_today_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    manager.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()


async def on_back_to_main_menu(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.done()


find_dialog = Dialog(
    Window(
        StaticMedia(path=SEARCH_IMAGE_PATH),
        Format("{find_title}"),
        Column(
            SwitchTo(
                Format("{find_btn_group}"),
                id="find_group_btn",
                state=FindMenu.enter_group,
            ),
            SwitchTo(
                Format("{find_btn_teacher}"),
                id=WidgetIds.FIND_TEACHER_BTN,
                state=FindMenu.enter_teacher,
            ),
            SwitchTo(
                Format("{find_btn_classroom}"),
                id=WidgetIds.FIND_CLASSROOM_BTN,
                state=FindMenu.enter_classroom,
            ),
            SwitchTo(Format("{find_history}"), id="find_history_btn", state=FindMenu.history),
        ),
        Button(
            Format("{btn_back}"),
            id=WidgetIds.BACK_TO_MAIN_SCHEDULE,
            on_click=on_back_to_main_menu,
        ),
        LinkPreview(is_disabled=True),
        state=FindMenu.choice,
        getter=get_find_data,
    ),
    Window(
        Format("{find_history_title}"),
        Select(
            Format("{item}"),
            id="history_select",
            item_id_getter=lambda item: item,
            items="history_items",
            on_click=on_history_selected,
        ),
        Format("{find_history_empty}", when=lambda data, w, m: not data.get("history_items")),
        SwitchTo(Format("{btn_back}"), id="history_back", state=FindMenu.choice),
        state=FindMenu.history,
        getter=get_history_data,
    ),
    Window(
        StaticMedia(path=SEARCH_IMAGE_PATH),
        Format("{find_enter_group}"),
        MessageInput(on_group_input),
        SwitchTo(Format("{btn_back}"), id="back_to_choice_group", state=FindMenu.choice),
        LinkPreview(is_disabled=True),
        state=FindMenu.enter_group,
        getter=get_find_data,
    ),
    Window(
        StaticMedia(path=TEACHER_IMAGE_PATH),
        Format("{find_enter_teacher}"),
        MessageInput(on_teacher_input),
        SwitchTo(Format("{btn_back}"), id=f"{WidgetIds.BACK_TO_CHOICE}_1", state=FindMenu.choice),
        LinkPreview(is_disabled=True),
        state=FindMenu.enter_teacher,
        getter=get_find_data,
    ),
    Window(
        StaticMedia(path=CLASSROOM_IMAGE_PATH),
        Format("{find_enter_classroom}"),
        MessageInput(on_classroom_input),
        SwitchTo(Format("{btn_back}"), id=f"{WidgetIds.BACK_TO_CHOICE}_2", state=FindMenu.choice),
        LinkPreview(is_disabled=True),
        state=FindMenu.enter_classroom,
        getter=get_find_data,
    ),
    Window(
        Format("{find_select_match}"),
        Column(
            Select(
                Format("{item}"),
                id=WidgetIds.SELECT_FOUND_ITEM,
                item_id_getter=lambda item: item,
                items=DialogDataKeys.FOUND_ITEMS,
                on_click=on_item_selected,
            )
        ),
        SwitchTo(
            Format("{btn_back}"),
            id=f"{WidgetIds.BACK_TO_CHOICE}_teacher",
            when=lambda data, w, m: m.dialog_data.get(DialogDataKeys.SEARCH_TYPE) == "teacher",
            state=FindMenu.enter_teacher,
        ),
        SwitchTo(
            Format("{btn_back}"),
            id=f"{WidgetIds.BACK_TO_CHOICE}_classroom",
            when=lambda data, w, m: m.dialog_data.get(DialogDataKeys.SEARCH_TYPE) == "classroom",
            state=FindMenu.enter_classroom,
        ),
        LinkPreview(is_disabled=True),
        state=FindMenu.select_item,
        getter=get_find_data,
        parse_mode="HTML",
    ),
    Window(
        Format("{result_text}"),
        Row(
            Button(
                Const("⏪"),
                id="find_prev_week",
                on_click=lambda c, b, m: on_find_date_shift(c, b, m, -7),
            ),
            Button(
                Const("◀️"),
                id="find_prev_day",
                on_click=lambda c, b, m: on_find_date_shift(c, b, m, -1),
            ),
            Button(Const("📅"), id="find_today", on_click=on_find_today_click),
            Button(
                Const("▶️"),
                id="find_next_day",
                on_click=lambda c, b, m: on_find_date_shift(c, b, m, 1),
            ),
            Button(
                Const("⏩"),
                id="find_next_week",
                on_click=lambda c, b, m: on_find_date_shift(c, b, m, 7),
            ),
        ),
        SwitchTo(
            Format("{find_new_search}"),
            id=f"{WidgetIds.BACK_TO_CHOICE}_3",
            state=FindMenu.choice,
        ),
        LinkPreview(is_disabled=True),
        state=FindMenu.view_result,
        getter=get_find_data,
        parse_mode="HTML",
    ),
)
