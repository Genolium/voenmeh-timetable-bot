import random
from thefuzz import process

from aiogram.types import Message, CallbackQuery
import re
from aiogram_dialog import Dialog, Window, DialogManager, StartMode
from aiogram_dialog.widgets.text import Const, Format
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.media import StaticMedia
from aiogram_dialog.widgets.kbd import Button, Row

from .states import MainMenu, Schedule, About
from .constants import DialogDataKeys, WidgetIds
from core.manager import TimetableManager
from core.user_data import UserDataManager
from core.config import WELCOME_IMAGE_PATH

async def get_main_menu_data(dialog_manager: DialogManager, **kwargs):
    """
    Выбирает случайную группу для примера в приветственном сообщении.
    """
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    # Получаем список групп, исключая служебные ключи
    groups = [g for g in manager._schedules.keys() if not g.startswith('__')]
    
    # Выбираем случайную группу или используем запасной вариант
    random_group = random.choice(groups) if groups else "О735Б"
    
    return {"random_group": random_group}


async def on_group_entered(message: Message, message_input: MessageInput, manager: DialogManager):
    # Нормализуем ввод: оставляем только цифры/буквы, ограничиваем длину
    raw = (message.text or "").upper()
    group_name = re.sub(r"[^А-ЯA-Z0-9]", "", raw)[:20]
    if not group_name:
        await message.answer("❌ Некорректный ввод. Введите номер группы, например: <b>О735Б</b>.")
        return
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    all_groups = [g for g in timetable_manager._schedules.keys() if not g.startswith('__')]

    # Проверяем прямое совпадение
    if group_name not in all_groups:
        # Если прямого совпадения нет, ищем похожие
        suggestions = process.extract(group_name, all_groups, limit=3)
        good_suggestions = [s[0] for s in suggestions if s[1] > 75]

        if good_suggestions:
            # Форматируем каждый предложенный вариант
            formatted_suggestions = [f"<code>{s}</code>" for s in good_suggestions]
            # Соединяем варианты через ", или " для правильного перечисления
            suggestion_text = ", или ".join(formatted_suggestions)
            await message.answer(f"❌ Группа <b>{group_name}</b> не найдена.\nВозможно, вы имели в виду: {suggestion_text}?")
        else:
            # Если нет даже похожих, выводим стандартное сообщение
            await message.answer(f"❌ Группа <b>{group_name}</b> не найдена.\nПопробуйте еще раз.")
        return # В любом случае, если не было точного совпадения, выходим

    # Этот код выполнится только если было точное совпадение
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    await user_data_manager.register_user(
        user_id=message.from_user.id,
        username=message.from_user.username
    )
    await user_data_manager.set_user_group(user_id=message.from_user.id, group=group_name)
    
    manager.dialog_data[DialogDataKeys.GROUP] = group_name
    await manager.switch_to(MainMenu.offer_tutorial)


async def on_skip_tutorial_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    group_name = manager.dialog_data.get(DialogDataKeys.GROUP)
    await manager.start(Schedule.view, data={DialogDataKeys.GROUP: group_name}, mode=StartMode.RESET_STACK)

async def on_show_tutorial_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(About.page_1, mode=StartMode.RESET_STACK)

dialog = Dialog(
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Format("👋 Привет! Я бот расписания Военмеха.\n\n"
               "Чтобы начать, введите номер вашей группы:\n"
               "<i>Например: {random_group}</i>"),
        MessageInput(on_group_entered),
        state=MainMenu.enter_group,
        getter=get_main_menu_data,
        parse_mode="HTML"
    ),
    Window(
        Format(
            "✅ Группа <b>{dialog_data[group]}</b> сохранена!\n\n"
            "Я могу не только показывать расписание, но и искать по преподавателям, "
            "присылать уведомления и работать в других чатах. "
            "Хотите посмотреть короткую инструкцию?"
        ),
        Row(
            Button(Const("📖 Показать инструкцию"), id=WidgetIds.SHOW_TUTORIAL, on_click=on_show_tutorial_clicked),
            Button(Const("Понятно, спасибо!"), id=WidgetIds.SKIP_TUTORIAL, on_click=on_skip_tutorial_clicked)
        ),
        state=MainMenu.offer_tutorial
    )
)