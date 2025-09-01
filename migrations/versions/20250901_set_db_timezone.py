"""Set database timezone to Europe/Moscow

Revision ID: set_db_timezone_20250901
Revises: 7f8e9a12b3c4
Create Date: 2025-09-01 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'set_db_timezone_20250901'
down_revision = '7f8e9a12b3c4'
branch_labels = None
depends_on = None


def upgrade():
    # Устанавливаем таймзону БД на уровне БД (персистентно для новых подключений)
    op.execute("ALTER DATABASE \"%s\" SET timezone TO 'Europe/Moscow';" % op.get_bind().engine.url.database)
    # Логирование таймзоны в журнале тоже в Moscow
    op.execute("ALTER DATABASE \"%s\" SET log_timezone TO 'Europe/Moscow';" % op.get_bind().engine.url.database)


def downgrade():
    # Возвращаем к настройкам по умолчанию (UTC) — опционально
    op.execute("ALTER DATABASE \"%s\" RESET timezone;" % op.get_bind().engine.url.database)
    op.execute("ALTER DATABASE \"%s\" RESET log_timezone;" % op.get_bind().engine.url.database)


