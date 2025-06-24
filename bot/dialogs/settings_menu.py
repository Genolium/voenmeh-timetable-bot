# voenmeh_bot/bot/dialogs/settings_menu.py
import logging
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.kbd import Button
from aiogram_dialog.widgets.text import Const, Format

from .states import SettingsMenu
from core.user_data import UserDataManager

# --- Утилиты для этого файла ---
def get_status_text(status: bool) -> str:
    return "✅ Включена" if status else "❌ Отключена"

def get_button_text(status: bool, action: str) -> str:
    return f"Отключить {action}" if status else f"Включить {action}"


# --- Getters ---
async def get_settings_data(dialog_manager: DialogManager, **kwargs):
    """
    Загружает актуальные настройки пользователя из БД и формирует
    данные для отображения текста и кнопок.
    """
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    settings = await user_data_manager.get_user_settings(user_id)
    
    evening_status = bool(settings.get("evening_notify"))
    morning_status = bool(settings.get("morning_summary"))
    reminders_status = bool(settings.get("lesson_reminders"))

    # Этот словарь будет использоваться для отрисовки текста и кнопок
    return {
        "evening_status_text": get_status_text(evening_status),
        "morning_status_text": get_status_text(morning_status),
        "reminders_status_text": get_status_text(reminders_status),
        
        "evening_button_text": get_button_text(evening_status, "сводку на завтра"),
        "morning_button_text": get_button_text(morning_status, "утреннюю сводку"),
        "reminders_button_text": get_button_text(reminders_status, "напоминания о парах"),
    }

# --- Click Handlers ---
async def on_toggle_setting(callback: CallbackQuery, button: Button, manager: DialogManager):
    """
    Универсальный обработчик для всех кнопок-переключателей.
    Он не зависит от `dialog_data`, а работает напрямую с БД.
    """
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id
    
    # 1. Определяем, какую настройку меняем, по ID кнопки
    setting_name = button.widget_id
    
    # 2. Получаем ТЕКУЩЕЕ состояние настройки НАПРЯМУЮ из БД
    current_settings = await user_data_manager.get_user_settings(user_id)
    current_status = bool(current_settings.get(setting_name))
    
    # 3. Инвертируем статус и сохраняем новое значение в БД
    new_status = not current_status
    await user_data_manager.update_setting(user_id, setting_name, new_status)
    
    # 4. Всплывающее уведомление для пользователя
    await callback.answer(f"Настройка обновлена.")
    
    # 5. Принудительно обновляем окно, чтобы перерисовать его с новыми данными из БД
    new_data_for_render = await get_settings_data(manager)
    await manager.update(new_data_for_render)


async def on_back_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Завершает диалог настроек и возвращает к предыдущему экрану."""
    await manager.done()

# --- Dialog Window ---
settings_dialog = Dialog(
    Window(
        Const("⚙️ <b>Настройки уведомлений</b>\n"),
        
        Format("Сводка на завтра (20:00): <b>{evening_status_text}</b>"),
        Format("Сводка на сегодня (8:00): <b>{morning_status_text}</b>"),
        Format("Напоминания о парах: <b>{reminders_status_text}</b>"),
        
        Button(
            Format("{evening_button_text}"),
            id="evening_notify",
            on_click=on_toggle_setting
        ),
        Button(
            Format("{morning_button_text}"),
            id="morning_summary",
            on_click=on_toggle_setting
        ),
        Button(
            Format("{reminders_button_text}"),
            id="lesson_reminders",
            on_click=on_toggle_setting
        ),

        Button(Const("◀️ Назад"), id="back_to_schedule", on_click=on_back_click),
        state=SettingsMenu.main,
        getter=get_settings_data,
        parse_mode="HTML"
    )
)