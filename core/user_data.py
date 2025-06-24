import aiosqlite
import logging
from typing import Optional, List, Tuple

from core.config import DATABASE_FILENAME

class UserDataManager:
    """Управляет данными и настройками пользователей с использованием SQLite."""
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _init_db(self):
        """
        Инициализирует БД. Создает таблицу users, если она не существует,
        и добавляет недостающие колонки для настроек для обратной совместимости.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('PRAGMA table_info(users)')
            columns = [row[1] for row in await cursor.fetchall()]
            
            if not columns:
                # Если таблицы нет совсем, создаем ее с нуля
                await db.execute('''
                    CREATE TABLE users (
                        user_id INTEGER PRIMARY KEY,
                        group_name TEXT NOT NULL,
                        evening_notify INTEGER DEFAULT 0,
                        morning_summary INTEGER DEFAULT 0,
                        lesson_reminders INTEGER DEFAULT 0
                    )
                ''')
                logging.info("Таблица 'users' успешно создана в базе данных.")
            else:
                # Миграция: добавляем колонки, если их нет
                if 'evening_notify' not in columns:
                    await db.execute('ALTER TABLE users ADD COLUMN evening_notify INTEGER DEFAULT 0')
                    logging.info("Добавлена колонка 'evening_notify' в таблицу 'users'.")
                if 'morning_summary' not in columns:
                    await db.execute('ALTER TABLE users ADD COLUMN morning_summary INTEGER DEFAULT 0')
                    logging.info("Добавлена колонка 'morning_summary' в таблицу 'users'.")
                if 'lesson_reminders' not in columns:
                    await db.execute('ALTER TABLE users ADD COLUMN lesson_reminders INTEGER DEFAULT 0')
                    logging.info("Добавлена колонка 'lesson_reminders' в таблицу 'users'.")
                # Удаляем старую колонку, если она была
                if 'lesson_notify_mode' in columns:
                    # SQLite не поддерживает DROP COLUMN напрямую в старых версиях,
                    # но в современных это работает. Для простоты оставляем так.
                    # В реальном продакшене миграция была бы сложнее.
                    logging.warning("Обнаружена устаревшая колонка 'lesson_notify_mode'. Она не будет удалена, но больше не используется.")

            await db.commit()

    async def set_user_group(self, user_id: int, group: str):
        """Сохраняет или обновляет группу для пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT INTO users (user_id, group_name) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET group_name = excluded.group_name
            ''', (user_id, group))
            await db.commit()
    
    async def get_user_group(self, user_id: int) -> Optional[str]:
        """Возвращает сохраненную группу пользователя или None, если не найдена."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('SELECT group_name FROM users WHERE user_id = ?', (user_id,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def update_setting(self, user_id: int, setting_name: str, enabled: bool):
        """Универсальный метод для обновления любой булевой настройки."""
        valid_settings = ['evening_notify', 'morning_summary', 'lesson_reminders']
        if setting_name not in valid_settings:
            logging.error(f"Попытка обновить невалидную настройку: {setting_name}")
            raise ValueError(f"Invalid setting name: {setting_name}")
            
        async with aiosqlite.connect(self.db_path) as db:
            # Сначала убедимся, что пользователь существует в таблице.
            # Если его нет (например, старый пользователь до добавления настроек), 
            # создаем запись с группой "unknown".
            await db.execute(
                'INSERT OR IGNORE INTO users (user_id, group_name) VALUES (?, ?)', 
                (user_id, 'unknown')
            )
            
            # Теперь обновляем настройку
            await db.execute(
                f'UPDATE users SET {setting_name} = ? WHERE user_id = ?', 
                (int(enabled), user_id)
            )
            await db.commit()
            logging.info(f"БД обновлена для user_id={user_id}: {setting_name} = {enabled}")


    async def get_user_settings(self, user_id: int) -> Optional[dict]:
        """Возвращает словарь с настройками пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT evening_notify, morning_summary, lesson_reminders FROM users WHERE user_id = ?', (user_id,))
            row = await cursor.fetchone()
            
            # Если пользователь новый и для него нет записи, создаем ее и возвращаем дефолтные значения
            if not row:
                logging.warning(f"Настройки для user_id={user_id} не найдены. Создается новая запись.")
                # Создаем запись с дефолтными значениями
                await db.execute(
                    'INSERT OR IGNORE INTO users (user_id, group_name, evening_notify, morning_summary, lesson_reminders) VALUES (?, ?, 0, 0, 0)',
                    (user_id, "unknown")
                )
                await db.commit()
                return {
                    "evening_notify": False,
                    "morning_summary": False,
                    "lesson_reminders": False,
                }
            return dict(row)

    async def get_users_for_evening_notify(self) -> List[Tuple[int, str]]:
        """Возвращает пользователей для вечерней рассылки."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('SELECT user_id, group_name FROM users WHERE evening_notify = 1')
            return await cursor.fetchall()
            
    async def get_users_for_morning_summary(self) -> List[Tuple[int, str]]:
        """Возвращает пользователей для утренней сводки."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('SELECT user_id, group_name FROM users WHERE morning_summary = 1')
            return await cursor.fetchall()

    async def get_users_for_lesson_reminders(self) -> List[Tuple[int, str]]:
        """Возвращает пользователей для напоминаний о парах."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('SELECT user_id, group_name FROM users WHERE lesson_reminders = 1')
            return await cursor.fetchall()