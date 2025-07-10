import asyncio
import aiosqlite
import asyncpg
import os
from dotenv import load_dotenv
from datetime import datetime

async def main():
    load_dotenv()
    
    sqlite_path = 'data/users.db'
    if not os.path.exists(sqlite_path):
        print(f"Файл {sqlite_path} не найден. Миграция не требуется.")
        return

    print("--- Начало миграции данных из SQLite в PostgreSQL ---")

    # 1. Чтение данных из SQLite
    try:
        sqlite_conn = await aiosqlite.connect(sqlite_path)
        sqlite_conn.row_factory = aiosqlite.Row
        cursor = await sqlite_conn.cursor()
        await cursor.execute("SELECT * FROM users")
        users_data = await cursor.fetchall()
        await sqlite_conn.close()
    except Exception as e:
        print(f"Ошибка чтения из SQLite: {e}")
        return

    if not users_data:
        print("Старая база данных пуста. Миграция не требуется.")
        os.rename(sqlite_path, f"{sqlite_path}.migrated_empty_{datetime.now().strftime('%Y%m%d')}")
        print(f"Старый пустой файл переименован.")
        return

    print(f"Найдено {len(users_data)} пользователей для миграции.")

    # 2. Подключение к PostgreSQL
    pg_url_from_env = os.getenv('DATABASE_URL')
    if not pg_url_from_env:
        print("DATABASE_URL не найден в .env. Невозможно подключиться к PostgreSQL.")
        return
    
    pg_url_for_asyncpg = pg_url_from_env.replace("postgresql+asyncpg", "postgresql")
    
    pg_conn = None
    try:
        pg_conn = await asyncpg.connect(dsn=pg_url_for_asyncpg)
        print("Успешное подключение к PostgreSQL.")
    except Exception as e:
        print(f"Ошибка подключения к PostgreSQL: {e}")
        return

    # 3. Запись данных в PostgreSQL
    inserted_count = 0
    try:
        # Начинаем транзакцию
        async with pg_conn.transaction():
            await pg_conn.execute("TRUNCATE TABLE users RESTART IDENTITY CASCADE;")
            print("Таблица 'users' в PostgreSQL очищена.")

            # Готовим запрос на вставку
            insert_query = """
                INSERT INTO users (
                    user_id, username, "group", registration_date, last_active_date,
                    evening_notify, morning_summary, lesson_reminders
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """
            
            # Вставляем данные по одной строке
            for row in users_data:
                user_dict = dict(row)
                
                # Явное преобразование типов для надежности
                registration_date = datetime.fromisoformat(user_dict.get("registration_date")) if user_dict.get("registration_date") else None
                last_active_date = datetime.fromisoformat(user_dict.get("last_active_date")) if user_dict.get("last_active_date") else None

                await pg_conn.execute(
                    insert_query,
                    user_dict.get('user_id'),
                    user_dict.get('username'),
                    user_dict.get('group'),
                    registration_date,
                    last_active_date,
                    bool(user_dict.get('evening_notify', True)),
                    bool(user_dict.get('morning_summary', True)),
                    bool(user_dict.get('lesson_reminders', True))
                )
                inserted_count += 1
        
        print(f"Успешно мигрировано {inserted_count} записей.")

    except Exception as e:
        print(f"Ошибка при записи данных в PostgreSQL на строке {inserted_count + 1}: {e}")
    finally:
        if pg_conn:
            await pg_conn.close()

    # 4. Переименовываем старый файл, если все прошло успешно
    if inserted_count == len(users_data):
        try:
            os.rename(sqlite_path, f"{sqlite_path}.migrated_to_postgres_{datetime.now().strftime('%Y%m%d_%H%M')}")
            print(f"Старый файл переименован.")
        except OSError as e:
            print(f"Не удалось переименовать старый файл БД: {e}")

    print("--- Миграция данных завершена ---")

if __name__ == '__main__':
    asyncio.run(main())