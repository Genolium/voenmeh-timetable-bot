from aiogram.fsm.state import State, StatesGroup

class MainMenu(StatesGroup):
    enter_group = State()
    offer_tutorial = State()

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
    
class About(StatesGroup):
    page_1 = State() # Приветствие
    page_2 = State() # Основной экран
    page_3 = State() # Поиск
    page_4 = State() # Уведомления
    page_5 = State() # Inline режим