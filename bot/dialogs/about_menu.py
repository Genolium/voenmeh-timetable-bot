from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager, StartMode
from aiogram_dialog.widgets.kbd import Button, Row, SwitchTo, Back
from aiogram_dialog.widgets.media import StaticMedia
from aiogram_dialog.widgets.text import Const, Format

from core.user_data import UserDataManager 

from .states import About, Schedule

# --- Константы для путей к изображениям ---
IMG_PATH = "bot/screenshots/"
IMG_WELCOME = IMG_PATH + "about_welcome.png"
IMG_MAIN_SCREEN = IMG_PATH + "about_main_screen.png"
IMG_SEARCH = IMG_PATH + "about_search.png"
IMG_NOTIFICATIONS = IMG_PATH + "about_notifications.png"
IMG_INLINE = IMG_PATH + "about_inline.png"

# --- Тексты для страниц ---
TEXT_PAGE_1 = (
    "👋 <b>Привет! Я бот расписания Военмеха.</b>\n\n"
    "Моя главная задача — сделать доступ к расписанию <b>максимально быстрым и удобным</b>. Меня разработал @ilyan_vas \n\n"
    "Давайте я покажу, что я умею!"
)
TEXT_PAGE_2 = (
    "✅ <b>Актуальное расписание всегда под рукой</b>\n\n"
    "Основной экран показывает расписание на выбранный день. "
    "А вверху вы увидите <b>динамический заголовок</b>, который подскажет, "
    "когда начнется следующая пара или когда закончится перерыв."
)
TEXT_PAGE_3 = (
    "🔍 <b>Умный поиск</b>\n\n"
    "Не знаете, где преподаватель или свободна ли нужная аудитория? "
    "Воспользуйтесь функцией поиска! Я могу найти расписание для любого "
    "преподавателя или аудитории на выбранный день."
)
TEXT_PAGE_4 = (
    "🔔 <b>Гибкие уведомления</b>\n\n"
    "Я могу присылать вам:\n"
    "• Сводку на завтра (вечером)\n"
    "• Сводку на сегодня (утром)\n"
    "• Напоминания за 20 минут до первой пары и в начале каждого перерыва.\n\n"
    "Все это настраивается в меню <b>«⚙️ Настройки»</b>."
)
TEXT_PAGE_5 = (
    "📲 <b>Inline-режим «Поделись расписанием»</b>\n\n"
    "В любом диалоге (чате группы, потока или просто друзей) просто напишите мое имя и запрос, например:\n"
    "<code>@bstu_timetable_bot О735Б завтра</code>\n\n"
    "Я сразу предложу отправить готовое расписание. Идеально для координации с одногруппниками!"
)

# --- Навигация и завершение ---
TOTAL_PAGES = 5

async def on_finish_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    """
    Обработчик нажатия на кнопку 'Завершить'.
    Удаляет сообщение и переходит к основному диалогу с расписанием.
    """
    # Получаем user_data_manager из middleware
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    
    # Получаем сохраненную группу пользователя из БД
    user_group = await user_data_manager.get_user_group(callback.from_user.id)
    
    # Удаляем сообщение с туториалом
    await callback.message.delete()
    
    if user_group:
        # Если группа найдена, запускаем диалог расписания
        await manager.start(Schedule.view, data={"group": user_group}, mode=StartMode.RESET_STACK)
    else:
        # Если по какой-то причине группы нет, просто завершаем диалог.
        # Пользователь сможет ввести /start снова.
        await manager.done()
        
    
# --- Создание диалога ---
about_dialog = Dialog(
    # --- Страница 1 ---
    Window(
        StaticMedia(path=IMG_WELCOME),
        Const(TEXT_PAGE_1),
        Row(
            Button(Const(f"1/{TOTAL_PAGES}"), id="pager"),
            SwitchTo(Const("Далее ▶️"), id="next_1", state=About.page_2),
        ),
        state=About.page_1,
        disable_web_page_preview=True,
        parse_mode="HTML"
    ),
    # --- Страница 2 ---
    Window(
        StaticMedia(path=IMG_MAIN_SCREEN),
        Const(TEXT_PAGE_2),
        Row(
            Back(Const("◀️ Назад")),
            Button(Const(f"2/{TOTAL_PAGES}"), id="pager"),
            SwitchTo(Const("Далее ▶️"), id="next_2", state=About.page_3),
        ),
        state=About.page_2,
        disable_web_page_preview=True,
        parse_mode="HTML"
    ),
    # --- Страница 3 ---
    Window(
        StaticMedia(path=IMG_SEARCH),
        Const(TEXT_PAGE_3),
        Row(
            Back(Const("◀️ Назад")),
            Button(Const(f"3/{TOTAL_PAGES}"), id="pager"),
            SwitchTo(Const("Далее ▶️"), id="next_3", state=About.page_4),
        ),
        state=About.page_3,
        disable_web_page_preview=True,
        parse_mode="HTML"
    ),
    # --- Страница 4 ---
    Window(
        StaticMedia(path=IMG_NOTIFICATIONS),
        Const(TEXT_PAGE_4),
        Row(
            Back(Const("◀️ Назад")),
            Button(Const(f"4/{TOTAL_PAGES}"), id="pager"),
            SwitchTo(Const("Далее ▶️"), id="next_4", state=About.page_5),
        ),
        state=About.page_4,
        disable_web_page_preview=True,
        parse_mode="HTML"
    ),
    # --- Страница 5 ---
    Window(
        StaticMedia(path=IMG_INLINE),
        Const(TEXT_PAGE_5),
        Row(
            Back(Const("◀️ Назад")),
            Button(Const(f"5/{TOTAL_PAGES}"), id="pager"),
            Button(Const("✅ Понятно"), id="finish", on_click=on_finish_clicked),
        ),
        state=About.page_5,
        disable_web_page_preview=True,
        parse_mode="HTML"
    ),    
)