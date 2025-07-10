import aiosqlite
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

class UserDataManager:
    """
    Класс для управления данными пользователей в базе данных SQLite.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _execute(self, query: str, params: tuple = (), commit: bool = False, fetchone: bool = False, fetchall: bool = False):
        """Универсальный и надежный метод для выполнения SQL-запросов."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(query, params) as cursor:
                    if fetchone:
                        return await cursor.fetchone()
                    if fetchall:
                        return await cursor.fetchall()
                if commit:
                    await db.commit()
        except aiosqlite.Error as e:
            logging.error(f"Database error: {e}. Query: {query}, Params: {params}")
            return None

    async def init_db(self):
        """Инициализирует таблицу в базе данных, если она не существует."""
        query = """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            "group" TEXT,
            registration_date TIMESTAMP,
            last_active_date TIMESTAMP,
            evening_notify INTEGER DEFAULT 1,
            morning_summary INTEGER DEFAULT 1,
            lesson_reminders INTEGER DEFAULT 1
        );
        """
        await self._execute(query, commit=True)
        logging.info("Таблица 'users' успешно создана или уже существует.")

    async def register_user(self, user_id: int, username: Optional[str]):
        """Регистрирует нового пользователя или обновляет дату последней активности."""
        now = datetime.utcnow()
        # Сначала пробуем обновить, если пользователь уже существует
        update_query = "UPDATE users SET last_active_date = ? WHERE user_id = ?"
        res = await self._execute(update_query, (now, user_id), commit=True)
        
        # Если ничего не обновилось (пользователя нет), вставляем новую запись
        # Используем INSERT OR IGNORE для атомарности
        insert_query = "INSERT OR IGNORE INTO users (user_id, username, registration_date, last_active_date) VALUES (?, ?, ?, ?)"
        await self._execute(insert_query, (user_id, username, now, now), commit=True)

    async def set_user_group(self, user_id: int, group: str):
        query = 'UPDATE users SET "group" = ? WHERE user_id = ?'
        await self._execute(query, (group, user_id), commit=True)

    async def get_user_group(self, user_id: int) -> Optional[str]:
        query = 'SELECT "group" FROM users WHERE user_id = ?'
        result = await self._execute(query, (user_id,), fetchone=True)
        return result['group'] if result else None

    async def get_user_settings(self, user_id: int) -> Dict[str, bool]:
        query = "SELECT evening_notify, morning_summary, lesson_reminders FROM users WHERE user_id = ?"
        settings_row = await self._execute(query, (user_id,), fetchone=True)
        if settings_row:
            settings_dict = dict(settings_row)
            return {key: bool(value) for key, value in settings_dict.items()}
        return {"evening_notify": False, "morning_summary": False, "lesson_reminders": False}

    async def update_setting(self, user_id: int, setting_name: str, status: bool):
        if setting_name not in ["evening_notify", "morning_summary", "lesson_reminders"]:
            logging.warning(f"Attempt to update invalid setting: {setting_name}")
            return
        query = f'UPDATE users SET "{setting_name}" = ? WHERE user_id = ?'
        await self._execute(query, (int(status), user_id), commit=True)

    # --- Методы для статистики ---
    async def get_total_users_count(self) -> int:
        query = "SELECT COUNT(user_id) FROM users"
        result = await self._execute(query, fetchone=True)
        return result[0] if result else 0

    async def get_new_users_count(self, days: int = 1) -> int:
        start_date = datetime.utcnow() - timedelta(days=days)
        query = "SELECT COUNT(user_id) FROM users WHERE registration_date >= ?"
        result = await self._execute(query, (start_date,), fetchone=True)
        return result[0] if result else 0

    async def get_subscribed_users_count(self) -> int:
        """Считает пользователей, у которых включена хотя бы одна подписка на рассылку."""
        query = "SELECT COUNT(user_id) FROM users WHERE evening_notify = 1 OR morning_summary = 1 OR lesson_reminders = 1"
        result = await self._execute(query, fetchone=True)
        return result[0] if result else 0

    async def get_active_users_by_period(self, days: int) -> int:
        """Считает количество уникальных активных пользователей за период (DAU, WAU, MAU)."""
        start_date = datetime.utcnow() - timedelta(days=days)
        # last_active_date обновляется при любом действии пользователя
        query = "SELECT COUNT(user_id) FROM users WHERE last_active_date >= ?"
        result = await self._execute(query, (start_date,), fetchone=True)
        return result[0] if result else 0

    async def get_top_groups(self, limit: int = 5) -> List[Tuple[str, int]]:
        query = """
        SELECT "group", COUNT(user_id) as user_count
        FROM users WHERE "group" IS NOT NULL
        GROUP BY "group" ORDER BY user_count DESC LIMIT ?
        """
        result = await self._execute(query, (limit,), fetchall=True)
        return [(row['group'], row['user_count']) for row in result] if result else []

    async def get_all_user_ids(self) -> List[int]:
        query = "SELECT user_id FROM users"
        result = await self._execute(query, fetchall=True)
        return [row['user_id'] for row in result] if result else []

    # --- Методы для рассылок ---
    async def get_users_for_evening_notify(self) -> List[Tuple[int, str]]:
        query = 'SELECT user_id, "group" FROM users WHERE evening_notify = 1 AND "group" IS NOT NULL;'
        rows = await self._execute(query, fetchall=True)
        return [(row['user_id'], row['group']) for row in rows] if rows else []

    async def get_users_for_morning_summary(self) -> List[Tuple[int, str]]:
        query = 'SELECT user_id, "group" FROM users WHERE morning_summary = 1 AND "group" IS NOT NULL;'
        rows = await self._execute(query, fetchall=True)
        return [(row['user_id'], row['group']) for row in rows] if rows else []

    async def get_users_for_lesson_reminders(self) -> List[Tuple[int, str]]:
        query = 'SELECT user_id, "group" FROM users WHERE lesson_reminders = 1 AND "group" IS NOT NULL;'
        rows = await self._execute(query, fetchall=True)
        return [(row['user_id'], row['group']) for row in rows] if rows else []