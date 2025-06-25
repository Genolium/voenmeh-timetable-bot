from datetime import date, timedelta, datetime, time
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, Window, DialogManager, StartMode
from aiogram_dialog.widgets.text import Format, Const
from aiogram_dialog.widgets.kbd import Button, Row, SwitchTo, Back
from aiogram_dialog.widgets.media import StaticMedia
from aiogram.enums import ContentType

from .states import Schedule, MainMenu, SettingsMenu, FindMenu
from core.manager import TimetableManager
from bot.utils import format_schedule_text, format_full_week_text
from core.config import MOSCOW_TZ, NO_LESSONS_IMAGE_PATH

def generate_dynamic_header(lessons: list, target_date: date) -> tuple[str, str]:
    """Генерирует контекстный заголовок и прогресс-бар с улучшенным UX."""
    is_today = target_date == datetime.now(MOSCOW_TZ).date()

    if is_today and not lessons:
        return "✨ <b>Сегодня занятий нет.</b> Отличного дня!", ""

    if not is_today or not lessons:
        return "", ""

    sorted_lessons = sorted(lessons, key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
    now_time = datetime.now(MOSCOW_TZ).time()
    
    passed_lessons_count = sum(1 for lesson in sorted_lessons if now_time > datetime.strptime(lesson['end_time_raw'], '%H:%M').time())
    total_lessons = len(sorted_lessons)
    progress_bar_emojis = '🟩' * passed_lessons_count + '⬜️' * (total_lessons - passed_lessons_count)
    progress_bar = f"<i>Прогресс дня: {passed_lessons_count}/{total_lessons}</i> {progress_bar_emojis}\n"

    first_lesson_start = datetime.strptime(sorted_lessons[0]['start_time_raw'], '%H:%M').time()
    last_lesson_end = datetime.strptime(sorted_lessons[-1]['end_time_raw'], '%H:%M').time()

    if now_time < first_lesson_start:
        header = f"☀️ <b>Доброе утро!</b> Первая пара в {sorted_lessons[0]['time'].split('–')[0].strip()}."
        return header, progress_bar

    if now_time > last_lesson_end:
        header = "✅ <b>Пары на сегодня закончились.</b> Отдыхайте!"
        return header, progress_bar

    for i, lesson in enumerate(sorted_lessons):
        start_time = datetime.strptime(lesson['start_time_raw'], '%H:%M').time()
        end_time = datetime.strptime(lesson['end_time_raw'], '%H:%M').time()

        if start_time <= now_time <= end_time:
            header = f"⏳ <b>Идет пара:</b> {lesson['subject']}.\n Закончится в {lesson['time'].split('–')[1].strip()}."
            return header, progress_bar
        
        if i + 1 < len(sorted_lessons):
            next_lesson = sorted_lessons[i+1]
            # Получаем только время НАЧАЛА следующей пары
            next_start_time_str = next_lesson['time'].split('-')[0].strip()
            next_start_time_obj = datetime.strptime(next_lesson['start_time_raw'], '%H:%M').time()
            
            if end_time < now_time < next_start_time_obj:
                # Используем отформатированную строку времени начала
                header = f"☕️ <b>Перерыв до {next_start_time_str}.</b>\n Следующая пара: {next_lesson['subject']}."
                return header, progress_bar

    return "", progress_bar 


# --- Getters ---
async def get_schedule_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    ctx = dialog_manager.current_context()

    if "group" not in ctx.dialog_data:
        ctx.dialog_data["group"] = dialog_manager.start_data.get("group")
        
    if not ctx.dialog_data.get("current_date_iso"):
        today_in_moscow = datetime.now(MOSCOW_TZ).date()
        ctx.dialog_data["current_date_iso"] = today_in_moscow.isoformat()

    current_date = date.fromisoformat(ctx.dialog_data["current_date_iso"])
    group = ctx.dialog_data.get("group", "N/A")

    day_info = manager.get_schedule_for_day(group, target_date=current_date)
    
    dynamic_header, progress_bar = generate_dynamic_header(day_info.get("lessons", []), current_date)

    return {
        "dynamic_header": dynamic_header,
        "progress_bar": progress_bar,
        "schedule_text": format_schedule_text(day_info),
        "has_lessons": bool(day_info.get("lessons"))
    }

async def get_full_week_data(dialog_manager: DialogManager, **kwargs):
    manager: TimetableManager = dialog_manager.middleware_data.get("manager")
    ctx = dialog_manager.current_context()
    current_date = date.fromisoformat(ctx.dialog_data.get("current_date_iso", date.today().isoformat()))
    group = ctx.dialog_data.get("group")

    week_info = manager.get_week_type(current_date)
    if not week_info: return {"week_text": "Не удалось определить тип недели"}
    week_key, week_name = week_info

    full_schedule = manager._schedules.get(group, {})
    week_schedule = full_schedule.get(week_key, {})
    
    return { "week_text": format_full_week_text(week_schedule, f"{week_name} неделя") }

# --- Click Handlers ---
async def on_date_shift(callback: CallbackQuery, button: Button, manager: DialogManager, days: int):
    ctx = manager.current_context()
    current_date = date.fromisoformat(ctx.dialog_data.get("current_date_iso"))
    new_date = current_date + timedelta(days=days)
    ctx.dialog_data["current_date_iso"] = new_date.isoformat()

async def on_today_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    today_in_moscow = datetime.now(MOSCOW_TZ).date()
    manager.current_context().dialog_data["current_date_iso"] = today_in_moscow.isoformat()
    
async def on_change_group_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(MainMenu.enter_group, mode=StartMode.RESET_STACK)
    
async def on_settings_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(SettingsMenu.main)

async def on_find_click(callback: CallbackQuery, button: Button, manager: DialogManager):
    await manager.start(FindMenu.choice)

# --- Dialogs ---
schedule_dialog = Dialog(
    Window(
        StaticMedia(
            path=NO_LESSONS_IMAGE_PATH,
            type=ContentType.PHOTO,
            when=lambda data, widget, manager: not data.get("has_lessons")
        ),
        Format("{dynamic_header}"),
        Format("{progress_bar}"),
        Format("{schedule_text}"),
        Row(
            Button(Const("⏪"), id="prev_week", on_click=lambda c, b, m: on_date_shift(c, b, m, -7)),
            Button(Const("◀️"), id="prev_day", on_click=lambda c, b, m: on_date_shift(c, b, m, -1)),
            Button(Const("📅"), id="today", on_click=on_today_click),
            Button(Const("▶️"), id="next_day", on_click=lambda c, b, m: on_date_shift(c, b, m, 1)),
            Button(Const("⏩"), id="next_week", on_click=lambda c, b, m: on_date_shift(c, b, m, 7)),
        ),
        Row(
            SwitchTo(Const("🗓️ Вся неделя"), id="full_week", state=Schedule.full_week_view),
            Button(Const("🔄 Сменить группу"), id="change_group", on_click=on_change_group_click),
            Button(Const("⚙️ Настройки"), id="settings", on_click=on_settings_click),
        ),
        Button(Const("🔍 Поиск"), id="find_btn", on_click=on_find_click),
        state=Schedule.view,
        getter=get_schedule_data,
        parse_mode="HTML",
        disable_web_page_preview=True
    ),
    Window(
        Format("{week_text}"),
        Back(Const("◀️ Назад")),
        state=Schedule.full_week_view,
        getter=get_full_week_data,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
)