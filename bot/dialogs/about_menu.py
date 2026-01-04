import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, DialogManager, StartMode, Window
from aiogram_dialog.widgets.kbd import Back, Button, Row, SwitchTo
from aiogram_dialog.widgets.link_preview import LinkPreview
from aiogram_dialog.widgets.media import StaticMedia
from aiogram_dialog.widgets.text import Const, Format

from core.config import ABOUT_INLINE_IMG, ABOUT_MAIN_SCREEN_IMG, ABOUT_NOTIFICATIONS_IMG, ABOUT_SEARCH_IMG, ABOUT_WELCOME_IMG
from core.i18n import i18n
from core.user_data import UserDataManager

from .constants import WidgetIds
from .states import About, Schedule


async def get_about_data(dialog_manager: DialogManager, **kwargs):
    user_id = dialog_manager.event.from_user.id
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_lang = await user_data_manager.get_user_language(user_id)

    # Реинициализируем функцию перевода с актуальным языком
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    return {
        "about_page_1": _("about_page_1"),
        "about_page_2": _("about_page_2"),
        "about_page_3": _("about_page_3"),
        "about_page_4": _("about_page_4"),
        "about_page_5": _("about_page_5"),
        "about_next": _("about_next"),
        "about_back": _("about_back"),
        "about_finish": _("about_finish"),
    }


# --- Навигация и завершение ---
TOTAL_PAGES = 5


async def on_finish_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    user_group = await user_data_manager.get_user_group(callback.from_user.id)

    try:
        await callback.message.delete()
    except TelegramBadRequest as e:
        if "message can't be deleted" in str(e):
            logging.warning(f"Не удалось удалить сообщение с туториалом (слишком старое): {e}")
        else:
            logging.error(f"Неожиданная ошибка при удалении сообщения: {e}")
            raise

    if user_group:
        await manager.start(Schedule.view, data={"group": user_group}, mode=StartMode.RESET_STACK)
    else:
        await manager.done()


about_dialog = Dialog(
    Window(
        StaticMedia(path=ABOUT_WELCOME_IMG),
        Format("{about_page_1}"),
        Row(
            Button(Const(f"1/{TOTAL_PAGES}"), id="pager_1"),
            SwitchTo(Format("{about_next}"), id="next_1", state=About.page_2),
        ),
        LinkPreview(is_disabled=True),
        state=About.page_1,
        parse_mode="HTML",
        getter=get_about_data,
    ),
    Window(
        StaticMedia(path=ABOUT_MAIN_SCREEN_IMG),
        Format("{about_page_2}"),
        Row(
            Back(Format("{about_back}")),
            Button(Const(f"2/{TOTAL_PAGES}"), id="pager_2"),
            SwitchTo(Format("{about_next}"), id="next_2", state=About.page_3),
        ),
        LinkPreview(is_disabled=True),
        state=About.page_2,
        parse_mode="HTML",
        getter=get_about_data,
    ),
    Window(
        StaticMedia(path=ABOUT_SEARCH_IMG),
        Format("{about_page_3}"),
        Row(
            Back(Format("{about_back}")),
            Button(Const(f"3/{TOTAL_PAGES}"), id="pager_3"),
            SwitchTo(Format("{about_next}"), id="next_3", state=About.page_4),
        ),
        LinkPreview(is_disabled=True),
        state=About.page_3,
        parse_mode="HTML",
        getter=get_about_data,
    ),
    Window(
        StaticMedia(path=ABOUT_NOTIFICATIONS_IMG),
        Format("{about_page_4}"),
        Row(
            Back(Format("{about_back}")),
            Button(Const(f"4/{TOTAL_PAGES}"), id="pager_4"),
            SwitchTo(Format("{about_next}"), id="next_4", state=About.page_5),
        ),
        LinkPreview(is_disabled=True),
        state=About.page_4,
        parse_mode="HTML",
        getter=get_about_data,
    ),
    Window(
        StaticMedia(path=ABOUT_INLINE_IMG),
        Format("{about_page_5}"),
        Row(
            Back(Format("{about_back}")),
            Button(Const(f"5/{TOTAL_PAGES}"), id="pager_5"),
            Button(
                Format("{about_finish}"),
                id=WidgetIds.FINISH_TUTORIAL,
                on_click=on_finish_clicked,
            ),
        ),
        LinkPreview(is_disabled=True),
        state=About.page_5,
        parse_mode="HTML",
        getter=get_about_data,
    ),
)
