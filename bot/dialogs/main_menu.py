from aiogram.types import Message, CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager, StartMode
from aiogram_dialog.widgets.text import Const, Format
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.media import StaticMedia
from aiogram_dialog.widgets.kbd import Button, Row

from .states import MainMenu, Schedule, About
from core.manager import TimetableManager
from core.user_data import UserDataManager
from core.config import WELCOME_IMAGE_PATH

async def on_group_entered(message: Message, message_input: MessageInput, manager: DialogManager):
    """
    Обработчик ввода группы. Проверяет, сохраняет и переходит к следующему шагу.
    """
    group_name = message.text.upper()
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")

    if group_name not in timetable_manager._schedules:
        await message.answer(f"❌ Группа <b>{group_name}</b> не найдена.\nПопробуйте еще раз. Например: <i>О735Б</i>")
        return

    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")

    # 1. Сначала регистрируем пользователя. Это создаст запись в БД, если ее нет.
    await user_data_manager.register_user(
        user_id=message.from_user.id,
        username=message.from_user.username
    )
    
    # 2. Теперь, когда пользователь гарантированно есть в БД, устанавливаем его группу.
    await user_data_manager.set_user_group(user_id=message.from_user.id, group=group_name)
    
    manager.dialog_data["group"] = group_name
    await manager.switch_to(MainMenu.offer_tutorial)

async def on_skip_tutorial_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Переходит к расписанию, пропуская инструкцию."""
    group_name = manager.dialog_data.get("group")
    await manager.start(Schedule.view, data={"group": group_name}, mode=StartMode.RESET_STACK)

async def on_show_tutorial_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Запускает диалог с инструкцией."""
    await manager.start(About.page_1, mode=StartMode.RESET_STACK)


dialog = Dialog(
    # --- Окно 1: Ввод группы ---
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Const("👋 Привет! Я бот расписания Военмеха.\n\n"
              "Чтобы начать, введите номер вашей группы:"),
        MessageInput(on_group_entered),
        state=MainMenu.enter_group,
    ),
    # --- Окно 2: Предложение инструкции ---
    Window(
        Format(
            "✅ Группа <b>{dialog_data[group]}</b> сохранена!\n\n"
            "Я могу не только показывать расписание, но и искать по преподавателям, "
            "присылать уведомления и работать в других чатах. "
            "Хотите посмотреть короткую инструкцию?"
        ),
        Row(
            Button(
                Const("📖 Показать инструкцию"), 
                id="show_tutorial", 
                on_click=on_show_tutorial_clicked,
            ),
            Button(
                Const("Понятно, спасибо!"), 
                id="skip_tutorial", 
                on_click=on_skip_tutorial_clicked
            )
        ),
        state=MainMenu.offer_tutorial
    )
)
