import aiosqlite
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

class UserDataManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _execute(self, query: str, params: tuple = (), commit: bool = False, fetchone: bool = False, fetchall: bool = False):
        """Универсальный метод для выполнения SQL-запросов."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(query, params)
                if commit:
                    await db.commit()
                if fetchone:
                    return await cursor.fetchone()
                if fetchall:
                    return await cursor.fetchall()
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
            evening_notify BOOLEAN DEFAULT TRUE,
            morning_summary BOOLEAN DEFAULT TRUE,
            lesson_reminders BOOLEAN DEFAULT TRUE
        );
        """
        await self._execute(query, commit=True)
        logging.info("Таблица 'users' успешно создана или уже существует.")

    async def register_user(self, user_id: int, username: Optional[str]):
        """
        Регистрирует нового пользователя или обновляет дату последней активности.
        Если пользователь с таким user_id уже существует, ничего не делает (благодаря OR IGNORE).
        """
        query = """
        INSERT OR IGNORE INTO users (user_id, username, registration_date, last_active_date)
        VALUES (?, ?, ?, ?)
        """
        now = datetime.utcnow()
        await self._execute(query, (user_id, username, now, now), commit=True)

    async def set_user_group(self, user_id: int, group: str):
        """Устанавливает или обновляет учебную группу пользователя."""
        query = 'UPDATE users SET "group" = ? WHERE user_id = ?'
        await self._execute(query, (group, user_id), commit=True)

    async def get_user_group(self, user_id: int) -> Optional[str]:
        """Получает учебную группу пользователя."""
        query = 'SELECT "group" FROM users WHERE user_id = ?'
        result = await self._execute(query, (user_id,), fetchone=True)
        return result[0] if result else None

    async def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Получает настройки уведомлений для пользователя."""
        query = "SELECT evening_notify, morning_summary, lesson_reminders FROM users WHERE user_id = ?"
        result = await self._execute(query, (user_id,), fetchone=True)
        if result:
            return {
                "evening_notify": result[0],
                "morning_summary": result[1],
                "lesson_reminders": result[2],
            }
        return {}

    async def update_setting(self, user_id: int, setting_name: str, status: bool):
        """Обновляет конкретную настройку уведомлений для пользователя."""
        if setting_name not in ["evening_notify", "morning_summary", "lesson_reminders"]:
            logging.warning(f"Attempt to update invalid setting: {setting_name}")
            return
        query = f'UPDATE users SET "{setting_name}" = ? WHERE user_id = ?'
        await self._execute(query, (status, user_id), commit=True)

    async def get_total_users_count(self) -> int:
        """Возвращает общее количество пользователей в БД."""
        query = "SELECT COUNT(user_id) FROM users"
        result = await self._execute(query, fetchone=True)
        return result[0] if result else 0

    async def get_new_users_count(self, days: int = 1) -> int:
        """Возвращает количество новых пользователей за последние N дней."""
        start_date = datetime.utcnow() - timedelta(days=days)
        query = "SELECT COUNT(user_id) FROM users WHERE registration_date >= ?"
        result = await self._execute(query, (start_date,), fetchone=True)
        return result[0] if result else 0

    async def get_active_users_count(self) -> int:
        """Возвращает количество пользователей с хотя бы одной включенной подпиской."""
        query = "SELECT COUNT(user_id) FROM users WHERE evening_notify = 1 OR morning_summary = 1 OR lesson_reminders = 1"
        result = await self._execute(query, fetchone=True)
        return result[0] if result else 0

    async def get_top_groups(self, limit: int = 5) -> List[Tuple[str, int]]:
        """Возвращает топ N самых популярных групп."""
        query = """
        SELECT "group", COUNT(user_id) as user_count
        FROM users
        WHERE "group" IS NOT NULL
        GROUP BY "group"
        ORDER BY user_count DESC
        LIMIT ?
        """
        result = await self._execute(query, (limit,), fetchall=True)
        return result if result else []

    async def get_all_user_ids(self) -> List[int]:
        """Возвращает список ID всех пользователей для рассылки."""
        query = "SELECT user_id FROM users"
        result = await self._execute(query, fetchall=True)
        return [row[0] for row in result] if result else []
