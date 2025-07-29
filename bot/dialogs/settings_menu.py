from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.kbd import Button, Row, Back, Select, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

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
    
    reminders_status = settings.get(WidgetIds.LESSON_REMINDERS.value, False)
    reminder_time = settings.get("reminder_time_minutes", 20)

    return {
        "evening_status_text": get_status_text(settings.get(WidgetIds.EVENING_NOTIFY.value, False)),
        "morning_status_text": get_status_text(settings.get(WidgetIds.MORNING_SUMMARY.value, False)),
        "reminders_status_text": get_status_text(reminders_status),
        
        "are_reminders_enabled": reminders_status,
        
        "reminder_time_text": f"({reminder_time} мин. до начала)",
        
        "evening_button_text": get_button_text(settings.get(WidgetIds.EVENING_NOTIFY.value, False), "сводку на завтра"),
        "morning_button_text": get_button_text(settings.get(WidgetIds.MORNING_SUMMARY.value, False), "утреннюю сводку"),
        "reminders_button_text": get_button_text(reminders_status, "напоминания о парах"),
        
        "reminder_times": [30, 60, 90, 120],
        "current_reminder_time": reminder_time
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

async def on_time_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str):
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id
    selected_time = int(item_id)
    
    await user_data_manager.set_reminder_time(user_id, selected_time)
    await callback.answer(f"Время напоминания установлено на {selected_time} минут.")
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
        
        Row(
            Button(Format("{reminders_button_text}"), id=WidgetIds.LESSON_REMINDERS, on_click=on_toggle_setting),
            SwitchTo(Const("⏰ Время"), id="to_time_settings", state=SettingsMenu.reminders_time, when="are_reminders_enabled"),
        ),
        Format("{reminder_time_text}", when="are_reminders_enabled"),

        Button(Const("◀️ Назад"), id="back_to_schedule", on_click=on_back_click),
        state=SettingsMenu.main, getter=get_settings_data, parse_mode="HTML"
    ),
    Window(
        Const("Выберите, за сколько минут до первой пары присылать напоминание:"),
        Row(
            Select(
                Jinja(
                    "{% if item == current_reminder_time %}"
                    "✅ {{ item }} мин."
                    "{% else %}"
                    "{{ item }} мин."
                    "{% endif %}"
                ),
                id="select_reminder_time",
                item_id_getter=lambda item: str(item),
                items="reminder_times",
                on_click=on_time_selected
            )
        ),
        Back(Const("◀️ Назад")),
        state=SettingsMenu.reminders_time, getter=get_settings_data
    )
)
