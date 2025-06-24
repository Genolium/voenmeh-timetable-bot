from aiogram.fsm.state import State, StatesGroup

class MainMenu(StatesGroup):
    enter_group = State()

class Schedule(StatesGroup):
    view = State()
    full_week_view = State()

class SettingsMenu(StatesGroup):
    main = State()
    
class FindMenu(StatesGroup):
    choice = State() 
    enter_teacher = State()
    enter_classroom = State()
    select_item = State()
    view_result = State()