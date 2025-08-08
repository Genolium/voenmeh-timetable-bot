from aiogram.fsm.state import State, StatesGroup

class MainMenu(StatesGroup):
    enter_group = State()
    offer_tutorial = State()

class Schedule(StatesGroup):
    view = State()
    full_week_view = State()

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
    stats = State()
    broadcast = State()
    enter_user_id = State()
    user_manage = State()
    change_group_confirm = State()
    segment_menu = State()
    template_input = State()
    preview = State()
    confirm_send = State()
    search_enter = State()
    search_results = State()