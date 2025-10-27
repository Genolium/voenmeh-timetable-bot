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

    return {"random_group": random_group}


async def on_user_type_selected(callback: CallbackQuery, button: Button, manager: DialogManager):
    """Обрабатывает выбор типа пользователя (студент/преподаватель)."""
    user_type = callback.data.replace("user_type_", "")  # Извлекаем тип из callback_data (student/teacher)
    manager.dialog_data["user_type"] = user_type

    if user_type == "student":
        await manager.switch_to(MainMenu.enter_group)
    elif user_type == "teacher":
        await manager.switch_to(MainMenu.enter_teacher)


async def on_group_entered(message: Message, message_input: MessageInput, manager: DialogManager):
    # Нормализуем ввод: оставляем только цифры/буквы, ограничиваем длину
    raw = (message.text or "").upper()
    group_name = re.sub(r"[^А-ЯA-Z0-9]", "", raw)[:20]
    if not group_name:
        await message.answer(
            "❌ <b>Некорректный ввод!</b>\n\n"
            "📝 Введите номер группы буквами и цифрами\n"
            "💡 <i>Например: О735Б, М123А, ИВТ-21</i>"
        )
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
            # Соединяем варианты через ", или " для правильного перечисления
            suggestion_text = ", или ".join(formatted_suggestions)
            await message.answer(
                f'🔍 <b>Группа "{group_name}" не найдена точно</b>\n\n'
                f"💡 <b>Возможно, вы имели в виду:</b>\n{suggestion_text}\n\n"
                f"📝 Попробуйте ввести один из предложенных вариантов"
            )
        else:
            # Если нет даже похожих, выводим стандартное сообщение
            await message.answer(
                f"❌ Группа <b>{group_name}</b> не найдена\n\n"
                f"🔍 <b>Проверьте правильность названия группы</b>\n"
                f"💡 <i>Попробуйте ввести только основную часть (например: О735 вместо О735Б)</i>\n\n"
                f"❓ <b>Нужна помощь?</b> Обратитесь к старосте или в деканат"
            )
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
    teacher_name = (message.text or "").strip()
    if not teacher_name or len(teacher_name) < 3:
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
            await message.answer(
                "🔍 <b>Преподаватель не найден точно</b>\n\n"
                f"💡 <b>Возможно, вы имели в виду:</b>\n{suggestion_text}\n\n"
                "📝 Скопируйте и отправьте один из предложенных вариантов"
            )
            return
        else:
            await message.answer(
                f'❌ <b>Преподаватель "{teacher_name}" не найден</b>\n\n'
                "🔍 <b>Проверьте правильность ФИО:</b>\n"
                "• Убедитесь в корректности написания\n"
                "• Попробуйте ввести только фамилию\n"
                "• Используйте полное ФИО без сокращений"
            )
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
    # Окно выбора типа пользователя - улучшенный UI
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Const("👋 <b>Добро пожаловать в бот расписания Военмеха!</b>\n\n" "🎯 <b>Выберите вашу роль:</b>"),
        Column(
            Button(
                Const("🎓 Я студент\n📚 Хочу смотреть расписание своей группы"),
                id="user_type_student",
                on_click=on_user_type_selected,
            ),
            Button(
                Const("🧑‍🏫 Я преподаватель\n📋 Хочу смотреть своё расписание"),
                id="user_type_teacher",
                on_click=on_user_type_selected,
            ),
        ),
        state=MainMenu.choose_user_type,
        parse_mode="HTML",
    ),
    # Окно ввода группы для студентов - улучшенный UI
    Window(
        StaticMedia(path=WELCOME_IMAGE_PATH),
        Format(
            "🎓 <b>Регистрация студента</b>\n\n"
            "📝 Введите <b>номер вашей группы</b>:\n"
            "💡 <i>Например: {random_group}</i>\n\n"
            "ℹ️ <i>Если не знаете точное название, введите приблизительно - я найду похожие варианты</i>"
        ),
        MessageInput(on_group_entered),
        Button(
            Const("⬅️ Назад к выбору роли"),
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
        Const(
            "🧑‍🏫 <b>Регистрация преподавателя</b>\n\n"
            "📝 Введите ваше <b>полное ФИО</b>:\n"
            "💡 <i>Например: Иванов Иван Иванович</i>\n\n"
            "ℹ️ <i>Система найдёт ваше расписание по ФИО в базе данных вуза</i>\n"
            "🔍 <i>Если точного совпадения нет, будут предложены похожие варианты</i>"
        ),
        MessageInput(on_teacher_entered),
        Button(
            Const("⬅️ Назад к выбору роли"),
            id="back_to_role_teacher",
            on_click=lambda c, b, m: m.switch_to(MainMenu.choose_user_type),
        ),
        state=MainMenu.enter_teacher,
        parse_mode="HTML",
    ),
    Window(
        Format(
            "🎉 <b>Регистрация завершена!</b>\n\n"
            "✅ Сохранено: <code>{dialog_data[group]}</code>\n\n"
            "🚀 <b>Теперь вам доступны:</b>\n"
            "📅 Просмотр расписания на любой день\n"
            "🔍 Поиск по преподавателям и аудиториям\n"
            "🔔 Умные напоминания о парах\n"
            "📊 Статистика и аналитика\n"
            "💬 Работа в групповых чатах\n\n"
            "❓ <b>Хотите узнать больше возможностей?</b>"
        ),
        Row(
            Button(
                Const("📖 Показать инструкцию"),
                id=WidgetIds.SHOW_TUTORIAL,
                on_click=on_show_tutorial_clicked,
            ),
            Button(
                Const("🚀 Начать пользоваться!"),
                id=WidgetIds.SKIP_TUTORIAL,
                on_click=on_skip_tutorial_clicked,
            ),
        ),
        state=MainMenu.offer_tutorial,
    ),
)
