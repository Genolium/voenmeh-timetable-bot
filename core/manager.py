import json
import os
from datetime import date, datetime, timedelta 
from core.config import CACHE_FILENAME, CACHE_LIFETIME, DAY_MAP

class TimetableManager:
    """
    Управляет полным расписанием, включая индексы для быстрого поиска,
    и предоставляет методы для доступа к данным.
    """
    def __init__(self, all_schedules_data: dict):
        if not all_schedules_data:
            raise ValueError("Данные расписания не могут быть пустыми.")
        
        self.metadata = all_schedules_data.get('__metadata__', {})
        self._schedules = {k: v for k, v in all_schedules_data.items() if not k.startswith('__')}
        self._teachers_index = all_schedules_data.get('__teachers_index__', {})
        self._classrooms_index = all_schedules_data.get('__classrooms_index__', {})
        self._current_xml_hash = all_schedules_data.get('__current_xml_hash__', '')
        self.semester_start_date = None
        
        if 'period' in self.metadata:
            try:
                period = self.metadata['period']
                self.semester_start_date = date(int(period['StartYear']), int(period['StartMonth']), int(period['StartDay']))
            except (KeyError, ValueError):
                print("Предупреждение: не удалось разобрать дату начала семестра.")

    @classmethod
    def load_from_cache(cls):
        """Загружает полное расписание из кэша, если он существует и не устарел."""
        if not os.path.exists(CACHE_FILENAME):
            return None
        
        try:
            file_mod_time = os.path.getmtime(CACHE_FILENAME)
            cache_age = datetime.now() - datetime.fromtimestamp(file_mod_time)

            if cache_age > CACHE_LIFETIME:
                print(f"Кэш устарел (возраст: {str(cache_age).split('.')[0]}). Будет выполнен новый запрос.")
                return None

            print(f"Загрузка расписания из свежего кэша (возраст: {str(cache_age).split('.')[0]})")
            
            with open(CACHE_FILENAME, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print("Данные из кэша успешно загружены.")
            return cls(data)
        except Exception as e:
            print(f"Ошибка чтения кэша или проверки его возраста: {e}. Будет выполнен новый запрос.")
            return None

    def save_to_cache(self):
        """Сохраняет полное расписание, включая индексы и хеш, в кэш."""
        print(f"Сохранение расписания в кэш: {CACHE_FILENAME}")
        data_to_save = {
            '__metadata__': self.metadata, 
            '__teachers_index__': self._teachers_index,
            '__classrooms_index__': self._classrooms_index,
            '__current_xml_hash__': self._current_xml_hash,
            **self._schedules
        }
        with open(CACHE_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)

    def get_week_type(self, target_date: date) -> tuple[str, str] | None:
        """Определяет тип недели ('odd'/'even') для указанной даты."""
        if not self.semester_start_date:
            return None
        if target_date < self.semester_start_date:
            return ('odd', 'Нечетная (до начала семестра)')

        week_number = ((target_date - self.semester_start_date).days // 7) + 1
        return ('odd', 'Нечетная') if week_number % 2 == 1 else ('even', 'Четная')

    def get_schedule_for_day(self, group_number: str, target_date: date = None) -> dict | None:
        """Возвращает расписание для группы на конкретный день."""
        target_date = target_date or date.today()
        group_schedule = self._schedules.get(group_number.upper())
        if not group_schedule:
            return {'error': f"Группа '{group_number}' не найдена."}

        week_info = self.get_week_type(target_date)
        if not week_info:
            return {'error': "Не удалось определить тип недели."}
        
        week_key, week_name = week_info
        day_name = DAY_MAP[target_date.weekday()]

        lessons = group_schedule.get(week_key, {}).get(day_name, []) if day_name else []

        return {
            'group': group_number.upper(),
            'date': target_date,
            'day_name': day_name or 'Воскресенье',
            'week_name': week_name,
            'lessons': sorted(lessons, key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        }
        
    def find_teachers(self, query: str) -> list[str]:
        """Находит фамилии преподавателей, содержащие поисковый запрос (регистронезависимо)."""
        if len(query) < 3:
            return []
        query_lower = query.lower()
        return sorted([name for name in self._teachers_index if query_lower in name.lower()])

    def get_teacher_schedule(self, teacher_name: str, target_date: date) -> dict | None:
        """Возвращает расписание преподавателя на конкретный день."""
        if teacher_name not in self._teachers_index:
            return {'error': f"Преподаватель '{teacher_name}' не найден в индексе."}
        
        week_info = self.get_week_type(target_date)
        if not week_info:
            return {'error': "Не удалось определить тип недели."}
        
        week_key_num, week_name = week_info
        day_name = DAY_MAP[target_date.weekday()]

        lessons_for_day = []
        if day_name:
            all_teacher_lessons = self._teachers_index.get(teacher_name, [])
            for lesson in all_teacher_lessons:
                if lesson.get('day') == day_name:
                    lesson_week_code = lesson.get('week_code', '0')
                    is_every_week = lesson_week_code == '0'
                    is_odd_match = week_key_num == 'odd' and lesson_week_code == '1'
                    is_even_match = week_key_num == 'even' and lesson_week_code == '2'
                    
                    if is_every_week or is_odd_match or is_even_match:
                        lessons_for_day.append(lesson)
        
        return {
            'teacher': teacher_name,
            'date': target_date,
            'day_name': day_name or 'Воскресенье',
            'week_name': week_name,
            'lessons': sorted(lessons_for_day, key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        }

    def find_classrooms(self, query: str) -> list[str]:
        """Находит аудитории, номер которых начинается с поискового запроса."""
        if not query: return []
        return sorted([number for number in self._classrooms_index if number.startswith(query)])

    def get_classroom_schedule(self, classroom_number: str, target_date: date) -> dict | None:
        """Возвращает расписание аудитории на конкретный день."""
        if classroom_number not in self._classrooms_index:
            return {'error': f"Аудитория '{classroom_number}' не найдена в индексе."}

        week_info = self.get_week_type(target_date)
        if not week_info:
            return {'error': "Не удалось определить тип недели."}
        
        week_key_num, week_name = week_info
        day_name = DAY_MAP[target_date.weekday()]
        
        lessons_for_day = []
        if day_name:
            all_classroom_lessons = self._classrooms_index.get(classroom_number, [])
            for lesson in all_classroom_lessons:
                if lesson.get('day') == day_name:
                    lesson_week_code = lesson.get('week_code', '0')
                    is_every_week = lesson_week_code == '0'
                    is_odd_match = week_key_num == 'odd' and lesson_week_code == '1'
                    is_even_match = week_key_num == 'even' and lesson_week_code == '2'

                    if is_every_week or is_odd_match or is_even_match:
                        lessons_for_day.append(lesson)

        return {
            'classroom': classroom_number,
            'date': target_date,
            'day_name': day_name or 'Воскресенье',
            'week_name': week_name,
            'lessons': sorted(lessons_for_day, key=lambda x: datetime.strptime(x['start_time_raw'], '%H:%M').time())
        }
    
    def get_current_xml_hash(self) -> str:
        """Возвращает хеш текущей версии XML расписания."""
        return self._current_xml_hash