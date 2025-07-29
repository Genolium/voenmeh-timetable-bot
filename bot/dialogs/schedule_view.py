from datetime import date, timedelta, datetime
from aiogram.types import CallbackQuery, ContentType
from aiogram_dialog import Dialog, Window, DialogManager, StartMode
from aiogram_dialog.widgets.text import Format, Const
from aiogram_dialog.widgets.kbd import Button, Row, SwitchTo, Back
from aiogram_dialog.widgets.media import StaticMedia

from .states import Schedule, MainMenu, SettingsMenu, FindMenu
from .constants import DialogDataKeys, WidgetIds
from core.manager import TimetableManager
from bot.text_formatters import (
    format_schedule_text, format_full_week_text, generate_dynamic_header
)
from core.config import MOSCOW_TZ, NO_LESSONS_IMAGE_PATH

async def get_schedule_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    ctx = dialog_manager.current_context()

    if DialogDataKeys.GROUP not in ctx.dialog_data:
        ctx.dialog_data[DialogDataKeys.GROUP] = dialog_manager.start_data.get(DialogDataKeys.GROUP)
        
    if not ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO):
        ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()

    current_date = date.fromisoformat(ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO])
    group = ctx.dialog_data.get(DialogDataKeys.GROUP, "N/A")

    day_info = manager.get_schedule_for_day(group, target_date=current_date)
    
    dynamic_header, progress_bar = generate_dynamic_header(day_info.get("lessons", []), current_date)

    return {
        "dynamic_header": dynamic_header,
        "progress_bar": progress_bar,
        "schedule_text": format_schedule_text(day_info),
        "has_lessons": bool(day_info.get("lessons"))
    }

async def get_full_week_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    ctx = dialog_manager.current_context()
    current_date = date.fromisoformat(ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO, date.today().isoformat()))
    group = ctx.dialog_data.get(DialogDataKeys.GROUP)

    week_info = manager.get_week_type(current_date)
    if not week_info: return {"week_text": "Не удалось определить тип недели"}
    week_key, week_name = week_info

    full_schedule = manager._schedules.get(group, {})
    week_schedule = full_schedule.get(week_key, {})
    
    return { "week_text": format_full_week_text(week_schedule, f"{week_name} неделя") }

async def on_date_shift(callback: CallbackQuery, button: Button, manager: DialogManager, days: int):
    ctx = manager.current_context()
    current_date = date.fromisoformat(ctx.dialog_data.get(DialogDataKeys.CURRENT_DATE_ISO))
    new_date = current_date + timedelta(days=days)
    ctx.dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = new_date.isoformat()

async def on_today_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    manager.current_context().dialog_data[DialogDataKeys.CURRENT_DATE_ISO] = datetime.now(MOSCOW_TZ).date().isoformat()
    
async def on_change_group_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(MainMenu.enter_group, mode=StartMode.RESET_STACK)
    
async def on_settings_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(SettingsMenu.main)

async def on_find_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(FindMenu.choice)

schedule_dialog = Dialog(
    Window(
        StaticMedia(
            path=NO_LESSONS_IMAGE_PATH,
            type=ContentType.PHOTO,
            when=lambda data, widget, manager: not data.get("has_lessons")
        ),
        Format("{dynamic_header}"),
        Format("{progress_bar}"),
        Format("{schedule_text}"),
        Row(
            Button(Const("⏪"), id=WidgetIds.PREV_WEEK, on_click=lambda c, b, m: on_date_shift(c, b, m, -7)),
            Button(Const("◀️"), id=WidgetIds.PREV_DAY, on_click=lambda c, b, m: on_date_shift(c, b, m, -1)),
            Button(Const("📅"), id=WidgetIds.TODAY, on_click=on_today_click),
            Button(Const("▶️"), id=WidgetIds.NEXT_DAY, on_click=lambda c, b, m: on_date_shift(c, b, m, 1)),
            Button(Const("⏩"), id=WidgetIds.NEXT_WEEK, on_click=lambda c, b, m: on_date_shift(c, b, m, 7)),
        ),
        Row(
            SwitchTo(Const("🗓️ Вся неделя"), id=WidgetIds.FULL_WEEK, state=Schedule.full_week_view),
            Button(Const("🔄 Сменить группу"), id=WidgetIds.CHANGE_GROUP, on_click=on_change_group_click),
            Button(Const("⚙️ Настройки"), id=WidgetIds.SETTINGS, on_click=on_settings_click),
        ),
        Button(Const("🔍 Поиск"), id=WidgetIds.FIND_BTN, on_click=on_find_click),
        state=Schedule.view, getter=get_schedule_data,
        parse_mode="HTML", disable_web_page_preview=True
    ),
    Window(
        Format("{week_text}"),
        Back(Const("◀️ Назад")),
        state=Schedule.full_week_view, getter=get_full_week_data,
        parse_mode="HTML", disable_web_page_preview=True
    )
)