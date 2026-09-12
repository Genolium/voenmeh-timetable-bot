from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from core.db import Base, User
from core.user_data import UserDataManager

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def analytics_manager():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    manager = UserDataManager(TEST_DB_URL, redis_url=None)
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.set.return_value = True
    manager._redis_client = mock_redis
    manager.engine = engine
    manager.async_session_maker.configure(bind=engine)

    try:
        yield manager
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


class TestUserDataAnalytics:
    @pytest.mark.asyncio
    async def test_blocked_users_tracking(self, analytics_manager):
        """Тест установки и подсчета заблокированных пользователей."""
        # Регистрируем пользователей
        await analytics_manager.register_user(101, "user101")
        await analytics_manager.register_user(102, "user102")

        # Изначально заблокированных нет
        assert await analytics_manager.get_blocked_users_count() == 0

        # Блокируем одного пользователя
        await analytics_manager.set_user_blocked(101, True)
        assert await analytics_manager.get_blocked_users_count() == 1
        assert await analytics_manager.get_blocked_users_count(days=1) == 1

        # Пользователь вернулся и снова зарегистрировался
        await analytics_manager.register_user(101, "user101")
        assert await analytics_manager.get_blocked_users_count() == 0

    @pytest.mark.asyncio
    async def test_users_without_group_funnel(self, analytics_manager):
        """Тест подсчета пользователей без группы (воронка онбординга)."""
        await analytics_manager.register_user(201, "no_group_user")
        await analytics_manager.register_user(202, "with_group_user")
        await analytics_manager.set_user_group(202, "О735Б")

        # Один пользователь без группы, один с группой
        assert await analytics_manager.get_users_without_group_count() == 1

    @pytest.mark.asyncio
    async def test_daily_dynamics(self, analytics_manager):
        """Тест расчета динамики и дельт к предыдущему дню."""
        # Снепшот вчера отсутствует - проверяем расчет по умолчанию
        dynamics = await analytics_manager.get_daily_dynamics(
            total_users=1000,
            dau=150,
            subscribed_total=800,
            new_users_day=12,
        )

        assert dynamics["delta_users_str"] == "+12"
        assert "delta_dau_pct_str" in dynamics
        assert "delta_subs_str" in dynamics

    @pytest.mark.asyncio
    async def test_get_activity_history(self, analytics_manager):
        """Тест получения истории активности для графиков."""
        await analytics_manager.register_user(301, "user301")
        dates, new_users, active_users = await analytics_manager.get_activity_history(days=7)

        assert len(dates) == 7
        assert len(new_users) == 7
        assert len(active_users) == 7
        assert sum(new_users) >= 1
