import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

load_dotenv()

from core.db.models import Base

config = context.config

# Безопасная настройка логирования Alembic: пропускаем, если в alembic.ini нет секций логирования
try:
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
except Exception:
    # Нет секций loggers/handlers/formatters — логирование Alembic пропускаем
    pass

"""Инициализация URL подключения для Alembic.

Порядок источников:
1) DATABASE_URL из переменных окружения (CI передаёт его шагу миграций)
2) settings.DATABASE_URL из core.config (если доступны все обязательные переменные)
3) Конструктор из POSTGRES_* переменных окружения (с безопасными значениями по умолчанию)
"""


def _to_sync_driver(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://")
    return url


# 1) Пробуем взять из окружения (приоритетно для CI)
db_url_env = os.getenv("DATABASE_URL")
if db_url_env:
    config.set_main_option("sqlalchemy.url", _to_sync_driver(db_url_env))
else:
    # 2) Пробуем загрузить из настроек приложения
    try:
        from core.config import settings

        config.set_main_option("sqlalchemy.url", _to_sync_driver(settings.DATABASE_URL))
    except Exception:
        # 3) Собираем из POSTGRES_* с дефолтами, избегая 'None' в URL
        DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
        DB_PORT = os.getenv("POSTGRES_PORT") or "5432"
        DB_NAME = os.getenv("POSTGRES_DB", "test_db")
        DB_USER = os.getenv("POSTGRES_USER", "test")
        DB_PASS = os.getenv("POSTGRES_PASSWORD", "test")

        sync_db_url = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        config.set_main_option("sqlalchemy.url", sync_db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
