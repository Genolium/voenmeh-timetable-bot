import pytest
from datetime import date
from core.schedule_diff import (
    ScheduleDiffDetector, ScheduleDiffFormatter, 
    ChangeType, LessonChange, GroupScheduleDiff
)


class TestScheduleDiffDetector:
    """Тесты для детектора изменений в расписании."""
    
    def test_compare_day_schedules_no_changes(self):
        """Тест сравнения одинаковых расписаний."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'},
                {'time': '10:40-12:10', 'subject': 'Физика', 'room': '102', 'teachers': 'Петров'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'},
                {'time': '10:40-12:10', 'subject': 'Физика', 'room': '102', 'teachers': 'Петров'}
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 0
    
    def test_compare_day_schedules_lesson_added(self):
        """Тест добавления новой пары."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'},
                {'time': '10:40-12:10', 'subject': 'Физика', 'room': '102', 'teachers': 'Петров'}
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.LESSON_ADDED
        assert changes[0].subject == 'Физика'
        assert changes[0].time == '10:40-12:10'
        assert changes[0].room == '102'
        assert changes[0].teacher == 'Петров'
    
    def test_compare_day_schedules_lesson_removed(self):
        """Тест удаления пары."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'},
                {'time': '10:40-12:10', 'subject': 'Физика', 'room': '102', 'teachers': 'Петров'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.LESSON_REMOVED
        assert changes[0].subject == 'Физика'
        assert changes[0].time == '10:40-12:10'
    
    def test_compare_day_schedules_room_changed(self):
        """Тест изменения аудитории."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '201', 'teachers': 'Иванов'}
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.ROOM_CHANGED
        assert changes[0].subject == 'Математика'
        assert changes[0].old_value == '101'
        assert changes[0].new_value == '201'
    
    def test_compare_day_schedules_teacher_changed(self):
        """Тест изменения преподавателя."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Сидоров'}
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.TEACHER_CHANGED
        assert changes[0].subject == 'Математика'
        assert changes[0].old_value == 'Иванов'
        assert changes[0].new_value == 'Сидоров'
    
    def test_compare_day_schedules_type_changed(self):
        """Тест изменения типа пары."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'type': 'Лекция', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'type': 'Семинар', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.TYPE_CHANGED
        assert changes[0].subject == 'Математика'
        assert changes[0].old_value == 'Лекция'
        assert changes[0].new_value == 'Семинар'
    
    def test_compare_day_schedules_multiple_changes(self):
        """Тест множественных изменений."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'},
                {'time': '10:40-12:10', 'subject': 'Физика', 'room': '102', 'teachers': 'Петров'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '201', 'teachers': 'Сидоров'},  # Изменены аудитория и преподаватель
                {'time': '12:20-13:50', 'subject': 'Химия', 'room': '103', 'teachers': 'Козлов'}  # Новая пара
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        
        # Должно быть: удаление Физики, добавление Химии, изменение аудитории и преподавателя у Математики
        assert len(changes) == 4
        
        change_types = [change.change_type for change in changes]
        assert ChangeType.LESSON_REMOVED in change_types
        assert ChangeType.LESSON_ADDED in change_types
        assert ChangeType.ROOM_CHANGED in change_types
        assert ChangeType.TEACHER_CHANGED in change_types
    
    def test_compare_day_schedules_empty_schedules(self):
        """Тест сравнения пустых расписаний."""
        old_schedule = {'lessons': []}
        new_schedule = {'lessons': []}
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 0
    
    def test_compare_day_schedules_with_errors(self):
        """Тест сравнения расписаний с ошибками."""
        old_schedule = {'error': 'Группа не найдена'}
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        
        changes = ScheduleDiffDetector.compare_day_schedules(old_schedule, new_schedule)
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.LESSON_ADDED
    
    def test_compare_group_schedules(self):
        """Тест сравнения расписаний группы."""
        old_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '101', 'teachers': 'Иванов'}
            ]
        }
        new_schedule = {
            'lessons': [
                {'time': '09:00-10:30', 'subject': 'Математика', 'room': '201', 'teachers': 'Иванов'}
            ]
        }
        
        diff = ScheduleDiffDetector.compare_group_schedules(
            group="О735Б",
            target_date=date(2025, 9, 1),
            old_schedule_data=old_schedule,
            new_schedule_data=new_schedule
        )
        
        assert diff.group == "О735Б"
        assert diff.date == date(2025, 9, 1)
        assert diff.has_changes()
        assert len(diff.changes) == 1
        assert diff.changes[0].change_type == ChangeType.ROOM_CHANGED


class TestScheduleDiffFormatter:
    """Тесты для форматтера уведомлений об изменениях."""
    
    def test_format_change_lesson_added(self):
        """Тест форматирования добавления пары."""
        change = LessonChange(
            change_type=ChangeType.LESSON_ADDED,
            lesson_id="09:00-10:30_Математика",
            subject="Математика",
            time="09:00-10:30",
            room="101",
            teacher="Иванов"
        )
        
        formatted = ScheduleDiffFormatter.format_change(change)
        assert "➕" in formatted
        assert "Добавлена пара" in formatted
        assert "Математика" in formatted
        assert "09:00-10:30" in formatted
        assert "📍 101" in formatted
        assert "🧑‍🏫 Иванов" in formatted
    
    def test_format_change_lesson_removed(self):
        """Тест форматирования удаления пары."""
        change = LessonChange(
            change_type=ChangeType.LESSON_REMOVED,
            lesson_id="10:40-12:10_Физика",
            subject="Физика",
            time="10:40-12:10"
        )
        
        formatted = ScheduleDiffFormatter.format_change(change)
        assert "❌" in formatted
        assert "Отменена пара" in formatted
        assert "Физика" in formatted
        assert "10:40-12:10" in formatted
    
    def test_format_change_room_changed(self):
        """Тест форматирования изменения аудитории."""
        change = LessonChange(
            change_type=ChangeType.ROOM_CHANGED,
            lesson_id="09:00-10:30_Математика",
            subject="Математика",
            time="09:00-10:30",
            old_value="101",
            new_value="201"
        )
        
        formatted = ScheduleDiffFormatter.format_change(change)
        assert "📍" in formatted
        assert "Изменена аудитория" in formatted
        assert "Математика" in formatted
        assert "101" in formatted
        assert "201" in formatted
        assert "→" in formatted
    
    def test_format_change_teacher_changed(self):
        """Тест форматирования изменения преподавателя."""
        change = LessonChange(
            change_type=ChangeType.TEACHER_CHANGED,
            lesson_id="09:00-10:30_Математика",
            subject="Математика",
            time="09:00-10:30",
            old_value="Иванов",
            new_value="Сидоров"
        )
        
        formatted = ScheduleDiffFormatter.format_change(change)
        assert "🧑‍🏫" in formatted
        assert "Изменен преподаватель" in formatted
        assert "Математика" in formatted
        assert "Иванов" in formatted
        assert "Сидоров" in formatted
    
    def test_format_group_diff_with_changes(self):
        """Тест форматирования изменений группы."""
        changes = [
            LessonChange(
                change_type=ChangeType.LESSON_ADDED,
                lesson_id="09:00-10:30_Математика",
                subject="Математика",
                time="09:00-10:30",
                room="101"
            ),
            LessonChange(
                change_type=ChangeType.ROOM_CHANGED,
                lesson_id="10:40-12:10_Физика",
                subject="Физика",
                time="10:40-12:10",
                old_value="102",
                new_value="202"
            )
        ]
        
        diff = GroupScheduleDiff(
            group="О735Б",
            date=date(2025, 9, 1),
            changes=changes
        )
        
        formatted = ScheduleDiffFormatter.format_group_diff(diff)
        assert formatted is not None
        assert "🔔" in formatted
        assert "Изменения в расписании О735Б" in formatted
        assert "01.09.2025" in formatted
        assert "Добавлена пара" in formatted
        assert "Изменена аудитория" in formatted
        assert "Проверьте актуальное расписание" in formatted
    
    def test_format_group_diff_no_changes(self):
        """Тест форматирования группы без изменений."""
        diff = GroupScheduleDiff(
            group="О735Б",
            date=date(2025, 9, 1),
            changes=[]
        )
        
        formatted = ScheduleDiffFormatter.format_group_diff(diff)
        assert formatted is None
    
    def test_format_group_diff_multiple_same_type_changes(self):
        """Тест форматирования множественных изменений одного типа."""
        changes = [
            LessonChange(
                change_type=ChangeType.LESSON_ADDED,
                lesson_id="09:00-10:30_Математика",
                subject="Математика",
                time="09:00-10:30"
            ),
            LessonChange(
                change_type=ChangeType.LESSON_ADDED,
                lesson_id="10:40-12:10_Физика",
                subject="Физика",
                time="10:40-12:10"
            ),
            LessonChange(
                change_type=ChangeType.LESSON_REMOVED,
                lesson_id="12:20-13:50_Химия",
                subject="Химия",
                time="12:20-13:50"
            )
        ]
        
        diff = GroupScheduleDiff(
            group="О735Б",
            date=date(2025, 9, 1),
            changes=changes
        )
        
        formatted = ScheduleDiffFormatter.format_group_diff(diff)
        assert formatted is not None
        
        # Проверяем, что все изменения включены
        assert formatted.count("➕") == 2  # Две добавленные пары
        assert formatted.count("❌") == 1  # Одна удаленная пара
        assert "Математика" in formatted
        assert "Физика" in formatted
        assert "Химия" in formatted
