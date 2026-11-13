import logging
from typing import Any, cast

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.kbd import Back, Button, Column, Row, Select, SwitchTo
from aiogram_dialog.widgets.text import Const, Format, Jinja

from core.i18n import i18n
from core.themes import DEFAULT_THEME, THEMES, get_theme_meta
from core.user_data import UserDataManager

from .constants import WidgetIds
from .states import MainMenu, SettingsMenu


def get_status_text(status: bool, lang: str = "ru") -> str:
    key = "settings_status_enabled" if status else "settings_status_disabled"
    return cast(str, i18n.get(key, lang))


def get_button_text(status: bool, action_key: str, lang: str = "ru") -> str:
    # action_key: "evening", "morning", "reminders"
    prefix = "settings_btn_disable" if status else "settings_btn_enable"
    return cast(str, i18n.get(f"{prefix}_{action_key}", lang))


async def get_settings_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    settings = await user_data_manager.get_user_settings(user_id)

    # CRITICAL: Fetch user_lang and define _ FIRST before any usage
    user_lang = await user_data_manager.get_user_language(user_id)
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    reminders_status = settings.get(WidgetIds.LESSON_REMINDERS.value, False)
    reminder_time = settings.get("reminder_time_minutes", 20)
    # Normalize theme ID
    current_theme = str(settings.get("theme", DEFAULT_THEME)).lower()
    current_meta = get_theme_meta(current_theme)
    current_theme_name = _(current_meta.name_key)

    language_map = {"ru": _("lang_ru"), "en": _("lang_en"), "zh": _("lang_zh")}
    lang_name = language_map.get(user_lang, user_lang)

    # Localized labels for status lines
    settings_evening_label = _("settings_evening_label", time=settings.get("evening_time", "20:00"))
    settings_morning_label = _("settings_morning_label", time=settings.get("morning_time", "08:00"))
    settings_reminders_label = _("settings_reminders_label")

    settings_minutes_short = _("settings_minutes_short")

    # Morning times: 6:00 to 12:00
    morning_times = [f"{h:02d}:00" for h in range(6, 13)]
    # Evening times: 16:00 to 23:00
    evening_times = [f"{h:02d}:00" for h in range(16, 24)]

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
        "reminder_times": [10, 15, 20, 30, 45, 60, 90, 120],
        "current_reminder_time": reminder_time,
        "settings_minutes_short": settings_minutes_short,
        "morning_times": morning_times,
        "evening_times": evening_times,
        "current_morning_time": settings.get("morning_time", "08:00"),
        "current_evening_time": settings.get("evening_time", "20:00"),
        "btn_time": _("btn_time"),  # For reminders
        "btn_morning_time": settings.get("morning_time", "08:00"),
        "btn_evening_time": settings.get("evening_time", "20:00"),
        "is_morning_enabled": settings.get(WidgetIds.MORNING_SUMMARY.value, False),
        "is_evening_enabled": settings.get(WidgetIds.EVENING_NOTIFY.value, False),
        "schedule_diff_status_text": get_status_text(settings.get(WidgetIds.SCHEDULE_DIFF_NOTIFY.value, True), user_lang),
        "schedule_diff_button_text": get_button_text(
            settings.get(WidgetIds.SCHEDULE_DIFF_NOTIFY.value, True), "diff", user_lang
        ),
        "settings_diff_label": _("settings_diff_label"),
        "btn_change_role": _("btn_change_role"),
        "settings_morning_time_title": _("settings_morning_time_title"),
        "settings_evening_time_title": _("settings_evening_time_title"),
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


async def on_toggle_setting(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id
    setting_name = button.widget_id

    current_settings = await user_data_manager.get_user_settings(user_id)
    current_status = current_settings.get(setting_name, False)

    new_status = not current_status
    await user_data_manager.update_setting(user_id, setting_name, new_status)

    # Напоминания теперь проверяются каждую минуту в minutely_reminders_checker,
    # поэтому нет необходимости вручную планировать/отменять напоминания.
    # Система автоматически учтёт изменения в следующую минуту.

    await callback.answer("Настройка обновлена.")
    await manager.switch_to(SettingsMenu.main)


async def on_time_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str) -> None:
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id

    await user_data_manager.set_reminder_time(user_id, int(item_id))
    await callback.answer(f"Время напоминаний: {item_id} минут")
    await manager.switch_to(SettingsMenu.main)


async def on_morning_time_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str) -> None:
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id

    await user_data_manager.set_morning_time(user_id, item_id)
    await callback.answer(f"Время утренней рассылки: {item_id}")
    await manager.switch_to(SettingsMenu.main)


async def on_evening_time_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str) -> None:
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id

    await user_data_manager.set_evening_time(user_id, item_id)
    await callback.answer(f"Время вечерней рассылки: {item_id}")
    await manager.switch_to(SettingsMenu.main)


async def on_back_click(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    await manager.done()


async def on_change_role_click(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    from aiogram_dialog import StartMode

    await manager.start(MainMenu.choose_user_type, mode=StartMode.RESET_STACK)


async def on_lang_selected(callback: CallbackQuery, widget: Any, manager: DialogManager, item_id: str) -> None:
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    await user_data_manager.set_user_language(callback.from_user.id, item_id)

    # Reload middleware I18n context manually or just reply
    _ = lambda k: i18n.get(k, lang=item_id)

    await callback.answer(_("lang_changed"), show_alert=True)
    await manager.switch_to(SettingsMenu.main)


async def _is_theme_subscribed(user_id: int, manager: DialogManager) -> bool:
    """Проверяет подписку через единый механизм БД/legacy-канала."""
    from core.config import SUBSCRIPTION_CHANNEL
    from core.subscription_filter import is_user_subscribed

    bot = manager.middleware_data.get("bot")
    if not bot:
        return not SUBSCRIPTION_CHANNEL and not manager.middleware_data.get("session_factory")
    return await is_user_subscribed(
        user_id=user_id,
        bot=bot,
        session_factory=manager.middleware_data.get("session_factory"),
        redis_client=manager.middleware_data.get("redis_client"),
        fallback_channel=SUBSCRIPTION_CHANNEL,
    )


def _build_channel_link(channel: str) -> str:
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    if channel.startswith("-"):
        return f"tg://resolve?domain={channel}"
    if not channel.startswith("http"):
        return f"https://t.me/{channel}"
    return channel


async def on_theme_button_click(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Обработчик кнопки выбора темы с проверкой подписки (fail-closed)."""
    user_id = callback.from_user.id
    from core.config import SUBSCRIPTION_CHANNEL

    if SUBSCRIPTION_CHANNEL and not await _is_theme_subscribed(user_id, manager):
        await manager.switch_to(SettingsMenu.theme_subscription_gate)
        try:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔔 Подписаться", url=_build_channel_link(SUBSCRIPTION_CHANNEL))]]
            )
            await callback.message.answer(
                "Доступ к персональным темам доступен по подписке на канал.",
                reply_markup=kb,
            )
        except Exception:
            pass
        try:
            await callback.answer()
        except Exception:
            pass
        return

    await manager.switch_to(SettingsMenu.choose_theme)


async def on_check_theme_subscription(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
    """Кнопка «Проверить подписку» в окне гейта."""
    user_id = callback.from_user.id
    if await _is_theme_subscribed(user_id, manager):
        await manager.switch_to(SettingsMenu.choose_theme)
        try:
            await callback.answer("✅ Подписка подтверждена!")
        except Exception:
            pass
    else:
        try:
            await callback.answer("❌ Подписка не найдена. Подпишитесь и попробуйте снова.", show_alert=True)
        except Exception:
            pass


async def on_theme_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str) -> None:
    """Сохраняет выбранную тему пользователя и возвращает в меню настроек."""
    try:
        user_id = callback.from_user.id
        user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
        if not await _is_theme_subscribed(user_id, manager):
            await callback.answer("❌ Требуется подписка на канал", show_alert=True)
            return
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


async def get_theme_data(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    """Возвращает текущую тему и список тем с пометкой текущей."""
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id

    # CRITICAL: Fetch user_lang and define _ FIRST before any usage
    user_lang = await user_data_manager.get_user_language(user_id) if user_data_manager else "ru"
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    current_theme = DEFAULT_THEME
    try:
        if user_data_manager:
            current_theme = await user_data_manager.get_user_theme(user_id) or DEFAULT_THEME
    except Exception:
        current_theme = DEFAULT_THEME

    current_meta = get_theme_meta(current_theme)
    current_theme_name = _(current_meta.name_key)

    themes = [
        {
            "id": t.id,
            "name": _(t.name_key),
            "description": _(t.desc_key),
            "is_current": t.id == current_theme,
        }
        for t in THEMES
    ]
    return {
        "current_theme": current_theme_name,
        "themes": themes,
        "btn_back": _("btn_back"),
        "theme_select_title": _("theme_select_title"),
        "theme_current_fmt": _("theme_current_fmt", current_theme=current_theme_name),
        "theme_available_label": _("theme_available_label"),
    }


async def on_news_clicked(callback: CallbackQuery, button: Button, manager: DialogManager) -> None:
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
        Row(
            Button(
                Format("{morning_button_text}"),
                id=WidgetIds.MORNING_SUMMARY,
                on_click=on_toggle_setting,
            ),
            SwitchTo(
                Format("🕒 {btn_morning_time}"),
                id="to_morning_time",
                state=SettingsMenu.morning_time,
                when="is_morning_enabled",
            ),
        ),
        Row(
            Button(
                Format("{evening_button_text}"),
                id=WidgetIds.EVENING_NOTIFY,
                on_click=on_toggle_setting,
            ),
            SwitchTo(
                Format("🕒 {btn_evening_time}"),
                id="to_evening_time",
                state=SettingsMenu.evening_time,
                when="is_evening_enabled",
            ),
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
        Format("{settings_diff_label}: <b>{schedule_diff_status_text}</b>"),
        Button(
            Format("{schedule_diff_button_text}"),
            id=WidgetIds.SCHEDULE_DIFF_NOTIFY,
            on_click=on_toggle_setting,
        ),
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
        Button(
            Format("{btn_change_role}"),
            id=WidgetIds.CHANGE_ROLE,
            on_click=on_change_role_click,
        ),
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
        SwitchTo(Format("{btn_back}"), id="back_from_reminders", state=SettingsMenu.main),
        state=SettingsMenu.reminders_time,
        getter=get_settings_data,
    ),
    Window(
        Format("{settings_morning_time_title}"),
        Row(
            Select(
                Jinja("{% if item == current_morning_time %}✅ {{ item }}{% else %}{{ item }}{% endif %}"),
                id="select_morning_time",
                item_id_getter=lambda item: str(item),
                items="morning_times",
                on_click=on_morning_time_selected,
            )
        ),
        SwitchTo(Format("{btn_back}"), id="back_from_morning", state=SettingsMenu.main),
        state=SettingsMenu.morning_time,
        getter=get_settings_data,
    ),
    Window(
        Format("{settings_evening_time_title}"),
        Row(
            Select(
                Jinja("{% if item == current_evening_time %}✅ {{ item }}{% else %}{{ item }}{% endif %}"),
                id="select_evening_time",
                item_id_getter=lambda item: str(item),
                items="evening_times",
                on_click=on_evening_time_selected,
            )
        ),
        SwitchTo(Format("{btn_back}"), id="back_from_evening", state=SettingsMenu.main),
        state=SettingsMenu.evening_time,
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
    Window(
        Format("{theme_gate_title}"),
        Button(
            Format("{theme_btn_check}"),
            id="check_subscription",
            on_click=on_check_theme_subscription,
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
