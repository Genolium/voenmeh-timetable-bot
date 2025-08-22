from aiogram.fsm.state import State, StatesGroup

class MainMenu(StatesGroup):
    enter_group = State()
    offer_tutorial = State()

class Schedule(StatesGroup):
    view = State()
    full_week_view = State()
    week_image_view = State()

class SettingsMenu(StatesGroup):
    main = State()
    reminders_time = State()
    
class FindMenu(StatesGroup):
    choice = State() 
    enter_teacher = State()
    enter_classroom = State()
    select_item = State()
    view_result = State()
    
class About(StatesGroup):
    page_1 = State()
    page_2 = State()
    page_3 = State()
    page_4 = State()
    page_5 = State()
    
class Feedback(StatesGroup):
    enter_feedback = State()

class Admin(StatesGroup):
    menu = State()
    sections = State()
    stats = State()
    broadcast = State()
    broadcast_menu = State()
    diagnostics_menu = State()
    cache_menu = State()
    enter_user_id = State()
    user_manage = State()
    change_group_confirm = State()
    segment_menu = State()
    template_input = State()
    preview = State()
    confirm_send = State()
    semester_settings = State()
    edit_fall_semester = State()
    edit_spring_semester = State()
    # New: categories and events management
    categories_menu = State()
    category_create = State()
    category_edit = State()
    events_menu = State()
    event_create = State()
    event_create_title = State()
    event_create_datetime = State()
    event_create_time = State()
    event_create_location = State()
    event_create_description = State()
    event_create_link = State()
    event_create_confirm = State()
    event_edit_menu = State()
    event_edit_title = State()
    event_edit_datetime = State()
    event_edit_time = State()
    event_edit_location = State()
    event_edit_description = State()
    event_edit_link = State()
    event_details = State()
    event_delete_confirm = State()
    event_edit_image = State()

class Events(StatesGroup):
    list = State()
    details = State()