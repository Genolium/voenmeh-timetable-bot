import logging
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.kbd import Button, Row, Back, Select, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

from .states import SettingsMenu
from .constants import WidgetIds
from core.user_data import UserDataManager
from bot.scheduler import cancel_reminders_for_user, plan_reminders_for_user
# theme_dialog импортируется в main.py

def get_status_text(status: bool) -> str:
    return "✅ Включена" if status else "❌ Отключена"

def get_button_text(status: bool, action: str) -> str:
    return f"Отключить {action}" if status else f"Включить {action}"

async def get_settings_data(dialog_manager: DialogManager, **kwargs):
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    settings = await user_data_manager.get_user_settings(user_id)

    reminders_status = settings.get(WidgetIds.LESSON_REMINDERS.value, False)
    reminder_time = settings.get("reminder_time_minutes", 60)
    current_theme = settings.get("theme", "standard")

    # Определяем названия тем с эмодзи
    themes_info = {
        'standard': '🎨 Стандартная',
        'light': '☀️ Светлая',
        'dark': '🌙 Тёмная',
        'classic': '📜 Классическая',
        'coffee': '☕ Кофейная'
    }

    current_theme_name = themes_info.get(current_theme, '🎨 Стандартная')

    return {
        "evening_status_text": get_status_text(settings.get(WidgetIds.EVENING_NOTIFY.value, False)),
        "morning_status_text": get_status_text(settings.get(WidgetIds.MORNING_SUMMARY.value, False)),
        "reminders_status_text": get_status_text(reminders_status),

        "are_reminders_enabled": reminders_status,

        "reminder_time_text": f"({reminder_time} мин. до начала)",

        "evening_button_text": get_button_text(settings.get(WidgetIds.EVENING_NOTIFY.value, False), "сводку на завтра"),
        "morning_button_text": get_button_text(settings.get(WidgetIds.MORNING_SUMMARY.value, False), "утреннюю сводку"),
        "reminders_button_text": get_button_text(reminders_status, "напоминания о парах"),

        "current_theme_name": current_theme_name,
        "current_theme": current_theme,

        "reminder_times": [30, 60, 90, 120],
        "current_reminder_time": reminder_time
    }

async def on_toggle_setting(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    scheduler = manager.middleware_data.get("scheduler")
    timetable_manager = manager.middleware_data.get("manager")
    user_id = callback.from_user.id
    setting_name = button.widget_id
    
    current_settings = await user_data_manager.get_user_settings(user_id)
    current_status = current_settings.get(setting_name, False)
    
    new_status = not current_status
    await user_data_manager.update_setting(user_id, setting_name, new_status)

    # Управляем ближайшими задачами для напоминаний о парах
    if setting_name == WidgetIds.LESSON_REMINDERS and scheduler and timetable_manager:
        if not new_status:
            await cancel_reminders_for_user(scheduler, user_id)
        else:
            await cancel_reminders_for_user(scheduler, user_id)
            await plan_reminders_for_user(scheduler, user_data_manager, timetable_manager, user_id)

    await callback.answer("Настройка обновлена.")
    await manager.switch_to(SettingsMenu.main)

async def on_time_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str):
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    scheduler = manager.middleware_data.get("scheduler")
    timetable_manager = manager.middleware_data.get("manager")
    user_id = callback.from_user.id
    selected_time = int(item_id)
    
    await user_data_manager.set_reminder_time(user_id, selected_time)

    # Перепланировать с новым окном
    if scheduler and timetable_manager:
        await cancel_reminders_for_user(scheduler, user_id)
        await plan_reminders_for_user(scheduler, user_data_manager, timetable_manager, user_id)

    await callback.answer(f"Время напоминания установлено на {selected_time} минут.")
    await manager.switch_to(SettingsMenu.main)

async def on_back_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.done()


async def on_theme_button_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Обработчик кнопки выбора темы с проверкой подписки."""
    user_id = callback.from_user.id

    # Проверяем подписку перед переходом к диалогу тем
    try:
        from core.config import SUBSCRIPTION_CHANNEL, get_redis_client
        from aiogram import Bot

        redis_client = await get_redis_client()
        cache_key = f"theme_sub_status:{user_id}"
        cached = await redis_client.get(cache_key)

        is_subscribed = False
        if cached is not None:
            is_subscribed = cached == '1'
        else:
            # Проверяем напрямую через API
            if SUBSCRIPTION_CHANNEL:
                bot: Bot = manager.middleware_data.get("bot")
                if bot:
                    member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                    status = getattr(member, "status", None)
                    is_subscribed = status in ("member", "administrator", "creator")

                    # Кэшируем результат
                    await redis_client.set(cache_key, '1' if is_subscribed else '0', ex=21600 if is_subscribed else 60)

        if not is_subscribed and SUBSCRIPTION_CHANNEL:
            # Пользователь не подписан, показываем уведомление
            from bot.tasks import check_theme_subscription_task
            check_theme_subscription_task.send(user_id, callback.id)
            await callback.answer("❌ Требуется подписка на канал для доступа к темам", show_alert=True)
            return

        # Пользователь подписан, переходим к диалогу тем
        await manager.switch_to(SettingsMenu.choose_theme)

    except Exception as e:
        # В случае ошибки (например, Redis недоступен) проверяем подписку через API
        logging.warning(f"Error checking theme subscription cache: {e}")

        if SUBSCRIPTION_CHANNEL:
            try:
                bot: Bot = manager.middleware_data.get("bot")
                if bot:
                    member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                    status = getattr(member, "status", None)
                    is_subscribed = status in ("member", "administrator", "creator")

                    if not is_subscribed:
                        # Пользователь не подписан, показываем уведомление
                        from bot.tasks import check_theme_subscription_task
                        check_theme_subscription_task.send(user_id, callback.id)
                        await callback.answer("❌ Требуется подписка на канал для доступа к темам", show_alert=True)
                        return

            except Exception as api_error:
                logging.warning(f"Error checking theme subscription via API: {api_error}")
                # Даже если API проверка не удалась, показываем уведомление о подписке
                from bot.tasks import check_theme_subscription_task
                check_theme_subscription_task.send(user_id, callback.id)
                await callback.answer("❌ Требуется подписка на канал для доступа к темам", show_alert=True)
                return

        # Если подписка не настроена или пользователь подписан, переходим к диалогу тем
        await manager.switch_to(SettingsMenu.choose_theme)

async def on_news_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Открывает канал с новостями разработки"""
    await callback.answer("📢 Переходим к новостям разработки!")
    await callback.message.answer(
        "🚀 <b>Новости разработки бота</b>\n\n"
        "Все обновления, планы и новости о разработке бота публикуются в нашем канале:\n\n"
        "📢 <a href='https://t.me/voenmeh404'>Аудитория 404 | Военмех</a>\n\n"
        "Там вы узнаете:\n"
        "• О новых функциях первыми\n"
        "• О планах развития\n"
        "• Сможете повлиять на разработку\n"
        "• Увидите закулисье проекта\n\n"
        "<i>Подписывайтесь, чтобы быть в курсе! 👆</i>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

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

        Row(
            Button(Format("🎨 Тема: {current_theme_name}"), id="theme_btn", on_click=on_theme_button_click),
            Button(Const("📢 Новости разработки"), id="news_btn", on_click=on_news_clicked),
        ),
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
