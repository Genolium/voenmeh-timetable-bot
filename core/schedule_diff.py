"""
Модуль для обнаружения и форматирования изменений в расписании.
Позволяет отправлять пользователям только уведомления о реальных изменениях.
"""

import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple, Set, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """Типы изменений в расписании."""
    LESSON_ADDED = "added"
    LESSON_REMOVED = "removed"
    TIME_CHANGED = "time_changed"
    ROOM_CHANGED = "room_changed"
    TEACHER_CHANGED = "teacher_changed"
    SUBJECT_CHANGED = "subject_changed"
    TYPE_CHANGED = "type_changed"


@dataclass
class LessonChange:
    """Описание изменения в паре."""
    change_type: ChangeType
    lesson_id: str  # Уникальный идентификатор пары (время + предмет)
    subject: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    time: Optional[str] = None
    room: Optional[str] = None
    teacher: Optional[str] = None


@dataclass
class GroupScheduleDiff:
    """Различия в расписании группы."""
    group: str
    date: date
    changes: List[LessonChange]
    
    def has_changes(self) -> bool:
        """Проверяет, есть ли реальные изменения."""
        return len(self.changes) > 0


class ScheduleDiffDetector:
    """Детектор изменений в расписании."""
    
    @staticmethod
    def _normalize_lesson(lesson: Dict[str, Any]) -> Dict[str, str]:
        """Нормализует данные пары для сравнения."""
        return {
            'time': lesson.get('time', '').strip(),
            'subject': lesson.get('subject', '').strip(),
            'type': lesson.get('type', '').strip(),
            'room': lesson.get('room', '').strip(),
            'teachers': lesson.get('teachers', '').strip(),
        }
    
    @staticmethod
    def _get_lesson_id(lesson: Dict[str, str]) -> str:
        """Создает уникальный идентификатор пары."""
        return f"{lesson['time']}_{lesson['subject']}"
    
    @classmethod
    def compare_day_schedules(
        cls, 
        old_schedule: Optional[Dict[str, Any]], 
        new_schedule: Optional[Dict[str, Any]]
    ) -> List[LessonChange]:
        """
        Сравнивает расписания на день и возвращает список изменений.
        
        Args:
            old_schedule: Старое расписание на день
            new_schedule: Новое расписание на день
            
        Returns:
            Список изменений в расписании
        """
        changes = []
        
        # Обработка случаев отсутствия расписания
        if not old_schedule or old_schedule.get('error'):
            old_lessons = []
        else:
            old_lessons = old_schedule.get('lessons', [])
            
        if not new_schedule or new_schedule.get('error'):
            new_lessons = []
        else:
            new_lessons = new_schedule.get('lessons', [])
        
        # Нормализуем пары
        old_normalized = {cls._get_lesson_id(cls._normalize_lesson(lesson)): cls._normalize_lesson(lesson) 
                         for lesson in old_lessons}
        new_normalized = {cls._get_lesson_id(cls._normalize_lesson(lesson)): cls._normalize_lesson(lesson) 
                         for lesson in new_lessons}
        
        old_ids = set(old_normalized.keys())
        new_ids = set(new_normalized.keys())
        
        # Найдем добавленные пары
        added_ids = new_ids - old_ids
        for lesson_id in added_ids:
            lesson = new_normalized[lesson_id]
            changes.append(LessonChange(
                change_type=ChangeType.LESSON_ADDED,
                lesson_id=lesson_id,
                subject=lesson['subject'],
                time=lesson['time'],
                room=lesson['room'],
                teacher=lesson['teachers']
            ))
        
        # Найдем удаленные пары
        removed_ids = old_ids - new_ids
        for lesson_id in removed_ids:
            lesson = old_normalized[lesson_id]
            changes.append(LessonChange(
                change_type=ChangeType.LESSON_REMOVED,
                lesson_id=lesson_id,
                subject=lesson['subject'],
                time=lesson['time'],
                room=lesson['room'],
                teacher=lesson['teachers']
            ))
        
        # Найдем изменения в существующих парах
        common_ids = old_ids & new_ids
        for lesson_id in common_ids:
            old_lesson = old_normalized[lesson_id]
            new_lesson = new_normalized[lesson_id]
            
            # Проверяем изменения в каждом поле
            if old_lesson['room'] != new_lesson['room']:
                changes.append(LessonChange(
                    change_type=ChangeType.ROOM_CHANGED,
                    lesson_id=lesson_id,
                    subject=new_lesson['subject'],
                    time=new_lesson['time'],
                    old_value=old_lesson['room'] or 'не указана',
                    new_value=new_lesson['room'] or 'не указана'
                ))
            
            if old_lesson['teachers'] != new_lesson['teachers']:
                changes.append(LessonChange(
                    change_type=ChangeType.TEACHER_CHANGED,
                    lesson_id=lesson_id,
                    subject=new_lesson['subject'],
                    time=new_lesson['time'],
                    old_value=old_lesson['teachers'] or 'не указан',
                    new_value=new_lesson['teachers'] or 'не указан'
                ))
            
            if old_lesson['type'] != new_lesson['type']:
                changes.append(LessonChange(
                    change_type=ChangeType.TYPE_CHANGED,
                    lesson_id=lesson_id,
                    subject=new_lesson['subject'],
                    time=new_lesson['time'],
                    old_value=old_lesson['type'] or 'не указан',
                    new_value=new_lesson['type'] or 'не указан'
                ))
        
        return changes
    
    @classmethod
    def compare_group_schedules(
        cls,
        group: str,
        target_date: date,
        old_schedule_data: Optional[Dict[str, Any]],
        new_schedule_data: Optional[Dict[str, Any]]
    ) -> GroupScheduleDiff:
        """
        Сравнивает расписания группы на конкретную дату.
        
        Args:
            group: Название группы
            target_date: Дата для сравнения
            old_schedule_data: Старые данные расписания
            new_schedule_data: Новые данные расписания
            
        Returns:
            Объект с различиями в расписании
        """
        changes = cls.compare_day_schedules(old_schedule_data, new_schedule_data)
        
        return GroupScheduleDiff(
            group=group,
            date=target_date,
            changes=changes
        )


class ScheduleDiffFormatter:
    """Форматтер для уведомлений об изменениях в расписании."""
    
    CHANGE_ICONS = {
        ChangeType.LESSON_ADDED: "➕",
        ChangeType.LESSON_REMOVED: "❌",
        ChangeType.TIME_CHANGED: "⏰",
        ChangeType.ROOM_CHANGED: "📍",
        ChangeType.TEACHER_CHANGED: "🧑‍🏫",
        ChangeType.SUBJECT_CHANGED: "📚",
        ChangeType.TYPE_CHANGED: "🔄",
    }
    
    CHANGE_DESCRIPTIONS = {
        ChangeType.LESSON_ADDED: "Добавлена пара",
        ChangeType.LESSON_REMOVED: "Отменена пара",
        ChangeType.TIME_CHANGED: "Изменено время",
        ChangeType.ROOM_CHANGED: "Изменена аудитория",
        ChangeType.TEACHER_CHANGED: "Изменен преподаватель",
        ChangeType.SUBJECT_CHANGED: "Изменен предмет",
        ChangeType.TYPE_CHANGED: "Изменен тип пары",
    }
    
    @classmethod
    def format_change(cls, change: LessonChange) -> str:
        """Форматирует одно изменение в читаемый текст."""
        icon = cls.CHANGE_ICONS.get(change.change_type, "🔄")
        description = cls.CHANGE_DESCRIPTIONS.get(change.change_type, "Изменение")
        
        if change.change_type == ChangeType.LESSON_ADDED:
            parts = [f"<b>{change.subject}</b>"]
            if change.time:
                parts.append(f"в <b>{change.time}</b>")
            if change.room:
                parts.append(f"📍 {change.room}")
            if change.teacher:
                parts.append(f"🧑‍🏫 {change.teacher}")
            return f"{icon} {description}: {' '.join(parts)}"
        
        elif change.change_type == ChangeType.LESSON_REMOVED:
            parts = [f"<b>{change.subject}</b>"]
            if change.time:
                parts.append(f"в <b>{change.time}</b>")
            return f"{icon} {description}: {' '.join(parts)}"
        
        else:  # Изменения в существующих парах
            base = f"{icon} {description} для <b>{change.subject}</b>"
            if change.time:
                base += f" в <b>{change.time}</b>"
            
            if change.old_value and change.new_value:
                base += f": <i>{change.old_value}</i> → <b>{change.new_value}</b>"
            
            return base
    
    @classmethod
    def format_group_diff(cls, diff: GroupScheduleDiff) -> Optional[str]:
        """
        Форматирует изменения в расписании группы для отправки пользователям.
        
        Args:
            diff: Различия в расписании группы
            
        Returns:
            Отформатированное сообщение или None, если изменений нет
        """
        if not diff.has_changes():
            return None
        
        date_str = diff.date.strftime('%d.%m.%Y')
        header = f"🔔 <b>Изменения в расписании {diff.group}</b>\n📅 <b>{date_str}</b>\n\n"
        
        # Группируем изменения по типам для лучшей читаемости
        changes_by_type = {}
        for change in diff.changes:
            if change.change_type not in changes_by_type:
                changes_by_type[change.change_type] = []
            changes_by_type[change.change_type].append(change)
        
        # Форматируем изменения
        formatted_changes = []
        for change_type in [ChangeType.LESSON_ADDED, ChangeType.LESSON_REMOVED, 
                           ChangeType.TIME_CHANGED, ChangeType.ROOM_CHANGED, 
                           ChangeType.TEACHER_CHANGED, ChangeType.TYPE_CHANGED]:
            if change_type in changes_by_type:
                for change in changes_by_type[change_type]:
                    formatted_changes.append(cls.format_change(change))
        
        if not formatted_changes:
            return None
        
        message = header + "\n".join(formatted_changes)
        message += "\n\n<i>Проверьте актуальное расписание в боте</i>"
        
        return message
