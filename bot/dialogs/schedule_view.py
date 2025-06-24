from datetime import date, timedelta, datetime
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager, StartMode
from aiogram_dialog.widgets.text import Format, Const
from aiogram_dialog.widgets.kbd import Button, Row, SwitchTo, Back

from .states import Schedule, MainMenu, SettingsMenu
from core.manager import TimetableManager
from bot.utils import format_schedule_text, format_full_week_text
from core.config import MOSCOW_TZ

# --- Getters ---
async def get_schedule_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    ctx = dialog_manager.current_context()

    if "group" not in ctx.dialog_data:
        ctx.dialog_data["group"] = dialog_manager.start_data.get("group")
        
    if not ctx.dialog_data.get("current_date_iso"):
        today_in_moscow = datetime.now(MOSCOW_TZ).date()
        ctx.dialog_data["current_date_iso"] = today_in_moscow.isoformat()

    current_date = date.fromisoformat(ctx.dialog_data["current_date_iso"])
    group = ctx.dialog_data.get("group", "N/A")

    day_info = manager.get_schedule_for_day(group, target_date=current_date)
    return { "schedule_text": format_schedule_text(day_info) }

async def get_full_week_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    ctx = dialog_manager.current_context()
    current_date = date.fromisoformat(ctx.dialog_data.get("current_date_iso", date.today().isoformat()))
    group = ctx.dialog_data.get("group")

    week_info = manager.get_week_type(current_date)
    if not week_info: return {"week_text": "Не удалось определить тип недели"}
    week_key, week_name = week_info

    full_schedule = manager._schedules.get(group, {})
    week_schedule = full_schedule.get(week_key, {})
    
    return { "week_text": format_full_week_text(week_schedule, f"{week_name} неделя") }

# --- Click Handlers ---
async def on_date_shift(callback: CallbackQuery, button: Button, manager: DialogManager, days: int):
    ctx = manager.current_context()
    current_date = date.fromisoformat(ctx.dialog_data.get("current_date_iso"))
    new_date = current_date + timedelta(days=days)
    ctx.dialog_data["current_date_iso"] = new_date.isoformat()

async def on_today_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    today_in_moscow = datetime.now(MOSCOW_TZ).date()
    manager.current_context().dialog_data["current_date_iso"] = today_in_moscow.isoformat()
    
async def on_change_group_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Запускает диалог смены группы со сбросом стека."""
    await manager.start(MainMenu.enter_group, mode=StartMode.RESET_STACK)
    
# --- ИЗМЕНЕНИЕ: Новый обработчик для перехода в настройки ---
async def on_settings_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    """
    Переключает на диалог настроек.
    Мы не используем RESET_STACK, чтобы можно было вернуться назад.
    """
    await manager.start(SettingsMenu.main)

# --- Dialogs ---
schedule_dialog = Dialog(
    Window(
        Format("{schedule_text}"),
        Row(
            Button(Const("⏪"), id="prev_week", on_click=lambda c, b, m: on_date_shift(c, b, m, -7)),
            Button(Const("◀️"), id="prev_day", on_click=lambda c, b, m: on_date_shift(c, b, m, -1)),
            Button(Const("📅"), id="today", on_click=on_today_click),
            Button(Const("▶️"), id="next_day", on_click=lambda c, b, m: on_date_shift(c, b, m, 1)),
            Button(Const("⏩"), id="next_week", on_click=lambda c, b, m: on_date_shift(c, b, m, 7)),
        ),
        Row(
            SwitchTo(Const("🗓️ Вся неделя"), id="full_week", state=Schedule.full_week_view),
            Button(Const("🔄 Сменить группу"), id="change_group", on_click=on_change_group_click),
            # --- ИЗМЕНЕНИЕ: Заменяем SwitchTo на Button с нашим обработчиком ---
            Button(Const("⚙️ Настройки"), id="settings", on_click=on_settings_click),
        ),
        state=Schedule.view,
        getter=get_schedule_data,
        parse_mode="HTML"
    ),
    Window(
        Format("{week_text}"),
        Back(Const("◀️ Назад")),
        state=Schedule.full_week_view,
        getter=get_full_week_data,
        parse_mode="HTML"
    )
)