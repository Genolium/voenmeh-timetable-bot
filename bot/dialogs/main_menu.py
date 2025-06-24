from aiogram.types import Message
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.text import Const
from aiogram_dialog.widgets.input import MessageInput

from .states import MainMenu, Schedule
from core.manager import TimetableManager
from core.user_data import UserDataManager

async def on_group_entered(message: Message, message_input: MessageInput, manager: DialogManager):
    group_name = message.text.upper()
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")

    if group_name not in timetable_manager._schedules:
        await message.answer(f"❌ Группа <b>{group_name}</b> не найдена.\nПопробуйте еще раз. Например: <i>О735Б</i>")
        return

    await user_data_manager.set_user_group(user_id=message.from_user.id, group=group_name)
    await message.answer(f"✅ Группа <b>{group_name}</b> сохранена!")

    await manager.done() 
    await manager.start(Schedule.view, data={"group": group_name})

dialog = Dialog(
    Window(
        Const("👋 Привет! Я бот расписания Военмеха.\n\n"
              "Чтобы начать, введите номер вашей группы:"),
        MessageInput(on_group_entered),
        state=MainMenu.enter_group,
    )
)