from enum import Enum


class DialogDataKeys(str, Enum):
    """Ключи для хранения данных в `dialog_data`."""
    GROUP = "group"
    SEARCH_TYPE = "search_type"
    TEACHER_NAME = "teacher_name"
    CLASSROOM_NUMBER = "classroom_number"
    FOUND_ITEMS = "found_items"
    CURRENT_DATE_ISO = "current_date_iso"

class WidgetIds(str, Enum):
    """Идентификаторы (ID) для виджетов в диалогах."""
    # Settings
    EVENING_NOTIFY = "evening_notify"
    MORNING_SUMMARY = "morning_summary"
    LESSON_REMINDERS = "lesson_reminders"

    # Schedule View
    PREV_WEEK = "prev_week"
    PREV_DAY = "prev_day"
    TODAY = "today"
    NEXT_DAY = "next_day"
    NEXT_WEEK = "next_week"
    CHANGE_GROUP = "change_group"
    SETTINGS = "settings"
    FIND_BTN = "find_btn"
    FULL_WEEK = "full_week"

    # Find Menu
    FIND_TEACHER_BTN = "find_teacher_btn"
    FIND_CLASSROOM_BTN = "find_classroom_btn"
    BACK_TO_MAIN_SCHEDULE = "back_to_main_schedule"
    BACK_TO_CHOICE = "back_to_choice"
    SELECT_FOUND_ITEM = "select_found_item"

    # About Menu
    FINISH_TUTORIAL = "finish"

    # Main Menu
    SHOW_TUTORIAL = "show_tutorial"
    SKIP_TUTORIAL = "skip_tutorial"

    # Admin Menu
    STATS = "stats"
    BROADCAST = "broadcast"
    TEST_MORNING = "test_morning"
    TEST_EVENING = "test_evening"
    TEST_REMINDERS = "test_reminders"
    GENERATE_FULL_SCHEDULE = "generate_full_schedule"
    # Admin categories/events
    ADMIN_CATEGORIES = "admin_categories"
    ADMIN_EVENTS = "admin_events"
    # Events user menu
    EVENTS_OPEN = "events_open"

    # Themes
    THEME_STANDARD = "theme_standard"
    THEME_LIGHT = "theme_light"
    THEME_DARK = "theme_dark"
    THEME_CLASSIC = "theme_classic"
    THEME_COFFEE = "theme_coffee"