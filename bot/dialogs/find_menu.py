from datetime import date, timedelta
from typing import Any

from aiogram.types import Message, CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager
from aiogram_dialog.widgets.text import Const, Format
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button, Back, Row, Select, Column, SwitchTo
from aiogram_dialog.widgets.media import StaticMedia
from aiogram.enums import ContentType

from .states import FindMenu
from core.manager import TimetableManager
from bot.utils import format_teacher_schedule_text, format_classroom_schedule_text
from core.config import SEARCH_IMAGE_PATH, TEACHER_IMAGE_PATH, CLASSROOM_IMAGE_PATH, NO_LESSONS_IMAGE_PATH

async def get_find_data(dialog_manager: DialogManager, **kwargs):
    if not dialog_manager.dialog_data.get("find_date_iso"):
        dialog_manager.dialog_data["find_date_iso"] = date.today().isoformat()
    
    current_date = date.fromisoformat(dialog_manager.dialog_data["find_date_iso"])
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    
    data = {"found_items": dialog_manager.dialog_data.get("found_items", [])}
    
    if dialog_manager.dialog_data.get("teacher_name"):
        teacher_name = dialog_manager.dialog_data["teacher_name"]
        schedule_info = manager.get_teacher_schedule(teacher_name, current_date)
        data["result_text"] = format_teacher_schedule_text(schedule_info)
    elif dialog_manager.dialog_data.get("classroom_number"):
        classroom_number = dialog_manager.dialog_data["classroom_number"]
        schedule_info = manager.get_classroom_schedule(classroom_number, current_date)
        data["result_text"] = format_classroom_schedule_text(schedule_info)
        
    return data

async def on_teacher_input(message: Message, message_input: MessageInput, manager: DialogManager):
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    found_teachers = timetable_manager.find_teachers(message.text)
    
    if not found_teachers:
        await message.answer("❌ Преподаватель не найден. Попробуйте ввести только фамилию.")
        return
    
    manager.dialog_data["search_type"] = "teacher"
    
    if len(found_teachers) == 1:
        manager.dialog_data["teacher_name"] = found_teachers[0]
        manager.dialog_data.pop("classroom_number", None)
        await manager.switch_to(FindMenu.view_result)
    else:
        manager.dialog_data["found_items"] = found_teachers[:20]
        await manager.switch_to(FindMenu.select_item)

async def on_classroom_input(message: Message, message_input: MessageInput, manager: DialogManager):
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    found_classrooms = timetable_manager.find_classrooms(message.text)

    if not found_classrooms:
        await message.answer("❌ Аудитория не найдена.")
        return

    manager.dialog_data["search_type"] = "classroom"
    
    if len(found_classrooms) == 1:
        manager.dialog_data["classroom_number"] = found_classrooms[0]
        manager.dialog_data.pop("teacher_name", None)
        await manager.switch_to(FindMenu.view_result)
    else:
        manager.dialog_data["found_items"] = found_classrooms[:20]
        await manager.switch_to(FindMenu.select_item)

async def on_item_selected(callback: CallbackQuery, widget: Any, manager: DialogManager, item_id: str):
    search_type = manager.dialog_data.get("search_type")
    
    if search_type == "teacher":
        manager.dialog_data["teacher_name"] = item_id
    elif search_type == "classroom":
        manager.dialog_data["classroom_number"] = item_id

    await manager.switch_to(FindMenu.view_result)

async def on_find_date_shift(callback: CallbackQuery, button: Button, manager: DialogManager, days: int):
    current_date = date.fromisoformat(manager.dialog_data["find_date_iso"])
    manager.dialog_data["find_date_iso"] = (current_date + timedelta(days=days)).isoformat()

async def on_find_today_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    manager.dialog_data["find_date_iso"] = date.today().isoformat()
    
async def on_back_to_main_menu(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.done()

find_dialog = Dialog(
    Window(
        StaticMedia(path=SEARCH_IMAGE_PATH),
        Const("Что вы хотите найти?"),
        Column(
            SwitchTo(Const("🧑‍🏫 По преподавателю"), id="find_teacher_btn", state=FindMenu.enter_teacher),
            SwitchTo(Const("🚪 По аудитории"), id="find_classroom_btn", state=FindMenu.enter_classroom),
        ),
        Button(Const("◀️ Назад"), id="back_to_main_schedule", on_click=on_back_to_main_menu),
        state=FindMenu.choice,
        disable_web_page_preview=True
    ),
    Window(
        StaticMedia(path=TEACHER_IMAGE_PATH),
        Const("Введите фамилию преподавателя (минимум 3 буквы):"),
        MessageInput(on_teacher_input),
        SwitchTo(Const("◀️ Назад"), id="back_to_choice_1", state=FindMenu.choice),
        state=FindMenu.enter_teacher,
        disable_web_page_preview=True
    ),
    Window(
        StaticMedia(path=CLASSROOM_IMAGE_PATH),
        Const("Введите номер аудитории:"),
        MessageInput(on_classroom_input),
        SwitchTo(Const("◀️ Назад"), id="back_to_choice_2", state=FindMenu.choice),
        state=FindMenu.enter_classroom,
        disable_web_page_preview=True
    ),
    Window(
        Const("Найдено несколько совпадений. Пожалуйста, выберите:"),
        Column(
            Select(
                Format("{item}"),
                id="select_found_item",
                item_id_getter=lambda item: item,
                items="found_items",
                on_click=on_item_selected,
            )
        ),
        SwitchTo(Const("◀️ Назад"), id="back_to_input_teacher", when=lambda data, w, m: m.dialog_data.get("search_type") == "teacher", state=FindMenu.enter_teacher),
        SwitchTo(Const("◀️ Назад"), id="back_to_input_classroom", when=lambda data, w, m: m.dialog_data.get("search_type") == "classroom", state=FindMenu.enter_classroom),
        state=FindMenu.select_item,
        getter=get_find_data,
        parse_mode="HTML",
        disable_web_page_preview=True
    ),
    Window(
        Format("{result_text}"),
        Row(
            Button(Const("⏪"), id="find_prev_week", on_click=lambda c, b, m: on_find_date_shift(c, b, m, -7)),
            Button(Const("◀️"), id="find_prev_day", on_click=lambda c, b, m: on_find_date_shift(c, b, m, -1)),
            Button(Const("📅"), id="find_today", on_click=on_find_today_click),
            Button(Const("▶️"), id="find_next_day", on_click=lambda c, b, m: on_find_date_shift(c, b, m, 1)),
            Button(Const("⏩"), id="find_next_week", on_click=lambda c, b, m: on_find_date_shift(c, b, m, 7)),
        ),
        SwitchTo(Const("◀️ Новый поиск"), id="back_to_choice_3", state=FindMenu.choice),
        state=FindMenu.view_result,
        getter=get_find_data,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
)