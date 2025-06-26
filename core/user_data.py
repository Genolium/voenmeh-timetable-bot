import aiosqlite
import logging
from datetime import datetime, timedelta

from .config import DATABASE_FILENAME, MOSCOW_TZ

class UserDataManager:
    def __init__(self, db_path=DATABASE_FILENAME):
        self.db_path = db_path

    async def _execute_query(self, query: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            await db.commit()
            if fetch_one:
                return await cursor.fetchone()
            if fetch_all:
                return await cursor.fetchall()

    async def _init_db(self):
        await self._execute_query("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                group_name TEXT,
                evening_notify BOOLEAN DEFAULT 1,
                morning_summary BOOLEAN DEFAULT 1,
                lesson_reminders BOOLEAN DEFAULT 1
            )
        """)
        logging.info("Таблица 'users' успешно создана или уже существует.")
        
        try:
            await self._execute_query("ALTER TABLE users ADD COLUMN created_at TIMESTAMP;")
            logging.info("Колонка 'created_at' успешно добавлена в таблицу 'users'.")
        except aiosqlite.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass 
            else:
                logging.error(f"Не удалось добавить колонку 'created_at': {e}")

    async def add_user_if_not_exists(self, user_id: int):
        now = datetime.now(MOSCOW_TZ)
        await self._execute_query(
            "INSERT OR IGNORE INTO users (user_id, created_at) VALUES (?, ?)",
            (user_id, now)
        )

    async def set_user_group(self, user_id: int, group: str):
        await self.add_user_if_not_exists(user_id)
        await self._execute_query(
            "UPDATE users SET group_name = ? WHERE user_id = ?", (group.upper(), user_id)
        )

    async def get_user_group(self, user_id: int) -> str | None:
        row = await self._execute_query("SELECT group_name FROM users WHERE user_id = ?", (user_id,), fetch_one=True)
        return row[0] if row and row[0] else None

    async def get_user_settings(self, user_id: int) -> dict:
        await self.add_user_if_not_exists(user_id)
        row = await self._execute_query(
            "SELECT evening_notify, morning_summary, lesson_reminders FROM users WHERE user_id = ?",
            (user_id,),
            fetch_one=True
        )
        if row:
            return {"evening_notify": bool(row[0]), "morning_summary": bool(row[1]), "lesson_reminders": bool(row[2])}
        return {}

    async def update_setting(self, user_id: int, setting_name: str, value: bool):
        await self.add_user_if_not_exists(user_id)
        if setting_name in ["evening_notify", "morning_summary", "lesson_reminders"]:
            await self._execute_query(f"UPDATE users SET {setting_name} = ? WHERE user_id = ?", (value, user_id))
    
    # --- Методы для рассылок ---

    async def get_users_for_evening_notify(self) -> list:
        result = await self._execute_query("SELECT user_id, group_name FROM users WHERE evening_notify = 1 AND group_name IS NOT NULL", fetch_all=True)
        return result or []

    async def get_users_for_morning_summary(self) -> list:
        result = await self._execute_query("SELECT user_id, group_name FROM users WHERE morning_summary = 1 AND group_name IS NOT NULL", fetch_all=True)
        return result or []

    async def get_users_for_lesson_reminders(self) -> list:
        result = await self._execute_query("SELECT user_id, group_name FROM users WHERE lesson_reminders = 1 AND group_name IS NOT NULL", fetch_all=True)
        return result or []
        
    async def get_all_user_ids(self) -> list[int]:
        rows = await self._execute_query("SELECT user_id FROM users", fetch_all=True)
        return [row[0] for row in rows] if rows else []

    # --- Методы для статистики ---
    
    async def get_total_users_count(self) -> int:
        result = await self._execute_query("SELECT COUNT(*) FROM users", fetch_one=True)
        return result[0] if result else 0

    async def get_new_users_count(self, days: int) -> int:
        target_date = datetime.now(MOSCOW_TZ) - timedelta(days=days)
        result = await self._execute_query("SELECT COUNT(*) FROM users WHERE created_at >= ?", (target_date,), fetch_one=True)
        return result[0] if result else 0
        
    async def get_active_users_count(self) -> int:
        query = "SELECT COUNT(*) FROM users WHERE evening_notify = 1 OR morning_summary = 1 OR lesson_reminders = 1"
        result = await self._execute_query(query, fetch_one=True)
        return result[0] if result else 0

    async def get_top_groups(self, limit: int = 5) -> list[tuple[str, int]]:
        query = "SELECT group_name, COUNT(*) as count FROM users WHERE group_name IS NOT NULL AND group_name != '' GROUP BY group_name ORDER BY count DESC LIMIT ?"
        return await self._execute_query(query, (limit,), fetch_all=True) or []