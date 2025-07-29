from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.kbd import Button
from aiogram_dialog.widgets.text import Const, Format

from .states import SettingsMenu
from .constants import WidgetIds
from core.user_data import UserDataManager

def get_status_text(status: bool) -> str:
    return "✅ Включена" if status else "❌ Отключена"

def get_button_text(status: bool, action: str) -> str:
    return f"Отключить {action}" if status else f"Включить {action}"

async def get_settings_data(dialog_manager: DialogManager, **kwargs):
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    settings = await user_data_manager.get_user_settings(user_id)
    
    evening_status = settings.get(WidgetIds.EVENING_NOTIFY.value, False)
    morning_status = settings.get(WidgetIds.MORNING_SUMMARY.value, False)
    reminders_status = settings.get(WidgetIds.LESSON_REMINDERS.value, False)

    return {
        "evening_status_text": get_status_text(evening_status),
        "morning_status_text": get_status_text(morning_status),
        "reminders_status_text": get_status_text(reminders_status),
        "evening_button_text": get_button_text(evening_status, "сводку на завтра"),
        "morning_button_text": get_button_text(morning_status, "утреннюю сводку"),
        "reminders_button_text": get_button_text(reminders_status, "напоминания о парах"),
    }

async def on_toggle_setting(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id
    setting_name = button.widget_id
    
    current_settings = await user_data_manager.get_user_settings(user_id)
    current_status = current_settings.get(setting_name, False)
    
    new_status = not current_status
    await user_data_manager.update_setting(user_id, setting_name, new_status)
    await callback.answer("Настройка обновлена.")
    await manager.switch_to(SettingsMenu.main)

async def on_back_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.done()

settings_dialog = Dialog(
    Window(
        Const("⚙️ <b>Настройки уведомлений</b>\n"),
        Format(f"Сводка на завтра (20:00): <b>{{evening_status_text}}</b>"),
        Format(f"Сводка на сегодня (8:00): <b>{{morning_status_text}}</b>"),
        Format(f"Напоминания о парах: <b>{{reminders_status_text}}</b>"),
        Button(Format("{evening_button_text}"), id=WidgetIds.EVENING_NOTIFY, on_click=on_toggle_setting),
        Button(Format("{morning_button_text}"), id=WidgetIds.MORNING_SUMMARY, on_click=on_toggle_setting),
        Button(Format("{reminders_button_text}"), id=WidgetIds.LESSON_REMINDERS, on_click=on_toggle_setting),
        Button(Const("◀️ Назад"), id="back_to_schedule", on_click=on_back_click),
        state=SettingsMenu.main, getter=get_settings_data, parse_mode="HTML"
    )
)