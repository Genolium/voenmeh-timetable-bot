import logging
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.kbd import Button, Row, Back, Select, SwitchTo, Column
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

    # Проверяем подписку перед переходом к диалогу тем (как в оригинале в расписании)
    try:
        from core.config import SUBSCRIPTION_CHANNEL
        from aiogram import Bot

        if SUBSCRIPTION_CHANNEL:
            bot: Bot = manager.middleware_data.get("bot")
            if bot:
                member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                status = getattr(member, "status", None)
                is_subscribed = status in ("member", "administrator", "creator")

                if not is_subscribed:
                    # Переводим в окно-гейт и отправляем ссылку
                    await manager.switch_to(SettingsMenu.theme_subscription_gate)
                    channel_link = SUBSCRIPTION_CHANNEL
                    if channel_link.startswith('@'):
                        channel_link = f"https://t.me/{channel_link[1:]}"
                    elif channel_link.startswith('-'):
                        channel_link = f"tg://resolve?domain={channel_link}"
                    elif not channel_link.startswith('http'):
                        channel_link = f"https://t.me/{channel_link}"
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=channel_link)]])
                    try:
                        await callback.message.answer("Доступ к персональным темам доступен по подписке на канал.", reply_markup=kb)
                        await callback.answer()
                    except Exception:
                        pass
                    return

        # Подписка подтверждена или не требуется — переходим к выбору тем
        await manager.switch_to(SettingsMenu.choose_theme)

    except Exception:
        # При ошибках продолжаем, не блокируем пользователя
        await manager.switch_to(SettingsMenu.choose_theme)

async def on_theme_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str):
    """Сохраняет выбранную тему пользователя и возвращает в меню настроек."""
    try:
        user_id = callback.from_user.id
        user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
        if user_data_manager:
            # Гарантируем наличие пользователя в БД
            try:
                await user_data_manager.register_user(user_id, getattr(callback.from_user, "username", None))
            except Exception:
                pass
            await user_data_manager.set_user_theme(user_id, item_id)
            await callback.answer("Тема обновлена ✅")
        else:
            await callback.answer("Не удалось сохранить тему", show_alert=True)
    except Exception:
        try:
            await callback.answer("Ошибка при сохранении темы", show_alert=True)
        except Exception:
            pass
    await manager.switch_to(SettingsMenu.main)

async def get_theme_data(dialog_manager: DialogManager, **kwargs):
    """Возвращает текущую тему и список тем с пометкой текущей."""
    themes_info = {
        'standard': {'name': '🎨 Стандартная', 'description': 'красная для нечётных недель, фиолетовая для чётных'},
        'light': {'name': '☀️ Светлая', 'description': 'бирюзовая тема с кремовыми карточками'},
        'dark': {'name': '🌙 Тёмная', 'description': 'тёмная тема с фиолетовыми акцентами'},
        'classic': {'name': '📜 Классическая', 'description': 'тёмно-синяя тема в цветовой гамме Военмеха'},
        'coffee': {'name': '☕ Кофейная', 'description': 'коричнево-золотая тема с кремовыми карточками'},
    }
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    current_theme = 'standard'
    try:
        if user_data_manager:
            current_theme = await user_data_manager.get_user_theme(user_id) or 'standard'
    except Exception:
        current_theme = 'standard'

    themes = []
    for key, info in themes_info.items():
        themes.append({
            'id': key,
            'name': info['name'],
            'description': info['description'],
            'is_current': key == current_theme,
        })

    return {
        'current_theme': themes_info.get(current_theme, {'name': '🎨 Стандартная'})['name'],
        'themes': themes,
    }
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

        Button(Format("🎨 Тема: {current_theme_name}"), id="theme_btn", on_click=on_theme_button_click),
        Button(Const("📢 Новости разработки"), id="news_btn", on_click=on_news_clicked),
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
    ),
    # Добавляем окна из theme_dialog
    Window(
        Const("🎨 <b>Доступ к персональным темам</b>\n\n"
              "Выберите уникальную тему для вашего расписания:\n\n"
              "🎨 <b>Стандартная</b> - красная для нечётных, фиолетовая для чётных недель\n"
              "☀️ <b>Светлая</b> - бирюзовая тема с кремовыми карточками\n"
              "🌙 <b>Тёмная</b> - тёмная тема с фиолетовыми акцентами\n"
              "📜 <b>Классическая</b> - тёмно-синяя тема с белыми карточками\n"
              "☕ <b>Кофейная</b> - коричнево-золотая тема с кремовыми карточками\n\n"
              "<i>Доступно только по подписке на канал разработки</i>"),
        Button(Const("✅ Проверить подписку"), id="check_subscription", on_click=lambda c, b, m: m.switch_to(SettingsMenu.choose_theme)),
        Back(Const("◀️ Назад"), on_click=lambda c, b, m: m.switch_to(SettingsMenu.main)),
        state=SettingsMenu.theme_subscription_gate,
        parse_mode="HTML"
    ),
    Window(
        Const("🎨 <b>Выбор темы оформления</b>\n\n"
              "Выберите тему для вашего расписания:\n"),
        Format("Текущая тема: <b>{current_theme}</b>\n"),
        Const("\n📋 <i>Доступные темы:</i>\n"),
        Column(
            Select(
                Jinja(
                    "{% if item.is_current %}"
                    "✅ {{ item.name }}"
                    "{% else %}"
                    "🔘 {{ item.name }}"
                    "{% endif %}"
                ),
                id="select_theme",
                item_id_getter=lambda item: item['id'],
                items="themes",
                on_click=on_theme_selected
            )
        ),
        Back(Const("◀️ Назад"), on_click=lambda c, b, m: m.switch_to(SettingsMenu.main)),
        state=SettingsMenu.choose_theme,
        getter=get_theme_data,
        parse_mode="HTML"
    )
)
