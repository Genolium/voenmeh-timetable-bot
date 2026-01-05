import logging
from typing import Any

from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.kbd import Back, Button, Column, Row, Select, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

from bot.scheduler import cancel_reminders_for_user, plan_reminders_for_user
from core.user_data import UserDataManager
from core.i18n import i18n

from .constants import WidgetIds
from .states import SettingsMenu

# theme_dialog импортируется в main.py


def get_status_text(status: bool, lang: str = "ru") -> str:
    key = "settings_status_enabled" if status else "settings_status_disabled"
    return i18n.get(key, lang)


def get_button_text(status: bool, action_key: str, lang: str = "ru") -> str:
    # action_key: "evening", "morning", "reminders"
    prefix = "settings_btn_disable" if status else "settings_btn_enable"
    return i18n.get(f"{prefix}_{action_key}", lang)


async def get_settings_data(dialog_manager: DialogManager, **kwargs):
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    settings = await user_data_manager.get_user_settings(user_id)

    # CRITICAL: Fetch user_lang and define _ FIRST before any usage
    user_lang = await user_data_manager.get_user_language(user_id)
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    reminders_status = settings.get(WidgetIds.LESSON_REMINDERS.value, False)
    reminder_time = settings.get("reminder_time_minutes", 60)
    # Normalize theme ID
    current_theme = str(settings.get("theme", "standard")).lower()

    # Определяем названия тем с эмодзи из локалей
    themes_info = {
        "standard": _("theme_standard_name"),
        "light": _("theme_light_name"),
        "dark": _("theme_dark_name"),
        "classic": _("theme_classic_name"),
        "coffee": _("theme_coffee_name"),
    }

    # Безопасное получение имени темы
    current_theme_name = themes_info.get(current_theme)
    if not current_theme_name:
        # Если тема не найдена (например старая база), используем стандартную
        current_theme_name = _("theme_standard_name")
    
    language_map = {
        "ru": _("lang_ru"),
        "en": _("lang_en"),
        "zh": _("lang_zh")
    }
    lang_name = language_map.get(user_lang, user_lang)

    # Localized labels for status lines
    settings_evening_label = _("settings_evening_label")
    settings_morning_label = _("settings_morning_label")
    settings_reminders_label = _("settings_reminders_label")

    return {
        "settings_title": _("settings_title", language=lang_name),
        "btn_settings": _("btn_settings"),
        "btn_change_lang": _("btn_change_lang"),
        "lang_select_title": _("lang_select_title"),
        
        "settings_evening_label": settings_evening_label,
        "settings_morning_label": settings_morning_label,
        "settings_reminders_label": settings_reminders_label,
        
        "evening_status_text": get_status_text(settings.get(WidgetIds.EVENING_NOTIFY.value, False), user_lang),
        "morning_status_text": get_status_text(settings.get(WidgetIds.MORNING_SUMMARY.value, False), user_lang),
        "reminders_status_text": get_status_text(reminders_status, user_lang),
        "are_reminders_enabled": reminders_status,
        "reminder_time_text": _("settings_reminder_time_fmt", minutes=reminder_time),
        "evening_button_text": get_button_text(settings.get(WidgetIds.EVENING_NOTIFY.value, False), "evening", user_lang),
        "morning_button_text": get_button_text(settings.get(WidgetIds.MORNING_SUMMARY.value, False), "morning", user_lang),
        "reminders_button_text": get_button_text(reminders_status, "reminders", user_lang),
        "current_theme_name": current_theme_name,
        "current_theme": current_theme,
        "reminder_times": [30, 60, 90, 120],
        "current_reminder_time": reminder_time,
        "settings_minutes_short": _("settings_minutes_short"),
        
        "lang_ru": _("lang_ru"),
        "lang_en": _("lang_en"),
        "lang_zh": _("lang_zh"),
        "btn_news": _("btn_news"),
        "btn_back": _("btn_back"),
        "btn_time": _("btn_time"),
        "settings_reminder_select_title": _("settings_reminder_select_title"),
        "current_lang_code": user_lang,
        "ru_checked": "✅" if user_lang == "ru" else "",
        "en_checked": "✅" if user_lang == "en" else "",
        "zh_checked": "✅" if user_lang == "zh" else "",
        "languages": [("ru", _("lang_ru")), ("en", _("lang_en")), ("zh", _("lang_zh"))],
        
        # Theme dialog keys
        "theme_gate_title": _("theme_gate_title"),
        "theme_btn_check": _("theme_btn_check"),
        "theme_select_title": _("theme_select_title"),
        "theme_current_fmt": _("theme_current_fmt", current_theme=current_theme_name),
        "theme_current_fmt": _("theme_current_fmt", current_theme=current_theme_name),
        "theme_available_label": _("theme_available_label"),
        "theme_button_text": _("menu_btn_theme", theme=current_theme_name),
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


async def on_lang_selected(callback: CallbackQuery, widget: Any, manager: DialogManager, item_id: str):
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    await user_data_manager.set_user_language(callback.from_user.id, item_id)
    
    # Reload middleware I18n context manually or just reply
    _ = lambda k: i18n.get(k, lang=item_id)
    
    await callback.answer(_("lang_changed"), show_alert=True)
    await manager.switch_to(SettingsMenu.main)


async def on_theme_button_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Обработчик кнопки выбора темы с проверкой подписки."""
    user_id = callback.from_user.id

    # Проверяем подписку перед переходом к диалогу тем (как в оригинале в расписании)
    try:
        from aiogram import Bot

        from core.config import SUBSCRIPTION_CHANNEL

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
                    if channel_link.startswith("@"):
                        channel_link = f"https://t.me/{channel_link[1:]}"
                    elif channel_link.startswith("-"):
                        channel_link = f"tg://resolve?domain={channel_link}"
                    elif not channel_link.startswith("http"):
                        channel_link = f"https://t.me/{channel_link}"
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=channel_link)]])
                    try:
                        await callback.message.answer(
                            "Доступ к персональным темам доступен по подписке на канал.",
                            reply_markup=kb,
                        )
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
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    
    # CRITICAL: Fetch user_lang and define _ FIRST before any usage
    user_lang = await user_data_manager.get_user_language(user_id) if user_data_manager else "ru"
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)
    
    current_theme = "standard"
    try:
        if user_data_manager:
            current_theme = await user_data_manager.get_user_theme(user_id) or "standard"
    except Exception:
        current_theme = "standard"

    themes_info = {
        "standard": {
            "name": _("theme_standard_name"),
            "description": _("theme_standard_desc"),
        },
        "light": {
            "name": _("theme_light_name"),
            "description": _("theme_light_desc"),
        },
        "dark": {
            "name": _("theme_dark_name"),
            "description": _("theme_dark_desc"),
        },
        "classic": {
            "name": _("theme_classic_name"),
            "description": _("theme_classic_desc"),
        },
        "coffee": {
            "name": _("theme_coffee_name"),
            "description": _("theme_coffee_desc"),
        },
    }

    themes = []
    for key, info in themes_info.items():
        themes.append(
            {
                "id": key,
                "name": info["name"],
                "description": info["description"],
                "is_current": key == current_theme,
            }
        )

    current_theme_name = themes_info.get(current_theme, {"name": _("theme_standard_name")})["name"]
    return {
        "current_theme": current_theme_name,
        "themes": themes,
        "btn_back": _("btn_back"),
        "theme_select_title": _("theme_select_title"),
        "theme_current_fmt": _("theme_current_fmt", current_theme=current_theme_name),
        "theme_available_label": _("theme_available_label"),
    }


async def on_news_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Открывает канал с новостями разработки"""
    # Force fetch language from DB to ensure correct localization even if middleware is stale
    try:
        user_data_manager = manager.middleware_data.get("user_data_manager")
        user_id = callback.from_user.id
        user_lang = await user_data_manager.get_user_language(user_id) or "ru"
    except Exception:
        user_lang = manager.middleware_data.get("i18n_lang", "ru")

    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)
    await callback.answer(_("news_toast"))
    await callback.message.answer(
        _("news_message"),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


settings_dialog = Dialog(
    Window(
        Format("{settings_title}"),
        Format("{settings_evening_label}: <b>{evening_status_text}</b>"),
        Format("{settings_morning_label}: <b>{morning_status_text}</b>"),
        Format("{settings_reminders_label}: <b>{reminders_status_text}</b>"),
        Button(
            Format("{evening_button_text}"),
            id=WidgetIds.EVENING_NOTIFY,
            on_click=on_toggle_setting,
        ),
        Button(
            Format("{morning_button_text}"),
            id=WidgetIds.MORNING_SUMMARY,
            on_click=on_toggle_setting,
        ),
        Row(
            Button(
                Format("{reminders_button_text}"),
                id=WidgetIds.LESSON_REMINDERS,
                on_click=on_toggle_setting,
            ),
            SwitchTo(
                Format("{btn_time}"),
                id="to_time_settings",
                state=SettingsMenu.reminders_time,
                when="are_reminders_enabled",
            ),
        ),
        Format("{reminder_time_text}", when="are_reminders_enabled"),
        Button(
            Format("{theme_button_text}"),
            id="theme_btn",
            on_click=on_theme_button_click,
        ),
        Button(
            Format("{btn_change_lang}"),
            id="lang_btn",
            on_click=lambda c, b, m: m.switch_to(SettingsMenu.select_lang),
        ),
        Button(Format("{btn_news}"), id="news_btn", on_click=on_news_clicked),
        Button(Format("{btn_back}"), id="back_to_schedule", on_click=on_back_click),
        state=SettingsMenu.main,
        getter=get_settings_data,
        parse_mode="HTML",
    ),
    Window(
        Format("{settings_reminder_select_title}"),
        Row(
            Select(
                Jinja(
                    "{% if item == current_reminder_time %}✅ {{ item }} {{ settings_minutes_short }}{% else %}{{ item }} {{ settings_minutes_short }}{% endif %}"
                ),
                id="select_reminder_time",
                item_id_getter=lambda item: str(item),
                items="reminder_times",
                on_click=on_time_selected,
            )
        ),
        Back(Format("{btn_back}")),
        state=SettingsMenu.reminders_time,
        getter=get_settings_data,
    ),
    Window(
        Format("{lang_select_title}"),
        Column(
             Button(
                 Format("🇷🇺 Русский {ru_checked}"),
                 id="lang_ru",
                 on_click=lambda c, b, m: on_lang_selected(c, b, m, "ru"),
             ),
             Button(
                 Format("🇬🇧 English {en_checked}"),
                 id="lang_en",
                 on_click=lambda c, b, m: on_lang_selected(c, b, m, "en"),
             ),
             Button(
                 Format("🇨🇳 中文 {zh_checked}"),
                 id="lang_zh",
                 on_click=lambda c, b, m: on_lang_selected(c, b, m, "zh"),
             ),
        ),
        SwitchTo(Format("{btn_back}"), id="back_from_lang", state=SettingsMenu.main),
        state=SettingsMenu.select_lang,
        getter=get_settings_data,
        parse_mode="HTML",
    ),
    # Добавляем окна из theme_dialog
    Window(
        Format("{theme_gate_title}"),
        Button(
            Format("{theme_btn_check}"),
            id="check_subscription",
            on_click=lambda c, b, m: m.switch_to(SettingsMenu.choose_theme),
        ),
        Button(
            Format("{btn_back}"),
            id="back_to_main_from_gate",
            on_click=lambda c, b, m: m.switch_to(SettingsMenu.main),
        ),
        state=SettingsMenu.theme_subscription_gate,
        getter=get_settings_data,
        parse_mode="HTML",
    ),
    Window(
        Format("{theme_select_title}"),
        Format("{theme_current_fmt}"),
        Format("{theme_available_label}"),
        Column(
            Select(
                Jinja("{% if item.is_current %}" "✅ {{ item.name }}" "{% else %}" "🔘 {{ item.name }}" "{% endif %}"),
                id="select_theme",
                item_id_getter=lambda item: item["id"],
                items="themes",
                on_click=on_theme_selected,
            )
        ),
        Button(
            Format("{btn_back}"),
            id="back_to_main_from_theme",
            on_click=lambda c, b, m: m.switch_to(SettingsMenu.main),
        ),
        state=SettingsMenu.choose_theme,
        getter=get_theme_data,
        parse_mode="HTML",
    ),
)
