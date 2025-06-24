import json
import os
from datetime import date, datetime, timedelta
from core.config import CACHE_FILENAME, CACHE_LIFETIME, DAY_MAP

class TimetableManager:
    """Управляет полным расписанием и предоставляет методы для доступа к нему."""
    def __init__(self, all_schedules_data: dict):
        if not all_schedules_data:
            raise ValueError("Данные расписания не могут быть пустыми.")
        
        self.metadata = all_schedules_data.get('__metadata__', {})
        self._schedules = {k: v for k, v in all_schedules_data.items() if k != '__metadata__'}
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
        """Сохраняет полное расписание в кэш."""
        print(f"Сохранение расписания в кэш: {CACHE_FILENAME}")
        data_to_save = {'__metadata__': self.metadata, **self._schedules}
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
            'lessons': lessons
        }