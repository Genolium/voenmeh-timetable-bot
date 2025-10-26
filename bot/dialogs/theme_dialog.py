from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.kbd import Button, Row, Back, Select
from aiogram_dialog.widgets.text import Const, Format, Jinja

from .states import SettingsMenu
from .constants import WidgetIds
from core.user_data import UserDataManager
from core.config import SUBSCRIPTION_CHANNEL
from bot.tasks import check_theme_subscription_task


async def get_theme_data(dialog_manager: DialogManager, **kwargs):
    """Получает данные о текущей теме пользователя."""
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_id = dialog_manager.event.from_user.id
    current_theme = await user_data_manager.get_user_theme(user_id)

    # Определяем названия тем с эмодзи
    themes_info = {
        'standard': ('🎨 Стандартная', 'красная для нечётных недель, фиолетовая для чётных'),
        'light': ('☀️ Светлая', 'бирюзовая тема с кремовыми карточками'),
        'dark': ('🌙 Тёмная', 'тёмная тема с фиолетовыми акцентами'),
        'classic': ('📜 Классическая', 'тёмно-синяя тема в цветовой гамме Военмеха'),
        'coffee': ('☕ Кофейная', 'коричнево-золотая тема с кремовыми карточками')
    }

    # Создаем список тем с информацией о текущей выбранной
    themes = []
    for theme_id, (name, description) in themes_info.items():
        themes.append({
            'id': theme_id,
            'name': name,
            'description': description,
            'is_current': theme_id == current_theme
        })

    is_subscribed = await _check_theme_subscription(user_id, dialog_manager)

    # Если пользователь не подписан, переключаемся на состояние блокировки
    if not is_subscribed and SUBSCRIPTION_CHANNEL:
        await manager.switch_to(SettingsMenu.theme_subscription_gate)
        return {}

    return {
        'current_theme': themes_info.get(current_theme, ('🎨 Стандартная', 'оранжево-красная для нечётных недель, фиолетовая для чётных'))[0],
        'themes': themes,
        'is_subscribed': is_subscribed
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
            return cached == '1'

        # Если кэша нет, проверяем напрямую через API
        if SUBSCRIPTION_CHANNEL:
            bot: Bot = dialog_manager.middleware_data.get("bot")
            if bot:
                member = await bot.get_chat_member(SUBSCRIPTION_CHANNEL, user_id)
                status = getattr(member, "status", None)
                is_subscribed = status in ("member", "administrator", "creator")

                # Кэшируем результат
                await redis_client.set(cache_key, '1' if is_subscribed else '0', ex=21600 if is_subscribed else 60)
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

    # Получаем информацию о выбранной теме
    themes_info = {
        'standard': '🎨 Стандартная',
        'light': '☀️ Светлая',
        'dark': '🌙 Тёмная',
        'classic': '📜 Классическая',
        'coffee': '☕ Кофейная'
    }

    theme_name = themes_info.get(item_id, '🎨 Стандартная')

    await callback.answer(f"✅ Тема изменена на {theme_name}!")
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
        Const("🎨 <b>Доступ к персональным темам</b>\n\n"
              "Выберите уникальную тему для вашего расписания:\n\n"
              "🎨 <b>Стандартная</b> - красная для нечётных, фиолетовая для чётных недель\n"
              "☀️ <b>Светлая</b> - бирюзовая тема с кремовыми карточками\n"
              "🌙 <b>Тёмная</b> - тёмная тема с фиолетовыми акцентами\n"
              "📜 <b>Классическая</b> - тёмно-синяя тема с белыми карточками\n"
              "☕ <b>Кофейная</b> - коричнево-золотая тема с кремовыми карточками\n\n"
              "<i>Доступно только по подписке на канал разработки</i>"),
        Button(Const("✅ Проверить подписку"), id="check_subscription", on_click=on_check_subscription),
        Back(Const("◀️ Назад"), on_click=on_back_to_settings),
        state=SettingsMenu.theme_subscription_gate,
        parse_mode="HTML"
    ),

    # Окно с выбором темы (если подписан)
    Window(
        Const("🎨 <b>Выбор темы оформления</b>\n\n"
              "Выберите тему для вашего расписания:\n"),
        Format("Текущая тема: <b>{current_theme}</b>\n"),
        Const("\n📋 <i>Доступные темы:</i>\n"),

        # Список тем для выбора
        Select(
            Jinja(
                "{% if item.is_current %}"
                "✅ <b>{{ item.name }}</b> (текущая)\n"
                "<i>{{ item.description }}</i>\n\n"
                "{% else %}"
                "🔘 {{ item.name }}\n"
                "<i>{{ item.description }}</i>\n\n"
                "{% endif %}"
            ),
            id="select_theme",
            item_id_getter=lambda item: item['id'],
            items="themes",
            on_click=on_theme_selected
        ),

        Back(Const("◀️ Назад"), on_click=on_back_to_settings),

        state=SettingsMenu.choose_theme,
        getter=get_theme_data,
        parse_mode="HTML"
    )
)
