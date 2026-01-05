from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.kbd import Back, Button, Row, Select
from aiogram_dialog.widgets.text import Const, Format, Jinja

from bot.tasks import check_theme_subscription_task
from core.config import SUBSCRIPTION_CHANNEL
from core.user_data import UserDataManager
from core.i18n import i18n

from .constants import WidgetIds
from .states import SettingsMenu


async def get_theme_data(dialog_manager: DialogManager, **kwargs):
    """Получает данные о текущей теме пользователя."""
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    # Получаем язык пользователя
    user_lang = await user_data_manager.get_user_language(user_id) if user_data_manager else "ru"
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    # Определяем названия тем с эмодзи
    themes_info = {
        "standard": (_("theme_standard_name"), _("theme_standard_desc")),
        "light": (_("theme_light_name"), _("theme_light_desc")),
        "dark": (_("theme_dark_name"), _("theme_dark_desc")),
        "classic": (_("theme_classic_name"), _("theme_classic_desc")),
        "coffee": (_("theme_coffee_name"), _("theme_coffee_desc")),
    }

    current_theme_key = themes_info.get(current_theme)
    if not current_theme_key:
        current_theme_name = _("theme_standard_name")
        current_theme_desc = _("theme_standard_desc")
    else:
        current_theme_name, current_theme_desc = current_theme_key

    # Создаем список тем с информацией о текущей выбранной
    themes = []
    for theme_id, (name, description) in themes_info.items():
        themes.append(
            {
                "id": theme_id,
                "name": name,
                "description": description,
                "is_current": theme_id == current_theme,
            }
        )

    is_subscribed = await _check_theme_subscription(user_id, dialog_manager)

    # Если пользователь не подписан, переключаемся на состояние блокировки
    if not is_subscribed and SUBSCRIPTION_CHANNEL:
        await dialog_manager.switch_to(SettingsMenu.theme_subscription_gate)
        return {}

    return {
        "current_theme": current_theme_name,
        "themes": themes,
        "is_subscribed": is_subscribed,
        # Localized UI strings
        "theme_gate_title": _("theme_gate_title"),
        "theme_select_title": _("theme_select_title"),
        "theme_current_fmt": _("theme_current_fmt", current_theme=current_theme_name),
        "theme_available_label": _("theme_available_label"),
        "theme_btn_check": _("theme_btn_check"),
        "btn_back": _("btn_back"),
    }


async def _check_theme_subscription(user_id: int, dialog_manager: DialogManager) -> bool:
    """Проверяет подписку пользователя на канал для доступа к темам."""
    try:
        # Проверяем кэш сначала
        from core.config import get_redis_client

        redis_client = await get_redis_client()
        cache_key = f"theme_sub_status:{user_id}"
        cached = await redis_client.get(cache_key)

        if cached is not None:
            return cached == "1"

        # Если кэша нет, проверяем напрямую через API
        if SUBSCRIPTION_CHANNEL:
            bot: Bot = dialog_manager.middleware_data.get("bot")
            if bot:
                member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                status = getattr(member, "status", None)
                is_subscribed = status in ("member", "administrator", "creator")

                # Кэшируем результат
                await redis_client.set(
                    cache_key,
                    "1" if is_subscribed else "0",
                    ex=21600 if is_subscribed else 60,
                )
                return is_subscribed

    except Exception:
        pass

    return False


async def on_theme_selected(callback: CallbackQuery, widget: Select, manager: DialogManager, item_id: str):
    """Обработчик выбора темы."""
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_id = callback.from_user.id

    # Проверяем подписку перед сменой темы
    is_subscribed = await _check_theme_subscription(user_id, manager)

    if not is_subscribed and SUBSCRIPTION_CHANNEL:
        # Пользователь не подписан, запускаем проверку через задачу
        check_theme_subscription_task.send(user_id, callback.id)
        await callback.answer("❌ Требуется подписка на канал для доступа к темам", show_alert=True)
        return

    await user_data_manager.set_user_theme(user_id, item_id)

    # Получаем информацию о выбранной теме (локализованную)
    # Здесь нам нужен язык пользователя, так как мы не в геттере
    # Получим его заново или возьмем дефолт, т.к. middleware_data может не иметь 'lang' в хендлере если это не прокинуто явно
    # Однако, UserDataMiddleware выполняется до хендлера, но i18n хелпер внутри геттеров
    # Проще всего достать язык из БД снова или использовать дефолт EN/RU по факту
    user_lang = await user_data_manager.get_user_language(user_id) or "ru"
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    themes_info_names = {
        "standard": _("theme_standard_name"),
        "light": _("theme_light_name"),
        "dark": _("theme_dark_name"),
        "classic": _("theme_classic_name"),
        "coffee": _("theme_coffee_name"),
    }

    theme_name = themes_info_names.get(item_id, _("theme_standard_name"))

    await callback.answer(_("theme_changed_success", theme=theme_name) if i18n.get("theme_changed_success") else f"✅ {theme_name}")
    await manager.switch_to(SettingsMenu.main)


async def on_back_to_settings(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Обработчик кнопки назад в настройки."""
    await manager.switch_to(SettingsMenu.main)


async def on_check_subscription(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Обработчик проверки подписки."""
    user_id = callback.from_user.id

    # Запускаем задачу проверки подписки
    check_theme_subscription_task.send(user_id, callback.id)

    # Возвращаемся в главное меню тем
    await manager.switch_to(SettingsMenu.choose_theme)


# Диалог выбора темы
theme_dialog = Dialog(
    # Окно с блокировкой доступа (если не подписан)
    Window(
        Format("{theme_gate_title}"),
        Button(
            Format("{theme_btn_check}"),
            id="check_subscription",
            on_click=on_check_subscription,
        ),
        Back(Format("{btn_back}"), on_click=on_back_to_settings),
        state=SettingsMenu.theme_subscription_gate,
        parse_mode="HTML",
    ),
    # Окно с выбором темы (если подписан)
    Window(
        Format("{theme_select_title}"),
        Format("{theme_current_fmt}"),
        Format("{theme_available_label}"),
        # Список тем для выбора
        Select(
            Jinja(
                "{% if item.is_current %}"
                "✅ <b>{{ item.name }}</b> (current)\n"
                "<i>{{ item.description }}</i>\n\n"
                "{% else %}"
                "🔘 {{ item.name }}\n"
                "<i>{{ item.description }}</i>\n\n"
                "{% endif %}"
            ),
            id="select_theme",
            item_id_getter=lambda item: item["id"],
            items="themes",
            on_click=on_theme_selected,
        ),
        Back(Format("{btn_back}"), on_click=on_back_to_settings),
        state=SettingsMenu.choose_theme,
        getter=get_theme_data,
        parse_mode="HTML",
    ),
)
