import random
import re

from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, StartMode, Window
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button, Column, Row
from aiogram_dialog.widgets.media import StaticMedia
from aiogram_dialog.widgets.text import Const, Format
from thefuzz import process

from core.config import WELCOME_IMAGE_PATH
from core.manager import TimetableManager
from core.user_data import UserDataManager
from core.text_utils import normalize_group_name
from core.i18n import i18n, I18n

from .constants import DialogDataKeys, WidgetIds
from .states import About, MainMenu, Schedule


async def get_main_menu_data(dialog_manager: DialogManager, **kwargs):
    """
    Выбирает случайную группу для примера в приветственном сообщении.
    """
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    # Получаем список групп, исключая служебные ключи
    groups = [g for g in manager._schedules.keys() if not g.startswith("__")]

    # Выбираем случайную группу или используем запасной вариант
    random_group = random.choice(groups) if groups else "О735Б"

    user_id = dialog_manager.event.from_user.id
    user_data_manager: UserDataManager = dialog_manager.middleware_data.get("user_data_manager")
    user_lang = await user_data_manager.get_user_language(user_id)

    # Реинициализируем функцию перевода с актуальным языком из БД
    _ = lambda k, **kw: i18n.get(k, lang=user_lang, **kw)

    return {
        "random_group": random_group,
        "welcome_title": _("welcome_title"),
        "role_student": _("role_student"),
        "role_teacher": _("role_teacher"),
        "reg_student_title": _("reg_student_title", random_group=random_group),
        "reg_teacher_title": _("reg_teacher_title"),
        "back_to_role": _("back_to_role"),
        "reg_complete": _("reg_complete", group=dialog_manager.dialog_data.get(DialogDataKeys.GROUP, "???")),
        "show_tutorial": _("show_tutorial"),
        "start_using": _("start_using"),
        "lang_select_title": _("lang_select_title"),
        "btn_change_lang": _("btn_change_lang"),
        "ru_checked": "✅" if user_lang == "ru" else "",
        "en_checked": "✅" if user_lang == "en" else "",
        "zh_checked": "✅" if user_lang == "zh" else "",
    }



async def on_lang_selected(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Обрабатывает выбор языка при первом запуске."""
    lang_code = button.widget_id.replace("lang_", "")
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    await user_data_manager.set_user_language(callback.from_user.id, lang_code)
    
    # После выбора языка переходим к выбору роли
    await manager.switch_to(MainMenu.choose_user_type)


async def on_user_type_selected(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Обрабатывает выбор типа пользователя (студент/преподаватель)."""
    user_type = callback.data.replace("user_type_", "")  # Извлекаем тип из callback_data (student/teacher)
    manager.dialog_data["user_type"] = user_type

    if user_type == "student":
        await manager.switch_to(MainMenu.enter_group)
    elif user_type == "teacher":
        await manager.switch_to(MainMenu.enter_teacher)


async def on_group_entered(message: Message, message_input: MessageInput, manager: DialogManager):
    # Нормализуем ввод с помощью text_utils
    raw = message.text or ""
    group_name = normalize_group_name(raw)
    
    # Получаем локализацию
    _ = manager.middleware_data.get("_")
    
    if not group_name:
        await message.answer(_("incorrect_group"))
        return
    timetable_manager: TimetableManager = manager.middleware_data.get("manager")
    all_groups = [g for g in timetable_manager._schedules.keys() if not g.startswith("__")]

    # Проверяем прямое совпадение
    if group_name not in all_groups:
        # Если прямого совпадения нет, ищем похожие
        suggestions = process.extract(group_name, all_groups, limit=3)
        good_suggestions = [s[0] for s in suggestions if s[1] > 75]

        if good_suggestions:
            # Форматируем каждый предложенный вариант
            formatted_suggestions = [f"<code>{s}</code>" for s in good_suggestions]
            # Соединяем варианты
            suggestion_text = ", ".join(formatted_suggestions)
            await message.answer(_("group_suggestion", group=group_name, suggestions=suggestion_text))
        else:
            # Если нет даже похожих
            await message.answer(_("group_not_found", group=group_name))
        return  # В любом случае, если не было точного совпадения, выходим

    # Этот код выполнится только если было точное совпадение
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    await user_data_manager.register_user(user_id=message.from_user.id, username=message.from_user.username)
    await user_data_manager.set_user_group(user_id=message.from_user.id, group=group_name)
    await user_data_manager.set_user_type(user_id=message.from_user.id, user_type="student")

    manager.dialog_data[DialogDataKeys.GROUP] = group_name
    await manager.switch_to(MainMenu.offer_tutorial)


async def on_teacher_entered(message: Message, message_input: MessageInput, manager: DialogManager):
    """Обрабатывает ввод ФИО преподавателя."""
    _ = manager.middleware_data.get("_", lambda k, **kw: k)
    
    teacher_name = (message.text or "").strip()
    # TODO: Add teacher name normalization if needed
    
    if not teacher_name or len(teacher_name) < 3:
        # TODO: Move string to locales with key "incorrect_teacher"
        # For now reusing generic or keeping hardcoded until locale update
        await message.answer(
             "❌ <b>Некорректный ввод!</b>\n\n"
             "📝 Введите <b>полное ФИО преподавателя</b> (минимум 3 символа)\n"
             "💡 <i>Например: Иванов Иван Иванович или Петров И.И.</i>"
        )
        return

    timetable_manager: TimetableManager = manager.middleware_data.get("manager")

    # Резолвим каноническое имя через TimetableManager
    canonical = timetable_manager.resolve_canonical_teacher(teacher_name)
    if not canonical:
        # Предложим несколько ближайших вариантов
        suggestions = timetable_manager.find_teachers_fuzzy(teacher_name, limit=5, score_cutoff=55)
        if suggestions:
            formatted_suggestions = [f"<code>{s}</code>" for s in suggestions[:3]]
            suggestion_text = "\n".join(formatted_suggestions)
            await message.answer(_("teacher_suggestion", suggestions=suggestion_text))
            return
        else:
            await message.answer(_("teacher_not_found", name=teacher_name))
            return
            
    teacher_name = canonical

    # Регистрируем преподавателя
    user_data_manager: UserDataManager = manager.middleware_data.get("user_data_manager")
    await user_data_manager.register_user(user_id=message.from_user.id, username=message.from_user.username)
    await user_data_manager.set_user_group(user_id=message.from_user.id, group=teacher_name)
    await user_data_manager.set_user_type(user_id=message.from_user.id, user_type="teacher")

    manager.dialog_data[DialogDataKeys.GROUP] = teacher_name
    await manager.switch_to(MainMenu.offer_tutorial)


async def on_skip_tutorial_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    group_name = manager.dialog_data.get(DialogDataKeys.GROUP)
    await manager.start(
        Schedule.view,
        data={DialogDataKeys.GROUP: group_name},
        mode=StartMode.RESET_STACK,
    )


async def on_show_tutorial_clicked(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(About.page_1, mode=StartMode.RESET_STACK)


dialog = Dialog(
    # Окно выбора языка (теперь первое в onboarding)
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Format("{lang_select_title}"),
        Column(
            Button(
                Format("🇷🇺 Русский {ru_checked}"),
                id="lang_ru",
                on_click=on_lang_selected,
            ),
            Button(
                Format("🇬🇧 English {en_checked}"),
                id="lang_en",
                on_click=on_lang_selected,
            ),
            Button(
                Format("🇨🇳 中文 {zh_checked}"),
                id="lang_zh",
                on_click=on_lang_selected,
            ),
        ),
        state=MainMenu.select_language,
        getter=get_main_menu_data,
        parse_mode="HTML",
    ),
    # Окно выбора типа пользователя - улучшенный UI
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Format("{welcome_title}"),
        Column(
            Button(
                Format("{role_student}"),
                id="user_type_student",
                on_click=on_user_type_selected,
            ),
            Button(
                Format("{role_teacher}"),
                id="user_type_teacher",
                on_click=on_user_type_selected,
            ),
        ),
        Button(
            Format("{btn_change_lang}"),
            id="back_to_lang",
            on_click=lambda c, b, m: m.switch_to(MainMenu.select_language),
        ),
        state=MainMenu.choose_user_type,
        getter=get_main_menu_data,
        parse_mode="HTML",
    ),
    # Окно ввода группы для студентов - улучшенный UI
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Format("{reg_student_title}"),
        MessageInput(on_group_entered),
        Button(
            Format("{back_to_role}"),
            id="back_to_role",
            on_click=lambda c, b, m: m.switch_to(MainMenu.choose_user_type),
        ),
        state=MainMenu.enter_group,
        getter=get_main_menu_data,
        parse_mode="HTML",
    ),
    # Окно ввода ФИО для преподавателей - улучшенный UI
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Format("{reg_teacher_title}"),
        MessageInput(on_teacher_entered),
        Button(
            Format("{back_to_role}"),
            id="back_to_role_teacher",
            on_click=lambda c, b, m: m.switch_to(MainMenu.choose_user_type),
        ),
        state=MainMenu.enter_teacher,
        getter=get_main_menu_data,
        parse_mode="HTML",
    ),
    Window(
        Format("{reg_complete}"),
        Row(
            Button(
                Format("{show_tutorial}"),
                id=WidgetIds.SHOW_TUTORIAL,
                on_click=on_show_tutorial_clicked,
            ),
            Button(
                Format("{start_using}"),
                id=WidgetIds.SKIP_TUTORIAL,
                on_click=on_skip_tutorial_clicked,
            ),
        ),
        state=MainMenu.offer_tutorial,
        getter=get_main_menu_data,
    ),
)
